# PyInstaller entry point for keypad-hook: Claude Code's command hook. Analysed
# on its own so it bundles nothing beyond the standard library and starts fast
# (it runs on every tool call).
import sys

from keypad.hook import run_hook

args = sys.argv[1:]
if args[:1] == ["hook"]:  # installed as `keypad-hook hook <Event>`, like `keypad hook <Event>`
    args = args[1:]
sys.exit(run_hook(args))
