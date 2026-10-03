"""`keypad tray`: the menu bar / notification area icon and its menu, which
holds every setting. Text is entered in native pop-up dialogs."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from . import claudecfg, config, dialog, ipc, osutil, service
from .core.sessions import WAITING_STATES, WORKING_STATES
from .core.text import clip
from .paths import in_app_bundle, self_path
from .version import version

RELEASES_URL = "https://github.com/jameelhamdan/assistant-keypad/releases"
RELEASES_API = "https://api.github.com/repos/jameelhamdan/assistant-keypad/releases/latest"
UPDATE_CHECK_EVERY = 24 * 3600

# The keypad's palette: Claude orange = working, permission blue = waiting
# on you, green = idle, grey = paused or no keypad.
COL_IDLE = (0x4E, 0xBA, 0x65)
COL_WORKING = (0xD7, 0x77, 0x57)
COL_WAITING = (0x57, 0x69, 0xF7)
COL_OFF = (0x8A, 0x93, 0xA0)

WAIT_PRESETS = [60, 120, 300, 600, 1800]
LIMIT_PRESETS = [5, 10, 20, 50, 100]
STOP_PRESETS = [  # (ask_on_stop, stop_when_away, label)
    (True, 60, "When I've been away 1 min"), (True, 120, "When I've been away 2 min"),
    (True, 300, "When I've been away 5 min"), (True, 0, "Always"), (False, 0, "Never"),
]
OPTIONS = [  # (path in config, label)
    (("behavior", "wifi_only"), "Wi-Fi only (USB just for power, setup and flashing)"),
    (("behavior", "intercept_ask_user_question"), "Answer Claude's questions on the keypad"),
    (("behavior", "pc_handback", "enabled"), "Hand back to the PC when I type there"),
    (("behavior", "announce_in_context"), "Tell Claude a keypad is connected"),
]


def tray_place() -> str:
    """Where the icon lives, for messages."""
    return "notification area (it may be under the ^ arrow)" if sys.platform == "win32" else "menu bar"


def icon_image(rgb: tuple[int, int, int], mode: str = "sessions"):
    """The keypad: 2 rows x 4 keys. Three looks, readable at 16px:
    "sessions" - filled, top row in the state color (sessions are mirrored);
    "ready"    - outlined keys: a keypad is connected but there are no sessions;
    "off"      - dim keys with a slash: no keypad (or paused)."""
    from PIL import Image, ImageDraw

    s, key, gap = 64, 12, 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0 = (s - (4 * key + 3 * gap)) // 2
    y0 = (s - (2 * key + gap)) // 2
    for row in range(2):
        for col in range(4):
            x, y = x0 + col * (key + gap), y0 + row * (key + gap)
            box = (x, y, x + key - 1, y + key - 1)
            if mode == "ready":
                d.rounded_rectangle(box, radius=3, outline=(*rgb, 255), width=2)
            elif mode == "off":
                d.rounded_rectangle(box, radius=3, fill=(*rgb, 110))
            else:
                d.rounded_rectangle(box, radius=3, fill=(*rgb, 255 if row == 0 else 150))
    if mode == "off":
        d.line((x0 - 2, y0 + 2 * key + gap + 4, x0 + 4 * key + 3 * gap + 2, y0 - 4), fill=(*rgb, 255), width=4)
    return img


def tray_lock() -> bool:
    """False if another tray already runs for this user."""
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        tray_lock.handle = k32.CreateMutexW(None, False, service.TRAY_MUTEX)  # type: ignore[attr-defined]  # held for life
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    import fcntl

    config.data_dir().mkdir(parents=True, exist_ok=True)
    f = open(config.data_dir() / "tray.lock", "w")  # noqa: SIM115 - kept open (and locked) for the process lifetime
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    tray_lock.handle = f  # type: ignore[attr-defined]
    return True


def run_tray() -> None:
    if not tray_lock():
        # Already running (e.g. the app was opened again): point at it.
        dialog.notify("Keypad is already running", f"Click the keypad icon in the {tray_place()}.")
        return
    # First launch of the macOS app: register the login items. launchd then
    # starts the agent and a tray of its own, so this instance steps aside.
    if in_app_bundle() and not service.registered():
        try:
            service.install(self_path())
            return
        except (OSError, RuntimeError):
            pass
    Tray().run()


def call(method: str, path: str, body: Any = None, timeout: float = 30) -> Any:
    return ipc.request(method, path, body, timeout=timeout)


def complain(fn: Callable[[], Any]) -> None:
    """Runs fn; shows an error dialog when it fails."""
    try:
        fn()
    except ipc.AgentNotRunning:
        dialog.alert("Keypad", "The Keypad agent isn't running. It restarts by itself in a few seconds.")
    except Exception as e:
        dialog.alert("Keypad", str(e))


def _get_path(d: dict, path: tuple[str, ...]) -> Any:
    for k in path:
        d = d.get(k, {}) if isinstance(d, dict) else {}
    return d


def _set_path(d: dict, path: tuple[str, ...], v: Any) -> None:
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = v


def minutes(sec: int) -> str:
    return f"{sec} s" if sec < 60 else f"{sec // 60} min"


def _version_tuple(v: str) -> tuple[int, ...]:
    out = []
    for p in v.lstrip("v").split("."):
        if not p.isdigit():
            break
        out.append(int(p))
    return tuple(out)


def newer(a: str, b: str) -> bool:
    """Whether release tag a is newer than the running version b."""
    return _version_tuple(a) > _version_tuple(b)


def latest_release() -> str:
    """The latest release tag on GitHub ("" on a dev build, no network, or
    any other failure -- this must never raise or block the caller)."""
    if version() == "dev":
        return ""
    try:
        req = urllib.request.Request(RELEASES_API, headers={"Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=4) as r:
            tag = json.loads(r.read()).get("tag_name", "")
    except (OSError, urllib.error.URLError, ValueError):
        return ""
    return tag if isinstance(tag, str) else ""


def state_word(state: str) -> str:
    """A session's state in a word, as the keypad's session list shows it.
    This and _look_for below duplicate by hand the state taxonomy in
    core/sessions.py (BUSY_STATES/WAITING_STATES/WORKING_STATES) and
    firmware/src/ui.cpp (busy()/waiting()/stateColor()/stateWord()); a new
    state needs updating in all of them."""
    if state in WORKING_STATES:
        return "working"
    if state in WAITING_STATES:
        return "needs you"
    return "failed" if state == "failed" else "idle"


def session_label(x: dict) -> str:
    """"Fix tests (money-mind)", or the project alone for an untitled session."""
    project = x.get("project") or x["id"][:8]
    return f"{clip(x['name'], 32)} ({project})" if x.get("name") else project


def synced(s: dict) -> list[dict]:
    """The sessions the keypad lists, in its order."""
    return [x for x in s.get("sessions", []) if x.get("on_keypad")]


def shown(s: dict) -> tuple[int, dict | None]:
    """The session the keypad shows and its position (1-based) in its list."""
    for i, x in enumerate(synced(s), 1):
        if x["id"] == s.get("current"):
            return i, x
    return 0, None


class Tray:
    def __init__(self) -> None:
        import pystray

        self.pystray = pystray
        self.bin = self_path()
        self.snap: dict[str, Any] = {}
        self.cfg: dict[str, Any] = {}
        self.ok = False  # the agent answered the last poll
        self.progress: dict[str, int] = {}  # firmware updates in progress, by keypad id
        self.claude_ok = claudecfg.check(self.bin).installed
        self.login = service.installed()
        self.update_available = ""  # a newer release's tag, once found
        self._sig = ""
        self._color: tuple[str, tuple[int, int, int]] | None = None  # (mode, rgb) last drawn
        self._dialogs = threading.Lock()  # one dialog flow at a time
        # see _guard_win32_menu. Must be reentrant: TrackPopupMenuEx pumps its
        # own nested message loop on the UI thread while a menu is open, and
        # a second tray notification arriving during that can re-dispatch
        # into _on_notify on the very same thread before the outer call
        # returns -- a plain Lock would self-deadlock there.
        self._win_ui_lock = threading.RLock()
        self.icon = pystray.Icon("Keypad", icon_image(COL_OFF), "Keypad", menu=pystray.Menu(self._items))
        if sys.platform == "win32":
            self._guard_win32_menu()

    # ---- plumbing ----

    def _guard_win32_menu(self) -> None:
        """pystray's win32 backend has no locking around its native menu
        handle: update_menu() (called off the UI thread by poll_once/refresh)
        destroys the old HMENU and installs a new one, while a real click is
        dispatched on the message-loop thread straight into _on_notify, which
        reads that same handle and calls TrackPopupMenuEx on it. A poll
        landing mid-click can have one thread destroy the handle the other is
        about to show a popup with, crashing the process with no Python
        traceback. Serialize both sides behind one lock via on_main()."""
        icon = self.icon
        orig_on_notify = icon._on_notify

        def locked_on_notify(wparam, lparam):
            with self._win_ui_lock:
                return orig_on_notify(wparam, lparam)

        for code, handler in list(icon._message_handlers.items()):
            if getattr(handler, "__func__", None) is orig_on_notify.__func__:
                icon._message_handlers[code] = locked_on_notify

    def run(self) -> None:
        if sys.platform == "darwin":  # a menu bar app: no Dock icon, even when not run from the .app bundle
            import AppKit

            AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.icon.run(setup=self._setup)

    def _setup(self, icon) -> None:
        icon.visible = True
        threading.Thread(target=self._poll, daemon=True).start()
        threading.Thread(target=self._check_updates, daemon=True).start()

    def _check_updates(self) -> None:
        """Looks for a newer release once at startup, then once a day. Keypad
        never downloads or installs anything itself -- just points at the
        release to keep this simple, matching the manual update flow in the
        README."""
        while True:
            if (tag := latest_release()) and newer(tag, version()) and tag != self.update_available:
                self.update_available = tag
                self.refresh()
                dialog.notify("Keypad update available", f"{tag} is out. See the tray menu to get it.")
            time.sleep(UPDATE_CHECK_EVERY)

    def on_main(self, fn: Callable[[], None]) -> None:
        """AppKit may only be touched from the main thread; pystray doesn't
        ensure it. On Windows, pystray's menu/icon/title updates and its own
        click handler touch the same native handles with no locking (see
        _guard_win32_menu) -- route them through the same lock here."""
        if sys.platform == "darwin":
            from PyObjCTools import AppHelper

            AppHelper.callAfter(fn)
        elif sys.platform == "win32":
            with self._win_ui_lock:
                fn()
        else:
            fn()

    def refresh(self) -> None:
        self.on_main(self.icon.update_menu)

    def act(self, fn: Callable[[], Any], dialog_flow: bool = False) -> Callable:
        """A menu callback: runs fn off the UI thread (dialogs block), errors shown."""
        def cb(icon=None, item=None) -> None:
            def work() -> None:
                if dialog_flow:
                    if not self._dialogs.acquire(blocking=False):
                        return  # a dialog is already open
                    try:
                        complain(fn)
                    finally:
                        self._dialogs.release()
                else:
                    complain(fn)
                self.poll_once()

            threading.Thread(target=work, daemon=True).start()
        return cb

    def _poll(self) -> None:
        last_start = 0.0
        welcomed = False
        while True:
            if not self.poll_once() and time.monotonic() - last_start > 10:
                last_start = time.monotonic()
                try:
                    service.start_agent(self.bin)
                except (OSError, RuntimeError):
                    pass
            elif self.ok and not welcomed:
                welcomed = True
                threading.Thread(target=self.act(self.welcome, dialog_flow=True), daemon=True).start()
            time.sleep(1.5)

    def poll_once(self) -> bool:
        try:
            snap, cfg, ok = call("GET", "/status", timeout=5), call("GET", "/config", timeout=5), True
        except (ipc.AgentNotRunning, ipc.RequestError):
            snap, cfg, ok = {}, self.cfg, False
        self.snap, self.cfg, self.ok = snap, cfg, ok
        look = self._look_for(snap) if ok else ("off", COL_OFF)
        if look != self._color:
            self._color = look
            img = icon_image(look[1], look[0])
            self.on_main(lambda: setattr(self.icon, "icon", img))
        sig = json.dumps([snap, cfg, ok, self.progress, self.claude_ok, self.login], sort_keys=True, default=str)
        if sig != self._sig:  # only rebuild the menu when something changed
            self._sig = sig
            showing, bar = self._showing(), self._bar_text()
            tip = "Keypad: " + self._head() + (f"\n{showing}" if showing else "")
            self.on_main(lambda: setattr(self.icon, "title", tip))
            self.on_main(lambda: self._set_bar_text(bar))
            self.refresh()
        return ok

    def _showing(self) -> str:
        """"Showing Fix tests (money-mind) · 2 of 3 sessions"."""
        i, x = shown(self.snap)
        n = len(synced(self.snap))
        if not self.ok or not self.snap.get("keypads") or not x:
            return ""
        return f"Showing {session_label(x)}" + (f" · {i} of {n} sessions" if n > 1 else "")

    def _bar_text(self) -> str:
        """Next to the icon in the macOS menu bar: the shown project and its
        place in the keypad's list ("money-mind 2/3")."""
        i, x = shown(self.snap)
        if not self.ok or not self.snap.get("keypads") or not x:
            return ""
        n = len(synced(self.snap))
        return clip(x.get("project") or x["id"][:8], 16) + (f" {i}/{n}" if n > 1 else "")

    def _set_bar_text(self, text: str) -> None:
        if sys.platform != "darwin":
            return  # the Windows notification area has no text; the tooltip carries it
        try:
            import AppKit

            button = self.icon._status_item.button()
            button.setImagePosition_(AppKit.NSImageLeft)
            button.setTitle_(" " + text if text else "")
        except Exception:  # pystray internals: losing the text must not break the tray
            pass

    def _look_for(self, s: dict) -> tuple[str, tuple[int, int, int]]:
        """The icon's three states: no keypad (or paused) -> "off"; a keypad
        but no sessions -> "ready"; sessions -> colored by what they're doing."""
        sessions = s.get("sessions", [])
        if s.get("paused") or not s.get("keypads"):
            return "off", COL_OFF
        if not sessions:
            return "ready", COL_IDLE
        states = {x.get("state") for x in sessions}
        if s.get("busy") or states & WAITING_STATES:
            return "sessions", COL_WAITING
        if states & WORKING_STATES:
            return "sessions", COL_WORKING
        return "sessions", COL_IDLE

    def _head(self) -> str:
        if not self.ok:
            return "Agent not running, starting…"
        if self.snap.get("paused"):
            return "Paused, decisions stay on the PC"
        n = len(self.snap.get("keypads", []))
        return {0: "No keypad connected", 1: "1 keypad connected"}.get(n, f"{n} keypads connected")

    # ---- the menu ----

    def _items(self):
        pystray = self.pystray
        Item, Menu, SEP = pystray.MenuItem, pystray.Menu, pystray.Menu.SEPARATOR
        s = self.snap
        out = [Item(self._head(), None, enabled=False)]
        if showing := self._showing():
            out.append(Item(showing, None, enabled=False))
        if self.ok:
            out += [self._keypad_item(d) for d in s.get("devices", [])[:8]]
            out += [Item("Add keypad…", self.act(self.add_keypad, True)), SEP]
            out += self._session_items()
            out += [SEP, Item("Pause keypad", self.act(lambda: call("POST", "/pause", {"paused": not s.get("paused")})),
                              checked=lambda _: bool(self.snap.get("paused"))),
                    Item("Options", Menu(*self._option_items())),
                    Item("Saved prompts", Menu(*self._shortcut_items()))]
        out += [SEP,
                Item("Claude Code integration", self.act(self.toggle_claude, True), checked=lambda _: self.claude_ok),
                Item("Start at login", self.act(self.toggle_login), checked=lambda _: self.login),
                Item("Edit settings file…", self.act(self.edit_file)),
                Item("Open logs folder", self.act(lambda: osutil.open_path(str(config.log_dir())))),
                SEP, Item(f"Keypad {version()}", None, enabled=False)]
        if self.update_available:
            out.append(Item(f"Update available: {self.update_available}", self.act(self.open_release)))
        if sys.platform == "darwin":  # Windows has a normal uninstaller in "Apps & features"
            out.append(Item("Uninstall Keypad…", self.act(self.uninstall, True)))
        out.append(Item("Quit Keypad", self.quit))
        return out

    def _keypad_item(self, d: dict):
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu
        live = {k["id"]: k for k in self.snap.get("keypads", [])}
        k = live.get(d["id"])
        dev_id, name = d["id"], d.get("name") or d["id"]
        if k:
            how = {"usb": "USB", "wifi": "Wi-Fi", "fake": "simulated"}.get(k["link"], k["link"])
            if k.get("battery", -1) >= 0:
                how += f" · {k['battery']}%"
        else:
            how = "offline"
        theme = d.get("theme") if d.get("theme") in ("dark", "light") else "system"
        patch = lambda body: self.act(lambda: call("PATCH", f"/devices/{dev_id}", body))  # noqa: E731
        themes = [Item(label, patch({"theme": t}), checked=lambda _, t=t: theme == t, radio=True)
                  for t, label in (("system", "Match computer"), ("dark", "Dark"), ("light", "Light"))]
        bright = [Item(f"{b}%", patch({"brightness": b}), checked=lambda _, b=b: b - 12 <= d.get("brightness", 80) < b + 13,
                       radio=True) for b in (25, 50, 75, 100)]
        prog = self.progress.get(dev_id)
        sub = [
            Item("Theme", Menu(*themes)), Item("Brightness", Menu(*bright)),
            Item("Identify", self.act(lambda: call("POST", f"/devices/{dev_id}/identify")), enabled=bool(k)),
            Item("Rename…", self.act(lambda: self.rename(dev_id, name), True)),
            Item("Only these projects…", self.act(lambda: self.projects(dev_id, d.get("projects") or []), True)),
            self.pystray.Menu.SEPARATOR,
            Item("Change Wi-Fi…" if d.get("paired") else "Set up Wi-Fi…", self.act(lambda: self.pair(dev_id, name), True),
                 enabled=bool(k and k["link"] == "usb")),
            Item(f"Updating… {prog}%" if prog is not None else "Update firmware",
                 self.act(lambda: self.update_firmware(dev_id, name), True), enabled=bool(k) and prog is None),
            Item("Forget…", self.act(lambda: self.forget(dev_id, name), True)),
        ]
        return Item(f"{name} · {how}", Menu(*sub))

    def _session_items(self) -> list:
        """Which sessions the keypad mirrors, which one it shows, how to switch."""
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu
        s = self.snap
        sessions = s.get("sessions", [])
        on = synced(s)
        if not sessions:
            return [Item("No Claude Code sessions", None, enabled=False)]
        follow = "following the latest" if not s.get("pinned") else "pinned"
        head = f"Sessions on the keypad: {len(on)}" + (f" · {follow}" if on else "")
        out = [Item(head, None, enabled=False)]
        for x in sessions[:10]:
            label = f"{session_label(x)} · {state_word(x.get('state', ''))}"
            if not x.get("on_keypad"):
                label += " · not on the keypad"
            out.append(Item(label, self.act(lambda sid=x["id"]: call("POST", "/session", {"id": sid})),
                            checked=lambda _, sid=x["id"]: self.snap.get("current") == sid, radio=True,
                            enabled=bool(x.get("on_keypad"))))
        out.append(Item("Follow the latest activity", self.act(lambda: call("POST", "/session", {"follow": True})),
                        checked=lambda _: not self.snap.get("pinned")))
        if len(on) > 1:
            out.append(Item("Switch: click one here, or press 6 on the keypad", None, enabled=False))
        shortcuts = s.get("shortcuts", [])
        if shortcuts and s.get("current"):
            out.append(Item("Send to the shown session", Menu(*[
                Item(label, self.act(lambda i=i: call("POST", "/shortcut", {"index": i}))) for i, label in enumerate(shortcuts)])))
        return out

    def _option_items(self) -> list:
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu
        out = [Item(label, self.act(lambda p=path: self.edit_config(lambda cfg: _set_path(cfg, p, not _get_path(cfg, p)))),
                    checked=lambda _, p=path: bool(_get_path(self.cfg, p))) for path, label in OPTIONS]

        def set_stop(ask: bool, away: int) -> Callable:
            return self.act(lambda: self.edit_config(lambda cfg: cfg["behavior"].update(ask_on_stop=ask, stop_when_away=away)))

        def stop_checked(ask: bool, away: int) -> Callable:
            def checked(_) -> bool:
                b = self.cfg.get("behavior", {})
                return b.get("ask_on_stop") == ask and (not ask or b.get("stop_when_away") == away)
            return checked

        out.insert(0, Item("When Claude finishes, ask on the keypad", Menu(*[
            Item(label, set_stop(ask, away), radio=True, checked=stop_checked(ask, away)) for ask, away, label in STOP_PRESETS])))

        def set_wait(sec: int) -> Callable:
            return self.act(lambda: self.edit_config(lambda cfg: _set_path(cfg, ("behavior", "timeout"), sec)))

        out += [self.pystray.Menu.SEPARATOR,
                Item("Keypad waits for an answer", Menu(*[
                    Item(minutes(sec), set_wait(sec), radio=True,
                         checked=lambda _, sec=sec: _get_path(self.cfg, ("behavior", "timeout")) == sec)
                    for sec in WAIT_PRESETS])),
                Item("Continues in a row before asking", Menu(*[
                    Item(str(n), self.act(lambda n=n: self.edit_config(lambda cfg: _set_path(cfg, ("behavior", "max_continues"), n))),
                         radio=True, checked=lambda _, n=n: _get_path(self.cfg, ("behavior", "max_continues")) == n)
                    for n in LIMIT_PRESETS]))]
        return out

    def _shortcut_items(self) -> list:
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu
        shortcuts = self.cfg.get("shortcuts", [])
        out = [Item("Add saved prompt…", self.act(lambda: self.edit_shortcut(None), True),
                    enabled=len(shortcuts) < config.MAX_SHORTCUTS)]
        if shortcuts:
            out.append(self.pystray.Menu.SEPARATOR)
        for i, sc in enumerate(shortcuts):
            out.append(Item(sc.get("label", ""), Menu(
                Item("Edit…", self.act(lambda sc=sc: self.edit_shortcut(sc), True)),
                Item("Move up", self.act(lambda sc=sc: self.edit_config(lambda cfg: _move_up(cfg["shortcuts"], sc))),
                     enabled=i > 0),
                Item("Remove", self.act(lambda sc=sc: self.remove_shortcut(sc), True)))))
        return out

    # ---- actions ----

    def edit_config(self, fn: Callable[[dict], None]) -> None:
        """Changes the agent's settings (read, modify, write back)."""
        c = call("GET", "/config")
        fn(c)
        call("PUT", "/config", c)

    def welcome(self) -> None:
        """Once, the first time an agent is reachable: offer to connect
        Claude Code and say where Keypad lives."""
        marker = config.data_dir() / ".welcomed"
        if marker.exists():
            return
        marker.write_text("1")
        if not claudecfg.check(self.bin).installed and dialog.confirm(
                "Welcome to Keypad", "Connect Claude Code to the keypad?\n\nThis adds Keypad's hooks and its MCP server "
                "to ~/.claude (your settings file is backed up first). Nothing is ever approved without a key press.",
                "Connect", "Not now"):
            self._install_claude()
        dialog.notify("Keypad is running",
                      f"Plug in your keypad with USB. Everything else is in the keypad icon in the {tray_place()}.")

    def _install_claude(self) -> None:
        cfg, _ = config.load()
        claudecfg.install(self.bin, cfg.behavior.max_continues)
        self.claude_ok = claudecfg.check(self.bin).installed
        dialog.notify("Claude Code connected", "Restart any open Claude Code sessions to use the keypad.")

    def toggle_claude(self) -> None:
        if self.claude_ok:
            if dialog.confirm("Disconnect Claude Code?", "Remove Keypad's hooks and MCP server from ~/.claude? "
                              "Claude Code then works as if the keypad didn't exist.", "Disconnect", "Cancel"):
                claudecfg.uninstall()
        else:
            self._install_claude()
        self.claude_ok = claudecfg.check(self.bin).installed

    def toggle_login(self) -> None:
        service.set_login_enabled(self.bin, not self.login)
        self.login = service.installed()

    def edit_file(self) -> None:
        if not config.config_path().exists():
            config.load()
        osutil.open_text(str(config.config_path()))

    def add_keypad(self) -> None:
        names = {d["id"]: d.get("name", "") for d in self.snap.get("devices", [])}
        usb = [k for k in self.snap.get("keypads", []) if k["link"] == "usb"]
        if usb:
            self.pair(usb[0]["id"], names.get(usb[0]["id"], ""))
            return
        try:
            call("POST", "/pairing", {})  # Wi-Fi only mode ignores USB otherwise
        except ipc.RequestError:
            pass
        dialog.alert("Add a keypad", "Plug the keypad into this computer with a USB-C cable (not a charge-only one).\n\n"
                     "It appears in this menu within a few seconds and works over the cable right away. "
                     "Then choose Add keypad… again to set up Wi-Fi.")

    def pair(self, dev_id: str, name: str) -> None:
        """Asks for a name and Wi-Fi details and pairs the keypad (USB only)."""
        title = "Set up Wi-Fi"
        name = "" if name == dev_id else name
        name = dialog.input(title, f"Pair {dev_id} with this computer so it works over Wi-Fi. It must be plugged in "
                            "with USB.\n\nStep 1 of 3: a name for the keypad.", "Name", name or "Desk keypad")
        if name is None:
            return
        ssid = dialog.input(title, "Step 2 of 3: the Wi-Fi network the keypad should join (2.4 GHz).", "Network name",
                            osutil.ssid())
        if ssid is None:
            return
        if not ssid:
            dialog.alert(title, "Enter the Wi-Fi network name.")
            return
        pw = dialog.input(title, f'Step 3 of 3: the password for "{ssid}". It is stored only on the keypad.',
                          "Password", secret=True)
        if pw is None:
            return
        try:
            call("POST", f"/devices/{dev_id}/provision", {"ssid": ssid, "pass": pw, "name": name}, timeout=30)
        except ipc.RequestError as e:
            dialog.alert(title, f"Pairing failed: {e}")
            return
        dialog.notify("Keypad paired", f"It is joining {ssid}. When the Wi-Fi bars on its screen light up, you can unplug it.")

    def rename(self, dev_id: str, name: str) -> None:
        v = dialog.input("Rename keypad", f"A name for {dev_id}, shown on its screen.", "Name", name)
        if v:
            call("PATCH", f"/devices/{dev_id}", {"name": v})

    def projects(self, dev_id: str, current: list[str]) -> None:
        v = dialog.input("Only these projects", "Project folders this keypad shows, separated by commas. "
                         "Leave empty for all projects.", "Projects", ", ".join(current))
        if v is not None:
            call("PATCH", f"/devices/{dev_id}", {"projects": [p.strip() for p in v.split(",") if p.strip()]})

    def forget(self, dev_id: str, name: str) -> None:
        if dialog.confirm(f"Forget {name}?", "This computer forgets the keypad and its Wi-Fi pairing. If it is connected "
                          "now, it also wipes its Wi-Fi settings. Plug it in with USB to use it again.", "Forget", "Cancel"):
            call("POST", f"/devices/{dev_id}/unpair")

    def update_firmware(self, dev_id: str, name: str) -> None:
        """Installs the bundled firmware, showing progress in the menu."""
        fw = call("GET", "/firmware")
        if not fw["bundled"]:
            dialog.alert("Update firmware", f"This build of Keypad has no bundled firmware. Use: keypad update {dev_id} <firmware.bin>")
            return
        if not dialog.confirm("Update firmware", f"Install firmware {fw['version']} on {name}? It takes about a minute; "
                              "keep the keypad connected. It restarts when done.", "Update", "Cancel"):
            return
        call("POST", f"/devices/{dev_id}/update", {})
        try:
            while True:
                time.sleep(0.7)
                p = call("GET", f"/devices/{dev_id}/update")["progress"]
                if p < 0:
                    dialog.alert("Update firmware", "The update failed. Open the logs folder for details; the keypad "
                                 "keeps its current firmware.")
                    return
                self.progress[dev_id] = p
                self.poll_once()
                if p >= 100:
                    dialog.notify("Firmware updated", f"{name} is restarting with firmware {fw['version']}.")
                    return
        finally:
            self.progress.pop(dev_id, None)

    def edit_shortcut(self, target: dict | None) -> None:
        """target is the shortcut shown on the menu when clicked, by value
        (None to add a new one) -- not a position, since self.cfg can be
        refreshed by the poll thread between menu build and click, which
        would make a captured index refer to a different saved prompt."""
        sc = target if target is not None else {"label": "", "prompt": ""}
        title = "Edit saved prompt" if target is not None else "Add saved prompt"
        label = dialog.input(title, "The name on the keypad (up to 28 characters).", "Name", sc["label"])
        if label is None:
            return
        prompt = dialog.input(title, f'The instruction sent to Claude when you pick "{label}".', "Instruction", sc["prompt"])
        if prompt is None:
            return
        if not label.strip() or not prompt.strip():
            dialog.alert(title, "A saved prompt needs both a name and an instruction.")
            return
        new = {"label": label.strip()[:28], "prompt": prompt}

        def fn(cfg: dict) -> None:
            if target is None:
                cfg["shortcuts"].append(new)
            elif target in cfg["shortcuts"]:
                cfg["shortcuts"][cfg["shortcuts"].index(target)] = new
            else:
                dialog.alert(title, "This saved prompt was changed elsewhere; nothing was edited.")

        self.edit_config(fn)

    def remove_shortcut(self, target: dict) -> None:
        """target is matched by value against the live config, for the same
        reason as edit_shortcut: a captured index can drift."""
        if target not in self.cfg.get("shortcuts", []):
            return
        if dialog.confirm("Remove saved prompt?", f'Remove "{target["label"]}"?', "Remove", "Cancel"):
            self.edit_config(lambda cfg: cfg["shortcuts"].remove(target) if target in cfg["shortcuts"] else None)

    def open_release(self) -> None:
        osutil.open_path(f"{RELEASES_URL}/tag/{self.update_available}" if self.update_available else RELEASES_URL)

    def uninstall(self) -> None:
        """Removes the login items and Claude Code integration, then quits.
        Needed on macOS: dragging Keypad.app to the Trash (the normal way to
        remove a mac app) would otherwise leave its LaunchAgents and Claude
        Code hooks pointing at a binary that no longer exists."""
        data_dir = config.data_dir()
        if not dialog.confirm("Uninstall Keypad?", "This removes Keypad's Claude Code hooks and MCP server, and "
                              "turns off starting at login, then quits Keypad. You can then move Keypad.app to the "
                              f"Trash. Settings and pairing keys stay in {data_dir} unless you delete them yourself.",
                              "Uninstall", "Cancel"):
            return
        try:
            call("POST", "/quit", timeout=5)
        except (ipc.AgentNotRunning, ipc.RequestError):
            pass
        service.uninstall()
        claudecfg.uninstall()
        dialog.notify("Keypad uninstalled", "You can now move Keypad.app to the Trash.")
        self.icon.stop()

    def quit(self, icon=None, item=None) -> None:
        try:
            call("POST", "/quit", timeout=3)
        except (ipc.AgentNotRunning, ipc.RequestError):
            pass
        self.icon.stop()


def _move_up(lst: list, item: dict) -> None:
    if item in lst and (i := lst.index(item)) > 0:
        lst[i - 1], lst[i] = lst[i], lst[i - 1]
