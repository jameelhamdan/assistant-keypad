# PyInstaller entry point: the same as `python -m keypad`.
import sys

from keypad.__main__ import main

sys.exit(main())
