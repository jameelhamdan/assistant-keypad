"""`keypad tray`: the menu bar / notification area icon and its menu, which
holds every setting. Text is entered in native pop-up dialogs."""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from . import agent_cmd, claudecfg, config, dialog, ipc, osutil, service, trayos
from .core.text import clip
from .dirs import in_app_bundle, self_path
from .version import version

# The keypad's palette: Claude orange = working, permission blue = waiting
# on you, green = idle, grey = paused or no keypad.
COL_IDLE = (0x4E, 0xBA, 0x65)
COL_WORKING = (0xD7, 0x77, 0x57)
COL_WAITING = (0x57, 0x69, 0xF7)
COL_OFF = (0x8A, 0x93, 0xA0)

STOP_PRESETS = [  # (ask_when_finished, label)
    (60, "When I've been away 1 min"), (120, "When I've been away 2 min"), (300, "When I've been away 5 min"),
    (0, "Always"), (-1, "Never"),
]
OPTIONS = [  # (path in config, label)
    (("behavior", "intercept_ask_user_question"), "Answer Claude's questions on the keypad"),
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


def tray_look(s: dict) -> tuple[str, tuple[int, int, int]]:
    """The icon's three states: no keypad (or paused) -> "off"; a keypad
    but no sessions -> "ready"; sessions -> colored by what they're doing."""
    sessions = s.get("sessions", [])
    if s.get("paused") or not s.get("keypads"):
        return "off", COL_OFF
    if not sessions:
        return "ready", COL_IDLE
    phases = {x.get("phase") for x in sessions}
    if s.get("busy") or phases & {"asking", "stopped"}:
        return "sessions", COL_WAITING
    if phases & {"working", "continuing"}:
        return "sessions", COL_WORKING
    return "sessions", COL_IDLE


def run_tray(quiet: bool = False) -> None:
    if not trayos.tray_lock():
        # Already running (e.g. the app was opened again): point at it. The login task starts
        # Keypad every minute with --quiet, and says nothing while it runs.
        if not quiet:
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
    agent_cmd.start_agent()  # the tray hosts the agent (None: a headless `keypad agent` already runs)
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


def state_word(phase: str) -> str:
    """A session's phase in a word, as the keypad's session list shows it."""
    return {"working": "working", "continuing": "working", "asking": "needs you", "stopped": "needs you"}.get(phase, "idle")


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
        self.quitting = False
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
        trayos.guard_win32_menu(self.icon, self._win_ui_lock)

    # ---- plumbing ----

    def run(self) -> None:
        if sys.platform == "darwin":  # a menu bar app: no Dock icon, even when not run from the .app bundle
            import AppKit

            AppKit.NSApplication.sharedApplication().setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        self.icon.run(setup=self._setup)

    def _setup(self, icon) -> None:
        icon.visible = True
        threading.Thread(target=self._poll, daemon=True).start()

    def on_main(self, fn: Callable[[], None]) -> None:
        trayos.on_main(fn, self._win_ui_lock)

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
        last_start = time.monotonic()  # the agent has had no time to start yet
        welcomed = False
        while True:
            if not self.poll_once() and not self.quitting and time.monotonic() - last_start > 10:
                last_start = time.monotonic()
                agent_cmd.start_agent()  # the agent stopped without us: bring it back in this process
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
        look = tray_look(snap) if ok else ("off", COL_OFF)
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
            self.on_main(lambda: trayos.set_bar_text(self.icon, bar))
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
            how = {"usb": "USB", "wifi": "Wi-Fi"}.get(k["link"], k["link"])
            if k.get("battery", -1) >= 0:
                how += f" · {k['battery']}%"
        else:
            how = "offline"
            if why := (self.snap.get("problems") or {}).get(dev_id):
                how = "can't connect: " + why
        prog = self.progress.get(dev_id)
        sub = [
            Item("Rename…", self.act(lambda: self.rename(dev_id, name), True)),
            Item("Change Wi-Fi…" if d.get("paired") else "Set up Wi-Fi…", self.act(self.add_keypad, True),
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
            label = f"{session_label(x)} · {state_word(x.get('phase', ''))}"
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

        out.insert(0, Item("When Claude finishes, ask on the keypad", Menu(*[
            Item(label, self.act(lambda v=v: self.edit_config(lambda cfg: _set_path(cfg, ("behavior", "ask_when_finished"), v))),
                 radio=True, checked=lambda _, v=v: _get_path(self.cfg, ("behavior", "ask_when_finished")) == v)
            for v, label in STOP_PRESETS])))
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
        for attempt in (1, 2):  # the PUT names the revision it started from: someone else's edit is not overwritten
            c = call("GET", "/config")
            fn(c)
            try:
                call("PUT", "/config", c)
                return
            except ipc.RequestError as e:
                if attempt == 2 or "changed elsewhere" not in str(e):
                    raise

    def welcome(self) -> None:
        """Once, the first time an agent is reachable: offer to connect
        Claude Code and say where Keypad lives."""
        marker = config.data_dir() / ".welcomed"
        if marker.exists():
            return
        marker.write_text("1")
        if not claudecfg.check(self.bin).installed and dialog.confirm(
                "Welcome to Keypad", "Connect Claude Code to the keypad?\n\nThis adds Keypad's hooks "
                "to ~/.claude (your settings file is backed up first). Nothing is ever approved without a key press.",
                "Connect", "Not now"):
            self._install_claude()
        dialog.notify("Keypad is running",
                      f"To add a keypad: plug it in with USB, then use Add keypad… in the keypad icon in the {tray_place()}.")

    def _install_claude(self) -> None:
        cfg, _ = config.load()
        claudecfg.install(self.bin, cfg.behavior)
        self.claude_ok = claudecfg.check(self.bin).installed
        dialog.notify("Claude Code connected", "Restart any open Claude Code sessions to use the keypad.")

    def toggle_claude(self) -> None:
        if self.claude_ok:
            if dialog.confirm("Disconnect Claude Code?", "Remove Keypad's hooks from ~/.claude? "
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
        """Opens the pairing page in the browser: it finds a keypad plugged in with USB and sets up its Wi-Fi."""
        osutil.open_path(call("GET", "/pair-url", timeout=10)["url"])

    def rename(self, dev_id: str, name: str) -> None:
        v = dialog.input("Rename keypad", f"A name for {dev_id}, shown on its screen.", "Name", name)
        if v:
            call("PATCH", f"/devices/{dev_id}", {"name": v})

    def forget(self, dev_id: str, name: str) -> None:
        if dialog.confirm(f"Forget {name}?", "This computer forgets the keypad and its Wi-Fi pairing. If it is connected "
                          "now, it also wipes its Wi-Fi settings. Plug it in with USB to use it again.", "Forget", "Cancel"):
            call("POST", f"/devices/{dev_id}/unpair")

    def update_firmware(self, dev_id: str, name: str) -> None:
        """Installs the bundled firmware, showing progress in the menu."""
        fw = call("GET", "/firmware")
        if not fw["bundled"]:
            dialog.alert("Update firmware", "This build of Keypad has no bundled firmware. Flash the keypad over USB (make flash).")
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

    def uninstall(self) -> None:
        """Removes the login items and Claude Code integration, then quits.
        Needed on macOS: dragging Keypad.app to the Trash (the normal way to
        remove a mac app) would otherwise leave its LaunchAgents and Claude
        Code hooks pointing at a binary that no longer exists."""
        data_dir = config.data_dir()
        self.quitting = True
        if not dialog.confirm("Uninstall Keypad?", "This removes Keypad's Claude Code hooks, and "
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
        self.quitting = True
        try:
            call("POST", "/quit", timeout=3)
        except (ipc.AgentNotRunning, ipc.RequestError):
            pass
        self.icon.stop()


def _move_up(lst: list, item: dict) -> None:
    if item in lst and (i := lst.index(item)) > 0:
        lst[i - 1], lst[i] = lst[i], lst[i - 1]
