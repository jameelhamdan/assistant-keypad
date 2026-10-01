"""The app version: release builds write it to keypad/data/version."""

from __future__ import annotations

from importlib import resources


def version() -> str:
    try:
        v = resources.files("keypad").joinpath("data", "version").read_text().strip()
    except (OSError, FileNotFoundError):
        v = ""
    return v or "dev"
