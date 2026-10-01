"""Where Keypad keeps its files. Standard library only, and cheap to import:
the hook shim needs data_dir() on every tool call."""

from __future__ import annotations

import os
import sys

APP_NAME = "Keypad"


def data_dir() -> str:
    """The per-user data directory (config, state, logs, socket)."""
    if d := os.environ.get("KEYPAD_HOME"):
        return d
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", APP_NAME)
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", home), APP_NAME)
    return os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config")), APP_NAME)
