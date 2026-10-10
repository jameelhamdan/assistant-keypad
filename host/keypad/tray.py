"""`keypad tray`: the menu bar / notification area icon and its menu, which
holds every setting. Text is entered in native pop-up dialogs."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from . import agent_cmd, claudecfg, config, dialog, ipc, osutil, service, trayos, updater
from .core.text import clip
from .dirs import in_app_bundle, self_path
from .trayicon import COL_OFF, icon_image, tray_look
from .version import version

STOP_PRESETS = [  # (ask_when_finished to set, label, whether a stored value counts as this one)
    (0, "Always", lambda v: v == 0),
    (60, "Only when I'm away", lambda v: v > 0),
    (-1, "Never", lambda v: v < 0),
]
OPTIONS = [  # (path in config, label)
    (("behavior", "notify_when_finished"), "Light up the keypad when Claude finishes"),
    (("behavior", "always_for_session"), "“Don't ask again” lasts this session only"),
    (("behavior", "auto_update"), "Update Keypad automatically"),
]


def tray_place() -> str:
    """Where the icon lives, for messages."""
    return "notification area (it may be under the ^ arrow)" if sys.platform == "win32" else "menu bar"


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
        self.updating: dict[str, str] = {}  # keypads this tray started a firmware update on -> their names
        self.claude_ok = claudecfg.check(self.bin).installed
        self.login = service.installed()
        self.quitting = False
        self.updater = updater.Updater(
            auto=lambda: bool((self.cfg.get("behavior") or {}).get("auto_update", True)),
            busy=lambda: bool(self.snap.get("busy")) or bool(self.snap.get("queue")),
            say=dialog.notify, quit_for_update=self.quit, log=logging.getLogger("keypad"))
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
        self.updater.start()

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
        self._update_done(snap)
        look = tray_look(snap) if ok else ("off", COL_OFF)
        if look != self._color:
            self._color = look
            img = icon_image(look[1], look[0])
            self.on_main(lambda: setattr(self.icon, "icon", img))
        sig = json.dumps([snap, cfg, ok, self.claude_ok, self.login], sort_keys=True, default=str)
        if sig != self._sig:  # only rebuild the menu when something changed
            self._sig = sig
            showing, bar = self._showing(), self._bar_text()
            tip = "Keypad: " + self._head() + (f"\n{showing}" if showing else "")
            self.on_main(lambda: setattr(self.icon, "title", tip))
            self.on_main(lambda: trayos.set_bar_text(self.icon, bar))
            self.refresh()
        return ok

    def _shown(self) -> tuple[int, dict | None, int]:
        """The session the keypad shows, its place in the keypad's list and the list's length
        (none when the agent or the keypad is away)."""
        if not self.ok or not self.snap.get("keypads"):
            return 0, None, 0
        i, x = shown(self.snap)
        return i, x, len(synced(self.snap))

    def _showing(self) -> str:
        """"Showing Fix tests (money-mind) · 2 of 3 sessions"."""
        i, x, n = self._shown()
        if not x:
            return ""
        return f"Showing {session_label(x)}" + (f" · {i} of {n} sessions" if n > 1 else "")

    def _bar_text(self) -> str:
        """Next to the icon in the macOS menu bar: the shown project and its
        place in the keypad's list ("money-mind 2/3")."""
        i, x, n = self._shown()
        if not x:
            return ""
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
                    Item("Options", Menu(*self._option_items()))]
        if rel := self.updater.available:
            out += [SEP, Item(f"Update to {rel.version}…", self.act(self.update_now, True))]
        out += [SEP,
                Item("Claude Code integration", self.act(self.toggle_claude, True), checked=lambda _: self.claude_ok),
                Item("Start at login", self.act(self.toggle_login), checked=lambda _: self.login),
                Item("Advanced", Menu(
                    Item("Edit settings file…", self.act(self.edit_file)),
                    Item("Open logs folder", self.act(lambda: osutil.open_path(str(config.log_dir())))),
                    Item("Check for updates…", self.act(self.check_updates, True)),
                    Item(f"Keypad {version()}", None, enabled=False))),
                SEP]
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
        prog = (self.snap.get("updates") or {}).get(dev_id)
        prog = prog if prog is not None and 0 <= prog < 100 else None
        sub = [
            Item("Rename…", self.act(lambda: self.rename(dev_id, name), True)),
            Item("Change Wi-Fi…" if d.get("paired") else "Set up Wi-Fi…", self.act(self.add_keypad, True),
                 enabled=bool(k and k["link"] == "usb")),
            Item(f"Updating… {prog}%" if prog is not None else "Update firmware",
                 self.act(lambda: self.update_firmware(dev_id, name), True), enabled=bool(k) and prog is None),
            Item("Use keypad here", self.act(lambda: call("POST", f"/devices/{dev_id}/take", {})),
                 enabled=not k and bool(d.get("paired"))),
            Item("Forget…", self.act(lambda: self.forget(dev_id, name), True)),
        ]
        return Item(f"{name} · {how}", Menu(*sub))

    def _session_items(self) -> list:
        """The sessions the keypad mirrors: click one to show it on the keypad."""
        Item = self.pystray.MenuItem
        on = synced(self.snap)
        if not on:
            return [Item("No Claude Code sessions", None, enabled=False)]
        out = [Item(f"{session_label(x)} · {state_word(x.get('state', ''))}",
                    self.act(lambda sid=x["id"]: call("POST", "/session", {"id": sid})),
                    checked=lambda _, sid=x["id"]: self.snap.get("current") == sid, radio=True) for x in on]
        return out

    def _option_items(self) -> list:
        Item, Menu = self.pystray.MenuItem, self.pystray.Menu
        out = [Item(label, self.act(lambda p=path: self.edit_config(lambda cfg: _set_path(cfg, p, not _get_path(cfg, p)))),
                    checked=lambda _, p=path: bool(_get_path(self.cfg, p))) for path, label in OPTIONS]

        out.insert(0, Item("When Claude finishes, ask on the keypad", Menu(*[
            Item(label, self.act(lambda v=v: self.edit_config(lambda cfg: _set_path(cfg, ("behavior", "ask_when_finished"), v))),
                 radio=True, checked=lambda _, is_it=is_it: is_it(_get_path(self.cfg, ("behavior", "ask_when_finished"))))
            for v, label, is_it in STOP_PRESETS])))
        return out

    # ---- actions ----

    def edit_config(self, fn: Callable[[dict], None]) -> None:
        """Changes the agent's settings (read, modify, write back)."""
        c = call("GET", "/config")
        fn(c)
        call("PUT", "/config", c)

    def welcome(self) -> None:
        """Once, the first time an agent is reachable: offer to connect Claude Code
        and say where Keypad lives and how to add a keypad."""
        marker = config.data_dir() / ".welcomed"
        if marker.exists():
            return
        marker.write_text("1")
        how = f"To add a keypad: plug it in with USB, then use Add keypad… in the keypad icon in the {tray_place()}."
        if claudecfg.check(self.bin).installed:
            dialog.notify("Keypad is running", how)
        elif dialog.confirm("Welcome to Keypad", "Connect Claude Code to the keypad?\n\nThis adds Keypad's hooks "
                            "to ~/.claude (your settings file is backed up first). Nothing is ever approved without "
                            f"a key press.\n\n{how}", "Connect", "Not now"):
            self._install_claude()

    def _install_claude(self) -> None:
        claudecfg.install(self.bin)
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

    def check_updates(self) -> None:
        """Advanced -> Check for updates…: says where things stand, and installs on request."""
        title = "Keypad updates"
        if not updater.supported():
            dialog.alert(title, f"Keypad {version()} is a development build: it does not update itself. "
                         "Install a release from the project's releases page to get updates.")
            return
        rel = self.updater.check()
        if rel is None:
            dialog.alert(title, self.updater.error or f"Keypad {version()} is up to date.")
            return
        self.update_now()

    def update_now(self) -> None:
        rel = self.updater.available
        if rel and dialog.confirm("Update Keypad", f"Keypad {rel.version} is available (you have {version()}). "
                                  "Install it now? Keypad restarts.", "Install", "Not now"):
            self.updater.install()

    def toggle_login(self) -> None:
        service.set_login_enabled(self.bin, not self.login)
        self.login = service.installed()

    def edit_file(self) -> None:
        if not config.config_path().exists():
            config.load()
        osutil.open_text(str(config.config_path()))

    def add_keypad(self) -> None:
        """Sets up a keypad plugged in with USB: its name, the Wi-Fi it joins and the password, in native dialogs."""
        title = "Set up a keypad"
        call("POST", "/pairing")
        usb = self._wait_for(lambda s: next((k for k in s.get("keypads", []) if k["link"] == "usb"), None), 45,
                             f"{title}: plug the keypad into this computer with a USB-C cable (not a charge-only one)…")
        if usb is None:
            dialog.alert(title, "No keypad found over USB. Plug it in with a data cable and try again.")
            return
        known = next((d for d in self.snap.get("devices", []) if d["id"] == usb["id"]), {})
        name = dialog.input(title, f"Found {usb['id']}. A name for it, shown on its screen.", "Name",
                            (known.get("name") or "") if known.get("name") != usb["id"] else "Desk keypad")
        if not name:
            return
        ssid = dialog.input(title, "The Wi-Fi network the keypad should join (2.4 GHz).", "Network name", osutil.ssid())
        if not ssid:
            return
        password = dialog.input(title, "The Wi-Fi password. It is stored only on the keypad.", "Password", secret=True)
        if password is None:
            return
        call("POST", f"/devices/{usb['id']}/provision", {"ssid": ssid, "pass": password, "name": name}, timeout=20)
        wifi = self._wait_for(lambda s: next((k for k in s.get("keypads", []) if k["id"] == usb["id"] and k["link"] == "wifi"), None),
                              45, f"{name} is joining {ssid}…")
        if wifi:
            dialog.alert(title, f"{name} is connected over Wi-Fi. You can unplug it.")
        else:
            why = (self.snap.get("problems") or {}).get(usb["id"], "it did not join the Wi-Fi in time (check the name and password)")
            dialog.alert(title, f"{name} is paired but not connected: {why}")

    def _wait_for(self, find: Callable[[dict], Any], seconds: float, hint: str) -> Any:
        """Polls the agent until find(status) returns something; tells the user what is awaited once."""
        end, told = time.monotonic() + seconds, False
        while time.monotonic() < end:
            snap = call("GET", "/status", timeout=5)
            self.snap = snap
            if (found := find(snap)) is not None:
                return found
            if not told:
                told = True
                dialog.notify("Keypad", hint)
            time.sleep(1)
        return None

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
        self.updating[dev_id] = name
        call("POST", f"/devices/{dev_id}/update", {})

    def _update_done(self, snap: dict) -> None:
        """Tells how a firmware update this tray started ended (progress arrives with the status)."""
        for dev_id, name in list(self.updating.items()):
            p = (snap.get("updates") or {}).get(dev_id, 0)
            if p >= 100 or p < 0:
                del self.updating[dev_id]
                if p < 0:
                    msg = ("Update firmware", "The update failed. Open the logs folder for details; the keypad "
                           "keeps its current firmware.")
                    threading.Thread(target=dialog.alert, args=msg, daemon=True).start()
                else:
                    msg = ("Firmware updated", f"{name} is restarting with its new firmware.")
                    threading.Thread(target=dialog.notify, args=msg, daemon=True).start()

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
        ipc.quit_agent()
        service.uninstall()
        claudecfg.uninstall()
        dialog.notify("Keypad uninstalled", "You can now move Keypad.app to the Trash.")
        self.icon.stop()

    def quit(self, icon=None, item=None) -> None:
        self.quitting = True
        ipc.quit_agent(timeout=3)
        self.icon.stop()
