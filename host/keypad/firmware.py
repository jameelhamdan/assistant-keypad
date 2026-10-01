"""The bundled keypad firmware, so the tray can update keypads over USB or
Wi-Fi. Release builds copy the PlatformIO output to keypad/data/keypad.bin
and its version to keypad/data/firmware-version; development builds have none."""

from __future__ import annotations

from importlib import resources


def _data(name: str) -> bytes | None:
    try:
        b = resources.files("keypad").joinpath("data", name).read_bytes()
    except (OSError, FileNotFoundError):
        return None
    return b or None


def image() -> bytes | None:
    return _data("keypad.bin")


def version() -> str:
    v = _data("firmware-version")
    return v.decode().strip() if v else "dev"
