"""The tray's operating-system workarounds: the single-instance lock, running UI calls on
the thread the OS wants, and the menu-bar text on macOS."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable

from .dirs import data_dir

TRAY_MUTEX = r"Local\KeypadTray"


def tray_lock() -> bool:
    """False if another tray already runs for this user."""
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        tray_lock.handle = k32.CreateMutexW(None, False, TRAY_MUTEX)  # type: ignore[attr-defined]  # held for life
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    import fcntl

    data_dir().mkdir(parents=True, exist_ok=True)
    f = open(data_dir() / "tray.lock", "w")  # noqa: SIM115 - kept open (and locked) for the process lifetime
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    tray_lock.handle = f  # type: ignore[attr-defined]
    return True


def guard_win32_menu(icon, lock: threading.RLock) -> None:
    """pystray's win32 backend has no locking around its native menu handle: update_menu()
    (called off the UI thread by the poll) destroys the old HMENU and installs a new one, while a
    real click is dispatched on the message-loop thread straight into _on_notify, which reads that
    same handle and calls TrackPopupMenuEx on it. A poll landing mid-click can have one thread
    destroy the handle the other is about to show a popup with, crashing the process with no
    Python traceback. Serialize both sides behind one lock (see on_main). The lock must be
    reentrant: TrackPopupMenuEx pumps a nested message loop on the UI thread while a menu is open,
    and a second notification can re-dispatch into _on_notify on that same thread."""
    if sys.platform != "win32":
        return
    orig_on_notify = icon._on_notify

    def locked_on_notify(wparam, lparam):
        with lock:
            return orig_on_notify(wparam, lparam)

    for code, handler in list(icon._message_handlers.items()):
        if getattr(handler, "__func__", None) is orig_on_notify.__func__:
            icon._message_handlers[code] = locked_on_notify


def on_main(fn: Callable[[], None], win_lock: threading.RLock) -> None:
    """Runs a UI update where the OS allows it: AppKit only on the main thread; on Windows
    behind the lock that guard_win32_menu shares with pystray's click handler."""
    if sys.platform == "darwin":
        from PyObjCTools import AppHelper

        AppHelper.callAfter(fn)
    elif sys.platform == "win32":
        with win_lock:
            fn()
    else:
        fn()


def set_bar_text(icon, text: str) -> None:
    """Next to the icon in the macOS menu bar (the Windows notification area has no text; the tooltip carries it)."""
    if sys.platform != "darwin":
        return
    try:
        import AppKit

        button = icon._status_item.button()
        button.setImagePosition_(AppKit.NSImageLeft)
        button.setTitle_(" " + text if text else "")
    except Exception:  # pystray internals: losing the text must not break the tray
        pass
