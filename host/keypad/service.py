"""Starts Keypad at login, per user: one launchd LaunchAgent on macOS, one Task
Scheduler task (restarted on failure) on Windows. The tray hosts the agent, so
there is a single process to start. No administrator rights needed."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import config, osutil
from .dirs import env_without_bundle, gui_path
from .trayos import TRAY_MUTEX

LABEL = "com.jameelhamdan.keypad"
TASK_NAME = "Keypad"


def _run(*args: str) -> subprocess.CompletedProcess:
    return osutil.run(list(args))


def spawn(binary: str, *args: str) -> None:
    """Starts `binary args...` detached from this process."""
    exe = gui_path(binary)
    if sys.platform == "win32":
        # CREATE_BREAKAWAY_FROM_JOB: if this process (e.g. the tray) is itself
        # in a job object with kill-on-close semantics and dies unexpectedly,
        # the child must not be taken down with it.
        base = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | osutil.NO_WINDOW
        try:
            subprocess.Popen([exe, *args], creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB,
                             close_fds=True, env=env_without_bundle())
        except OSError:  # the enclosing job (if any) forbids breakaway: fall back to inheriting it
            subprocess.Popen([exe, *args], creationflags=base, close_fds=True, env=env_without_bundle())
    else:
        subprocess.Popen([exe, *args], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env_without_bundle())


# ---- macOS ----------------------------------------------------------------------


def _plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _plist(label: str, binary: str, arg: str) -> str:
    log = escape(str(config.log_dir() / f"{arg.split()[0]}.launchd.log"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>{label}</string>
	<key>ProgramArguments</key><array><string>{escape(binary)}</string>{"".join(f"<string>{a}</string>" for a in arg.split())}</array>
	<key>RunAtLoad</key><true/>
	<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
	<key>ThrottleInterval</key><integer>5</integer>
	<key>ProcessType</key><string>Interactive</string>
	<key>LimitLoadToSessionType</key><string>Aqua</string>
	<key>StandardOutPath</key><string>{log}</string>
	<key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _mac_write(binary: str) -> None:
    p = _plist_path(LABEL)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_plist(LABEL, binary, "tray --quiet"))


def _disabled(label: str) -> bool:
    """launchd's persistent "disabled" override for label."""
    for line in _run("launchctl", "print-disabled", _domain()).stdout.splitlines():
        if f'"{label}"' in line:
            return "disabled" in line or "true" in line
    return False


# ---- Windows ------------------------------------------------------------------------


def _task_xml(binary: str, user: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Keypad (hardware keypad for Claude Code): the tray and its agent</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <!-- like launchd's KeepAlive on macOS: a crash or kill (non-zero exit) is restarted after a minute; Quit is not -->
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <Hidden>true</Hidden>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(binary)}</Command><Arguments>tray --quiet</Arguments></Exec></Actions>
</Task>
"""


def _win_register(binary: str) -> None:
    user = os.environ.get("USERNAME", "")
    if dom := os.environ.get("USERDOMAIN"):
        user = dom + "\\" + user
    config.data_dir().mkdir(parents=True, exist_ok=True)
    fd, xml = tempfile.mkstemp(suffix=".xml", dir=config.data_dir())
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(_task_xml(binary, user).encode("utf-16"))  # with BOM, as the declaration says
        r = _run("schtasks", "/Create", "/TN", TASK_NAME, "/XML", xml, "/F")
        if r.returncode != 0:
            raise RuntimeError(f"schtasks: {r.stdout}{r.stderr}".strip())
    finally:
        os.unlink(xml)


def _win_unregister() -> None:
    _run("schtasks", "/Delete", "/TN", TASK_NAME, "/F")


def tray_running() -> bool:
    """Whether a tray holds its single-instance lock (Windows mutex)."""
    if sys.platform != "win32":
        return False
    import ctypes

    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, TRAY_MUTEX)  # SYNCHRONIZE
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def _win_stop_trays() -> None:
    # Only the windowless build: keypad.exe also runs Claude Code's hook.
    _run("taskkill", "/F", "/IM", "keypadw.exe", "/FI", f"PID ne {os.getpid()}")


# ---- API ---------------------------------------------------------------------------------


def install(binary: str) -> None:
    """Registers Keypad to start at login, and starts it now."""
    binary = gui_path(binary)
    if sys.platform == "darwin":
        config.log_dir().mkdir(parents=True, exist_ok=True)
        _run("launchctl", "enable", f"{_domain()}/{LABEL}")
        _run("launchctl", "bootout", f"{_domain()}/{LABEL}")
        _mac_write(binary)
        r = _run("launchctl", "bootstrap", _domain(), str(_plist_path(LABEL)))
        if r.returncode != 0:
            raise RuntimeError(f"launchctl bootstrap {LABEL}: {r.stdout}{r.stderr}".strip())
    elif sys.platform == "win32":
        _win_register(binary)
        if not tray_running() and not start_registered():  # a second tray would only say "already running"
            spawn(binary, "tray")
    else:
        raise RuntimeError("start at login is supported on macOS and Windows")


def uninstall() -> None:
    """Removes the login item and stops the tray (stop the agent through its API first)."""
    if sys.platform == "darwin":
        _run("launchctl", "bootout", f"{_domain()}/{LABEL}")
        _run("launchctl", "enable", f"{_domain()}/{LABEL}")  # drop any override
        _plist_path(LABEL).unlink(missing_ok=True)
    elif sys.platform == "win32":
        _win_unregister()
        _win_stop_trays()


def set_login_enabled(binary: str, on: bool) -> None:
    """Turns starting at login on or off without stopping or restarting
    anything that runs now (the tray menu uses it)."""
    binary = gui_path(binary)
    if sys.platform == "darwin":
        if on:
            _mac_write(binary)  # launchd loads it at the next login
            _run("launchctl", "enable", f"{_domain()}/{LABEL}")
        elif (r := _run("launchctl", "disable", f"{_domain()}/{LABEL}")).returncode != 0:
            raise RuntimeError(f"launchctl disable {LABEL}: {r.stderr}".strip())
    elif sys.platform == "win32":
        _win_register(binary) if on else _win_unregister()
    else:
        raise RuntimeError("start at login is supported on macOS and Windows")


def registered() -> bool:
    """Whether the login item was ever set up, even if turned off since
    (first-launch setup must not undo the user's choice)."""
    if sys.platform == "darwin":
        return _plist_path(LABEL).exists()
    if sys.platform == "win32":
        return _run("schtasks", "/Query", "/TN", TASK_NAME).returncode == 0
    return False


def installed() -> bool:
    """Whether Keypad starts at login."""
    if sys.platform == "darwin":
        return _plist_path(LABEL).exists() and not _disabled(LABEL)
    return registered()


def start_registered() -> bool:
    if sys.platform == "darwin":
        return _run("launchctl", "kickstart", f"{_domain()}/{LABEL}").returncode == 0
    if sys.platform == "win32":
        return _run("schtasks", "/Run", "/TN", TASK_NAME).returncode == 0
    return False
