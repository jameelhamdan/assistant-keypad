"""Where Keypad keeps its files and where its executables are. Standard library only, and
cheap to import: the hook shim needs data_dir() on every prompt and decision."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Keypad"


def data_dir() -> Path:
    """The per-user data directory (config, state, logs, agent.json)."""
    if d := os.environ.get("KEYPAD_HOME"):
        return Path(d)
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return Path(home, "Library", "Application Support", APP_NAME)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", home), APP_NAME)
    return Path(os.environ.get("XDG_CONFIG_HOME", os.path.join(home, ".config")), APP_NAME)


def self_path() -> str:
    """The console executable Claude Code hooks should run."""
    if getattr(sys, "frozen", False):
        p = Path(sys.executable).resolve()
    else:
        argv0 = Path(sys.argv[0])
        if argv0.name.lower().removesuffix(".exe") in ("keypad", "keypadw"):
            p = argv0.resolve()
        else:  # e.g. `python -m keypad`: the venv's entry point
            found = shutil.which("keypad", path=str(Path(sys.executable).parent))
            p = Path(found).resolve() if found else argv0.resolve()
    if p.name.lower() == "keypadw.exe":  # hooks must run the console build on Windows
        p = p.with_name("keypad.exe")
    return str(p)


def gui_path(binary: str) -> str:
    """The executable for background processes: on Windows the windowless
    keypadw.exe next to keypad.exe, when it exists."""
    if sys.platform == "win32":
        w = Path(binary).with_name("keypadw.exe")
        if w.exists():
            return str(w)
    return binary


def in_app_bundle() -> bool:
    return sys.platform == "darwin" and ".app/Contents/MacOS/" in self_path()


def env_without_bundle() -> dict[str, str]:
    """os.environ minus PyInstaller's library path, for spawning other programs."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        for k in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            if (orig := env.pop(k + "_ORIG", None)) is not None:
                env[k] = orig
            else:
                env.pop(k, None)
    return env
