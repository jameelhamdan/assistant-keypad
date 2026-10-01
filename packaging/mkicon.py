"""Writes the app icon: the keypad's 2x4 keys on a dark rounded tile, top row
in Claude orange (the same picture as the tray icon).

    python packaging/mkicon.py dist/Keypad.iconset   # macOS: then iconutil -c icns
    python packaging/mkicon.py dist/keypad.ico       # Windows
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

TILE, ACCENT, DIM = (0x1E, 0x1E, 0x1E, 255), (0xD7, 0x77, 0x57, 255), (0x5A, 0x5A, 0x5A, 255)


def draw(n: int) -> Image.Image:
    scale = 4  # draw large, then downsample: smooth edges
    s = n * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m, t = s * 0.1, s * 0.8  # Apple's grid: the tile is ~80% of the canvas
    d.rounded_rectangle((m, m, m + t, m + t), radius=t * 0.22, fill=TILE)
    key, gap = t * 0.15, t * 0.05
    x0, y0 = m + (t - (4 * key + 3 * gap)) / 2, m + (t - (2 * key + gap)) / 2
    for row in range(2):
        for col in range(4):
            x, y = x0 + col * (key + gap), y0 + row * (key + gap)
            d.rounded_rectangle((x, y, x + key, y + key), radius=key * 0.2, fill=ACCENT if row == 0 else DIM)
    return img.resize((n, n), Image.LANCZOS)


def main() -> None:
    out = Path(sys.argv[1])
    if out.suffix == ".ico":
        out.parent.mkdir(parents=True, exist_ok=True)
        draw(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        return
    out.mkdir(parents=True, exist_ok=True)
    for pt in (16, 32, 128, 256, 512):
        draw(pt).save(out / f"icon_{pt}x{pt}.png")
        draw(pt * 2).save(out / f"icon_{pt}x{pt}@2x.png")


if __name__ == "__main__":
    main()
