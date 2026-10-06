"""The key diagram in docs/HARDWARE.md must match the firmware's key map."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LABELS = {1: "[1]", 2: "[2]", 3: "[3]", 4: "[4 up]", 5: "[5]", 6: "[6]", 7: "[7]", 8: "[8 down]"}


def key_map() -> list[list[int]]:
    cfg = (ROOT / "firmware" / "include" / "config.h").read_text(encoding="utf-8")
    body = re.search(r"KEY_MAP\[NUM_ROWS\]\[NUM_COLS\] = \{(.*?)\};", cfg, re.S).group(1)
    return [[int(n) for n in row.split(",")] for row in re.findall(r"\{([\d, ]+)\}", body)]


def test_hardware_diagram_matches_the_key_map():
    doc = (ROOT / "docs" / "HARDWARE.md").read_text(encoding="utf-8")
    diagram = doc.split("```")[1]
    rows = [ln.strip() for ln in diagram.splitlines() if ln.strip().startswith("[")]
    want = ["    ".join(LABELS[k] for k in row) for row in key_map()]
    assert [re.sub(r"\s+", " ", r) for r in rows] == [re.sub(r"\s+", " ", w) for w in want]


def test_knob_is_left_of_the_screen_and_the_cable_leaves_on_the_right():
    diagram = (ROOT / "docs" / "HARDWARE.md").read_text(encoding="utf-8").split("```")[1]
    knob_line = next(ln for ln in diagram.splitlines() if "(   o   )" in ln)
    cable_line = next(ln for ln in diagram.splitlines() if "USB-C cable" in ln)
    screen_left = next(ln for ln in diagram.splitlines() if "+---" in ln).index("+")
    assert knob_line.index("(") < screen_left, "the knob sits left of the screen"
    assert cable_line.index("USB-C") > cable_line.rindex("|"), "the cable leaves from the right edge"
