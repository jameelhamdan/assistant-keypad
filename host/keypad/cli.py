"""The command-line commands (install, status, update...)."""

from __future__ import annotations

import os
import shutil
import time

from . import claudecfg, config, ipc, service
from .paths import self_path


def call(method: str, path: str, body=None, timeout: float = 30):
    try:
        return ipc.request(method, path, body, timeout=timeout)
    except ipc.AgentNotRunning:
        raise RuntimeError("Keypad is not running (start it from the tray, or `keypad tray`)") from None


def stop_agent() -> None:
    """Asks a running agent (possibly an older version) to exit."""
    if not ipc.alive():
        return
    try:
        ipc.request("POST", "/quit", timeout=5)
    except (ipc.AgentNotRunning, ipc.RequestError):
        pass
    for _ in range(30):
        if not ipc.alive():
            return
        time.sleep(0.1)


def cmd_install(args: list[str]) -> None:
    stop_agent()
    cfg, _ = config.load()
    claudecfg.install(self_path(), cfg.behavior.max_continues)
    print("Claude Code integration installed:", claudecfg.settings_path())
    service.install(self_path())
    print("Keypad starts at login and is running now.")
    print("Restart any open Claude Code sessions to pick up the hooks.")


def cmd_uninstall(args: list[str]) -> None:
    stop_agent()
    service.uninstall()
    claudecfg.uninstall()
    print("Removed the Claude Code integration and the login items.")
    if "--purge" in args:
        shutil.rmtree(config.data_dir(), ignore_errors=True)
        print("Also removed settings and pairing keys in", config.data_dir())
    else:
        print("Settings and pairing keys remain in", config.data_dir(), "(--purge removes them too)")


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
              "this one lost its key). Tray -> your keypad -> Set up Wi-Fi… (over USB) fixes it.")
    print()
    _table([("SESSION", "PROJECT", "STATE", "DETAIL")] +
           [(x["id"][:8], x["project"], x["state"], f"{x['title']} {x['detail']}") for x in s["sessions"]])


def _table(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]) - 1)]
    for r in rows:  # the last column isn't padded
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=False)) + "  " + r[-1])


def cmd_update(args: list[str]) -> None:
    if len(args) != 2:
        raise RuntimeError("usage: keypad update <keypad-id> <firmware.bin>")
    dev, path = args[0], os.path.abspath(args[1])
    call("POST", f"/devices/{dev}/update", {"path": path})
    while True:
        time.sleep(0.7)
        p = call("GET", f"/devices/{dev}/update")["progress"]
        if p < 0:
            raise RuntimeError("update failed (see the agent log)")
        print(f"\rupdating {dev}: {p:3d}%", end="", flush=True)
        if p >= 100:
            print("\ndone, the keypad restarts")
            return
