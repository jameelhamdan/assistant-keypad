"""The tray icon: a keypad drawn in the color of what the sessions are doing."""

from __future__ import annotations

# The keypad's palette: Claude orange = working, permission blue = waiting
# on you, green = idle, grey = paused or no keypad.
COL_IDLE = (0x4E, 0xBA, 0x65)
COL_WORKING = (0xD7, 0x77, 0x57)
COL_WAITING = (0x57, 0x69, 0xF7)
COL_OFF = (0x8A, 0x93, 0xA0)


def icon_image(rgb: tuple[int, int, int], mode: str = "sessions"):
    """The keypad: 2 rows x 4 keys. Three looks, readable at 16px:
    "sessions" - filled, top row in the state color (sessions are mirrored);
    "ready"    - outlined keys: a keypad is connected but there are no sessions;
    "off"      - dim keys with a slash: no keypad (or paused)."""
    from PIL import Image, ImageDraw

    s, key, gap = 64, 12, 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0 = (s - (4 * key + 3 * gap)) // 2
    y0 = (s - (2 * key + gap)) // 2
    for row in range(2):
        for col in range(4):
            x, y = x0 + col * (key + gap), y0 + row * (key + gap)
            box = (x, y, x + key - 1, y + key - 1)
            if mode == "ready":
                d.rounded_rectangle(box, radius=3, outline=(*rgb, 255), width=2)
            elif mode == "off":
                d.rounded_rectangle(box, radius=3, fill=(*rgb, 110))
            else:
                d.rounded_rectangle(box, radius=3, fill=(*rgb, 255 if row == 0 else 150))
    if mode == "off":
        d.line((x0 - 2, y0 + 2 * key + gap + 4, x0 + 4 * key + 3 * gap + 2, y0 - 4), fill=(*rgb, 255), width=4)
    return img


def tray_look(s: dict) -> tuple[str, tuple[int, int, int]]:
    """The icon's three states: no keypad (or paused) -> "off"; a keypad
    but no sessions -> "ready"; sessions -> colored by what they're doing."""
    sessions = s.get("sessions", [])
    if s.get("paused") or not s.get("keypads"):
        return "off", COL_OFF
    if not sessions:
        return "ready", COL_IDLE
    phases = {x.get("state") for x in sessions}
    if s.get("busy") or phases & {"asking", "stopped"}:
        return "sessions", COL_WAITING
    if phases & {"working", "continuing"}:
        return "sessions", COL_WORKING
    return "sessions", COL_IDLE
