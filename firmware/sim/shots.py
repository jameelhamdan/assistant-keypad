"""Renders every screen and every permission mode into out/gallery.png (contact sheet), no hardware needed.
    python firmware/sim/shots.py"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
import scenarios as sc  # noqa: E402
from keypad_sim import Sim  # noqa: E402

out = Path(__file__).parent / "out"
tiles: list[tuple[str, Image.Image]] = []
with Sim() as kp:
    kp.msg(sc.HELLO); kp.msg(sc.SETTINGS)
    kp.msg({"t": "status", "sessions": [], "sel": "", "queue": 0, "paused": False, "menu": False})
    tiles.append(("no sessions", kp.image()))
    quick = {"menu": True, "quick": ["Tests", "Commit", "Review", "Explain", "Summary"]}
    for mode in sc.MODES:
        kp.msg({**sc.status(mode=mode), **(quick if mode == "default" else {})}); tiles.append((f"mode {mode}", kp.image()))
    kp.msg({**sc.status(state="idle"), **quick, "queued": "Commit"}); tiles.append(("a saved prompt queued", kp.image()))
    kp.msg(sc.status(state="asking", queue=2)); tiles.append(("asking, 2 queued", kp.image()))
    kp.msg(sc.status(paused=True)); tiles.append(("paused", kp.image()))
    kp.msg(sc.status())
    for name, scr in sc.SCREENS.items():
        kp.msg(scr); tiles.append((name, kp.image()))
        kp.msg({"t": "close", "id": scr["id"], "why": "answered"})
    kp.msg({**sc.status(menu=True), "quick": ["Tests", "Commit", "Review"]})
    kp.key(6); tiles.append(("sessions list (key 6)", kp.image()))
w, h, pad, cols = 320 * 2, 170 * 2, 28, 3
rows = (len(tiles) + cols - 1) // cols
sheet = Image.new("RGB", (cols * (w + 12) + 12, rows * (h + pad + 12) + 12), (40, 40, 44))
d = ImageDraw.Draw(sheet)
for i, (name, im) in enumerate(tiles):
    x, y = 12 + (i % cols) * (w + 12), 12 + (i // cols) * (h + pad + 12)
    d.text((x, y + 6), name, fill=(230, 230, 230))
    sheet.paste(im.resize((w, h), Image.NEAREST), (x, y + pad))
sheet.save(out / "gallery.png")
print("wrote", out / "gallery.png", len(tiles), "screens")
