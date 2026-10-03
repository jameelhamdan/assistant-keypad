"""The few OS facilities the agent needs: last-input time (PC hand-back),
dark mode (keypad "system" theme), the current Wi-Fi SSID (pairing dialog)
and opening folders and files."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def run(args: list[str], *, timeout: float | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """subprocess.run with output captured as text and no console flash on Windows."""
    return subprocess.run(args, capture_output=True, text=True, creationflags=NO_WINDOW, timeout=timeout, env=env)


def _out(args: list[str]) -> str:
    try:
        return run(args, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


_cg = None


def idle() -> tuple[float, bool]:
    """Seconds since the last input of this user session (keyboard only on
    macOS: moving the mouse near the keypad should not count as "at the PC";
    keyboard and mouse on Windows). ok is False when unknown."""
    global _cg
    if sys.platform == "darwin":
        try:
            if _cg is None:
                _cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
                _cg.CGEventSourceSecondsSinceLastEventType.restype = ctypes.c_double
                _cg.CGEventSourceSecondsSinceLastEventType.argtypes = [ctypes.c_int32, ctypes.c_uint32]
            # kCGEventSourceStateCombinedSessionState = 0, kCGEventKeyDown = 10
            s = _cg.CGEventSourceSecondsSinceLastEventType(0, 10)
            return (s, True) if s >= 0 else (0.0, False)
        except OSError:
            return 0.0, False
    if sys.platform == "win32":
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return 0.0, False
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((now - lii.dwTime) & 0xFFFFFFFF) / 1000.0, True
    return 0.0, False


def idle_any() -> tuple[float, bool]:
    """Seconds since any input, mouse included: whether you are at the PC at
    all (idle() counts only the keyboard on macOS, for the hand-back)."""
    if sys.platform == "darwin":
        try:
            idle()  # loads CoreGraphics
            s = _cg.CGEventSourceSecondsSinceLastEventType(0, 0xFFFFFFFF)  # kCGAnyInputEventType
            return (s, True) if s >= 0 else (0.0, False)
        except (OSError, AttributeError):
            return 0.0, False
    return idle()  # Windows: keyboard and mouse already


def dark_mode() -> bool:
    if sys.platform == "darwin":
        return "Dark" in _out(["defaults", "read", "-g", "AppleInterfaceStyle"])
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        except OSError:
            return False
    return True


def ssid() -> str:
    """The current Wi-Fi network name, or ""."""
    if sys.platform == "darwin":
        for dev in ("en0", "en1"):
            for line in _out(["ipconfig", "getsummary", dev]).splitlines():
                k, sep, v = line.strip().partition(" : ")
                if sep and k == "SSID" and v != "<redacted>":
                    return v
        return ""
    if sys.platform == "win32":
        for line in _out(["netsh", "wlan", "show", "interfaces"]).splitlines():
            k, sep, v = line.partition(":")
            if sep and k.strip() == "SSID":
                return v.strip()
    return ""


def open_path(target: str) -> None:
    """Opens a folder, file or URL with the default handler."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", target])
    elif sys.platform == "win32":
        os.startfile(target)  # noqa: S606 - a local path we built
    else:
        subprocess.Popen(["xdg-open", target])


def open_text(path: str) -> None:
    """Opens a file in a plain-text editor."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-t", path])
    elif sys.platform == "win32":
        subprocess.Popen(["notepad.exe", path])
    else:
        subprocess.Popen(["xdg-open", path])
