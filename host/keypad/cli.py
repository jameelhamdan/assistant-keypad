"""The command-line commands (install, status, update...)."""

from __future__ import annotations

import getpass
import os
import shutil
import sys
import time
from pathlib import Path

from . import claudecfg, config, ipc, osutil, service
from .dirs import self_path


def call(method: str, path: str, body=None, timeout: float = 30):
    try:
        return ipc.request(method, path, body, timeout=timeout)
    except ipc.AgentNotRunning:
        raise RuntimeError("Keypad is not running (start it from the tray, or `keypad tray`)") from None


def cmd_install(args: list[str]) -> None:
    ipc.quit_agent(wait=3)
    claudecfg.install(self_path())
    print("Claude Code integration installed:", claudecfg.settings_path())
    service.install(self_path())
    print("Keypad starts at login and is running now.")
    print("Restart any open Claude Code sessions to pick up the hooks.")


def cmd_uninstall(args: list[str]) -> None:
    ipc.quit_agent(wait=3)
    service.uninstall()
    claudecfg.uninstall()
    print("Removed the Claude Code integration and the login items.")
    if "--purge" in args:
        shutil.rmtree(config.data_dir(), ignore_errors=True)
        print("Also removed settings and pairing keys in", config.data_dir())
    else:
        print("Settings and pairing keys remain in", config.data_dir(), "(--purge removes them too)")


def _opt(args: list[str], name: str) -> str | None:
    """The value after --name (or --name=value), None if absent."""
    for i, a in enumerate(args):
        if a == name:
            if i + 1 >= len(args):
                raise RuntimeError(f"{name} needs a value")
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def _dotenv() -> dict[str, str]:
    """KEY=value lines of the nearest .env above the current directory (comments, quotes and `export` allowed)."""
    here = Path.cwd().resolve()
    for d in (here, *here.parents):
        f = d / ".env"
        if f.is_file():
            out: dict[str, str] = {}
            for line in f.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip().removeprefix("export ").strip()
                k, sep, v = line.partition("=")
                if sep and k.strip() and not line.startswith("#"):
                    out[k.strip()] = v.strip().strip("\"'")
            return out
    return {}


def _wait(find, seconds: float, hint: str):
    """Polls the agent's status until find(status) returns something (None after `seconds`)."""
    end, told = time.monotonic() + seconds, False
    while time.monotonic() < end:
        if (found := find(call("GET", "/status", timeout=5))) is not None:
            return found
        if not told:
            told = True
            print(hint, flush=True)
        time.sleep(1)
    return None


def cmd_setup(args: list[str]) -> None:
    """`keypad setup`: pairs a keypad plugged in with USB and gives it Wi-Fi, the tray's Set up a keypad without dialogs."""
    env = _dotenv()

    def setting(*names: str) -> str | None:  # the environment first, then .env
        return next((v for n in names if (v := os.environ.get(n, env.get(n))) is not None), None)

    name = _opt(args, "--name") or setting("KEYPAD_NAME") or "Desk keypad"
    ssid = _opt(args, "--ssid") or setting("KEYPAD_WIFI_SSID", "WIFI_SSID") or osutil.ssid()
    password = _opt(args, "--pass")
    if password is None:
        password = setting("KEYPAD_WIFI_PASS", "WIFI_PASSWORD")
    if password is None and sys.stdin.isatty():
        password = getpass.getpass(f"Wi-Fi password for {ssid or 'the network'}: ")
    if not ssid:
        raise RuntimeError("no Wi-Fi network name: pass --ssid (the keypad joins 2.4 GHz networks)")
    if password is None:
        raise RuntimeError("no Wi-Fi password: pass --pass, or set WIFI_PASSWORD in .env or KEYPAD_WIFI_PASS (empty for an open network)")
    call("POST", "/pairing")
    usb = _wait(lambda s: next((k for k in s.get("keypads", []) if k["link"] == "usb"), None), 45,
                "Plug the keypad into this computer with a USB-C data cable…")
    if usb is None:
        raise RuntimeError("no keypad found over USB (is the cable data-capable? is another program using the port?)")
    print(f"Found {usb['id']}; pairing it as {name!r} on {ssid!r}…", flush=True)
    call("POST", f"/devices/{usb['id']}/provision", {"ssid": ssid, "pass": password, "name": name}, timeout=20)
    up = _wait(lambda s: next((k for k in s.get("keypads", []) if k["id"] == usb["id"] and k["link"] == "wifi"), None), 45,
               f"{name} is joining {ssid}…")
    if up is None:
        why = (call("GET", "/status").get("problems") or {}).get(usb["id"], "it did not join the Wi-Fi in time (check the name and password)")
        raise RuntimeError(f"{name} is paired but not connected: {why}")
    print(f"{name} is connected over Wi-Fi ({up['addr']}). You can unplug it.")


def cmd_status(args: list[str]) -> None:
    st = claudecfg.check(self_path())
    print(f"Claude Code: hooks {st.hooks}/{st.expected} in {st.settings}"
          f"{' (pointing at another keypad binary)' if st.stale else ''}; start at login: {service.installed()}\n")
    s = call("GET", "/status")
    live = {k["id"]: k for k in s["keypads"]}
    rows = [("KEYPAD", "NAME", "CONNECTION", "PAIRED HERE", "KEYPAD SAYS")]
    mismatch = False
    for d in s["devices"]:
        k = live.get(d["id"])
        says = "-" if k is None else str(k["paired"]).lower()
        mismatch |= k is not None and k["paired"] != d["paired"]
        rows.append((d["id"], d["name"], f"{k['link']} {k['addr']}" if k else "offline", str(d["paired"]).lower(), says))
    print(f"host id  {s['host_id']}\npaused   {str(s['paused']).lower()}\n")
    _table(rows)
    if mismatch:
        print("\nThis computer and the keypad disagree about pairing (it was paired from another computer, or "
              "this one lost its key). Tray -> your keypad -> Set up Wi-Fiâ€¦ (over USB) fixes it.")
    print()
    _table([("SESSION", "PROJECT", "STATE", "FEED", "DETAIL")] +
           [(x["id"][:8], x["project"], x["state"], "ok" if x["feed_ok"] else "unreadable", f"{x['title']} {x['detail']}")
            for x in s["sessions"]])
    _health(s.get("stats") or {})


def _health(st: dict) -> None:
    """Counters since the agent started: dropped presses, reconnects, how long the hooks took."""
    counts = st.get("counts") or {}
    print(f"\nHEALTH (agent up {_duration(st.get('uptime', 0))})")
    print(f"  keypad connections  {counts.get('keypad_connects', 0)} ({counts.get('keypad_disconnects', 0)} dropped)")
    print(f"  presses ignored     {counts.get('press_stale', 0)} stale, {counts.get('press_rejected', 0)} not offered, "
          f"{counts.get('press_dropped', 0)} lost")
    if hooks := st.get("hooks"):
        _table([("HOOK", "CALLS", "AVERAGE", "SLOWEST")] +
               [(e, str(h["n"]), f"{h['avg_ms']} ms", f"{h['max_ms']} ms") for e, h in hooks.items()])


def _duration(seconds: int) -> str:
    h, m = divmod(seconds // 60, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def _table(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    for r in rows:  # the last column isn't padded
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=False)) + "  " + r[-1])
