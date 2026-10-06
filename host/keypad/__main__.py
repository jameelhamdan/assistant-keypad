"""keypad: the whole PC side of the hardware keypad in one program.

  keypad tray          tray icon and menu (all settings); it runs the agent that owns the keypads and answers hooks
  keypad agent         the agent alone, without a tray (headless)
  keypad hook <Event>  Claude Code command hook (installed into ~/.claude/settings.json)
  keypad install       connect Claude Code and start at login
  keypad uninstall [--purge]   remove both (--purge also deletes settings and pairing keys)
  keypad status        show keypads and sessions
  keypad update <keypad-id> <firmware.bin>
  keypad version

Imports are lazy: `keypad hook` runs on every Claude Code tool call and must
load nothing beyond the standard library.
"""

from __future__ import annotations

import sys

USAGE = """usage: keypad <command>

  tray                show the tray icon and run the agent (started at login)
  agent               run the agent without a tray
  install             connect Claude Code and start Keypad at login
  uninstall           undo install
  status              show keypads and Claude Code sessions
  update <id> <file>  update a keypad's firmware
  hook <Event>        (used by Claude Code)
  version
"""


def default_command() -> str:
    """What a double-click (no arguments) does."""
    return "tray" if sys.platform in ("darwin", "win32") else "help"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    args = [a for a in args if not a.startswith("-psn_")]  # older macOS adds this when the app is double-clicked
    args = args or [default_command()]
    cmd, rest = args[0], args[1:]
    if cmd == "hook":
        from .hook import run_hook

        return run_hook(rest)
    if cmd in ("version", "--version", "-v"):
        from .version import version

        print("keypad", version())
        return 0
    if cmd in ("help", "--help", "-h"):
        print(USAGE, end="", file=sys.stderr)
        return 0
    try:
        if cmd == "agent":
            from .agent_cmd import run_agent

            return run_agent(rest)
        elif cmd == "tray":
            from .tray import run_tray

            run_tray(quiet="--quiet" in rest)
        else:
            from . import cli

            fn = {"install": cli.cmd_install, "uninstall": cli.cmd_uninstall, "status": cli.cmd_status,
                  "update": cli.cmd_update}.get(cmd)
            if fn is None:
                print(USAGE, end="", file=sys.stderr)
                return 2
            fn(rest)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # CLI errors: one line, no traceback
        print(f"keypad: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
