"""The command-line commands (install, status, update...)."""

from __future__ import annotations

import shutil

from . import claudecfg, config, ipc, service
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
