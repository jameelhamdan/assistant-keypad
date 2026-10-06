"""Keypad wire protocol v3 (see proto/PROTOCOL.md).

Host -> device messages are plain dicts built by the helpers below;
device -> host messages are validated by decode().
"""

from __future__ import annotations

import json
from typing import Any

VERSION = 3
TCP_PORT = 7470
MDNS_SERVICE = "_ckeypad._tcp.local."
USB_VENDOR_ID = 0x303A  # Espressif native USB

MAX_HOST_MSG = 16000  # the keypad takes 16 KB (whole Claude messages)
MAX_DEVICE_MSG = 1024
MAX_ITEMS = 32  # options on one screen; the cursor scrolls through them

# The keypad's fixed layout (firmware/include/config.h):  1 2 3 [4 up]
#                                                         [5 esc] [6 sessions] [7 enter] [8 down]
KEY_ESC, KEY_ENTER = 5, 7  # the knob press is sent as Enter (7): the keypad never sends key 0
ESC_KEYS = (KEY_ESC,)
DIRECT_PICKS = 3  # keys 1-3 pick options 1-3

DEVICE_TYPES = {"hello", "pong", "press", "session", "provisioned", "ota"}


# Text buffers on the keypad (firmware/src/model.h), in bytes without the NUL.
# Text is fitted to these so it is never cut inside a UTF-8 character there.
SESSION_PROJECT, SESSION_NAME, SESSION_TITLE, SESSION_DETAIL, SESSION_MODE = 39, 39, 63, 239, 23
LOG_TEXT, LOG_USER_TEXT, LOG_CLAUDE_TEXT = 203, 2000, 8000  # per transcript entry, by kind
LOG_POOL = 12000  # all transcript text together (the keypad's buffer is 12288 bytes)
SCREEN_TITLE, SCREEN_PROJECT, SCREEN_BODY, SCREEN_Q, SCREEN_ITEM, SCREEN_NOTE = 127, 39, 1399, 127, 63, 47
TOAST_TEXT, DEVICE_NAME, HOST_NAME = 127, 24, 31


def fit(s: str, nbytes: int) -> str:
    """s shortened to at most nbytes of UTF-8, whole characters only, with an
    ellipsis marking a cut."""
    if len(s.encode()) <= nbytes:
        return s
    b = s.encode()[: max(0, nbytes - 3)]  # room for "…" (3 bytes)
    return b.decode(errors="ignore") + "…"


def fit_screen(s: dict[str, Any]) -> dict[str, Any]:
    """A screen message with every text field fitted to the keypad's buffers."""
    s = dict(s)
    for k, n in (("title", SCREEN_TITLE), ("project", SCREEN_PROJECT), ("body", SCREEN_BODY), ("q", SCREEN_Q)):
        if isinstance(s.get(k), str):
            s[k] = fit(s[k], n)
    for k, n in (("items", SCREEN_ITEM), ("notes", SCREEN_NOTE)):
        if isinstance(s.get(k), list):
            s[k] = [fit(str(x), n) for x in s[k][:MAX_ITEMS]]
    return s


class InvalidMessage(ValueError):
    pass


def encode(msg: dict[str, Any]) -> bytes:
    """Marshals a host message and enforces the size limit."""
    b = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode()
    if len(b) > MAX_HOST_MSG:
        raise InvalidMessage(f"message too large ({len(b)} bytes)")
    return b


def decode(b: bytes) -> dict[str, Any]:
    """Parses and validates a device message."""
    if not b or len(b) > MAX_DEVICE_MSG:
        raise InvalidMessage("bad size")
    try:
        m = json.loads(b)
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidMessage("not JSON") from e
    if not isinstance(m, dict) or m.get("t") not in DEVICE_TYPES:
        raise InvalidMessage("unknown type")
    key = m.get("key", 0)
    sel = m.get("sel", [])
    if (
        len(str(m.get("id", ""))) > 48
        or len(str(m.get("act", ""))) > 24
        or len(str(m.get("sid", ""))) > 64
        or not isinstance(key, int)
        or not 0 <= key <= 8
        or not isinstance(sel, list)
        or len(sel) > MAX_ITEMS
        or not all(isinstance(i, int) for i in sel)
    ):
        raise InvalidMessage("field out of range")
    idx = m.get("idx")
    if idx is not None and not isinstance(idx, int):
        raise InvalidMessage("bad idx")
    return m
