"""Starts the agent and the tray at login, per user: launchd LaunchAgents on
macOS, a Task Scheduler task (agent, restarted on failure) plus a Run key
(tray) on Windows. No administrator rights needed."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import config, osutil
from .paths import env_without_bundle, gui_path

AGENT_LABEL = "com.jameelhamdan.keypad.agent"
TRAY_LABEL = "com.jameelhamdan.keypad.tray"
TASK_NAME = "Keypad Agent"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Keypad"
TRAY_MUTEX = "Local\\KeypadTray"


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
    log = escape(str(config.log_dir() / f"{arg}.launchd.log"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>{label}</string>
	<key>ProgramArguments</key><array><string>{escape(binary)}</string><string>{arg}</string></array>
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


def _jobs() -> dict[str, str]:
    return {AGENT_LABEL: "agent", TRAY_LABEL: "tray"}


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
  <RegistrationInfo><Description>Keypad background agent (hardware keypad for Claude Code)</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
    <Hidden>true</Hidden>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(binary)}</Command><Arguments>agent</Arguments></Exec></Actions>
</Task>
"""


def _win_register(binary: str) -> None:
    import winreg

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
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{binary}" tray')


def _win_unregister() -> None:
    import winreg

    _run("schtasks", "/Delete", "/TN", TASK_NAME, "/F")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, RUN_NAME)
    except OSError:
        pass


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
    # Only the windowless build: keypad.exe also runs Claude Code's hook and MCP shims.
    _run("taskkill", "/F", "/IM", "keypadw.exe", "/FI", f"PID ne {os.getpid()}")


# ---- API ---------------------------------------------------------------------------------


def install(binary: str) -> None:
    """Registers the agent and tray to start at login, and starts them now."""
    binary = gui_path(binary)
    if sys.platform == "darwin":
        config.log_dir().mkdir(parents=True, exist_ok=True)
        for label, arg in _jobs().items():
            _run("launchctl", "enable", f"{_domain()}/{label}")
            p = _plist_path(label)
            p.parent.mkdir(parents=True, exist_ok=True)
            _run("launchctl", "bootout", f"{_domain()}/{label}")
            p.write_text(_plist(label, binary, arg))
            r = _run("launchctl", "bootstrap", _domain(), str(p))
            if r.returncode != 0:
                raise RuntimeError(f"launchctl bootstrap {label}: {r.stdout}{r.stderr}".strip())
    elif sys.platform == "win32":
        _win_register(binary)
        start_registered()
        if not tray_running():  # a second tray would only say "already running"
            spawn(binary, "tray")
    else:
        raise RuntimeError("start at login is supported on macOS and Windows")


def uninstall() -> None:
    """Removes the login items and stops the tray (stop the agent through its API first)."""
    if sys.platform == "darwin":
        for label in (TRAY_LABEL, AGENT_LABEL):
            _run("launchctl", "bootout", f"{_domain()}/{label}")
            _run("launchctl", "enable", f"{_domain()}/{label}")  # drop any override
            _plist_path(label).unlink(missing_ok=True)
    elif sys.platform == "win32":
        _win_unregister()
        _win_stop_trays()


def set_login_enabled(binary: str, on: bool) -> None:
    """Turns starting at login on or off without stopping or restarting
    anything that runs now (the tray menu uses it)."""
    binary = gui_path(binary)
    if sys.platform == "darwin":
        for label, arg in _jobs().items():
            if on:
                p = _plist_path(label)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(_plist(label, binary, arg))  # launchd loads it at the next login
                _run("launchctl", "enable", f"{_domain()}/{label}")
            elif (r := _run("launchctl", "disable", f"{_domain()}/{label}")).returncode != 0:
                raise RuntimeError(f"launchctl disable {label}: {r.stderr}".strip())
    elif sys.platform == "win32":
        _win_register(binary) if on else _win_unregister()
    else:
        raise RuntimeError("start at login is supported on macOS and Windows")


def registered() -> bool:
    """Whether login items were ever set up, even if turned off since
    (first-launch setup must not undo the user's choice)."""
    if sys.platform == "darwin":
        return _plist_path(AGENT_LABEL).exists()
    if sys.platform == "win32":
        return _run("schtasks", "/Query", "/TN", TASK_NAME).returncode == 0
    return False


def installed() -> bool:
    """Whether the agent starts at login."""
    if sys.platform == "darwin":
        return registered() and not _disabled(AGENT_LABEL)
    return registered()


def start_registered() -> bool:
    if sys.platform == "darwin":
        return _run("launchctl", "kickstart", f"{_domain()}/{AGENT_LABEL}").returncode == 0
    if sys.platform == "win32":
        return _run("schtasks", "/Run", "/TN", TASK_NAME).returncode == 0
    return False


def start_agent(binary: str) -> None:
    """Starts the agent through the service manager, or directly."""
    if not (installed() and start_registered()):
        spawn(binary, "agent")
