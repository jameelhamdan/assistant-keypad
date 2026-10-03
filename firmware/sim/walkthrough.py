"""Drives the simulator through the user flows and writes contact sheets to out/walk-*.png for review.
    python firmware/sim/walkthrough.py"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
import scenarios as sc  # noqa: E402
from keypad_sim import Sim  # noqa: E402

out = Path(__file__).parent / "out"
sheets: dict[str, list[tuple[str, Image.Image]]] = {}
cur: list = []


def sheet(name):
    global cur
    cur = sheets.setdefault(name, [])


def snap(kp, label):
    cur.append((label, kp.image()))


LONG_LOG = [
    {"k": "u", "t": "refactor the billing module so invoices are generated per customer and add retries for failed payments"},
    {"k": "c", "t": "\u0001Plan\u0001\nI'll read the billing code first, then split `generate_invoice` into smaller functions and add a retry wrapper."},
    {"k": "t", "t": "Read(billing/invoices.py)"}, {"k": "r", "t": "412 lines"},
    {"k": "t", "t": "Grep(generate_invoice)"}, {"k": "r", "t": "9 matches in 4 files"},
    {"k": "c", "t": "Found it. The function mixes data loading, PDF rendering and emailing, so I'll extract each part."},
    {"k": "t", "t": "Edit(billing/invoices.py)"}, {"k": "r", "t": "Updated billing/invoices.py with 38 additions and 71 removals"},
    {"k": "t", "t": "Bash(pytest tests/billing -q)"}, {"k": "r", "t": "2 failed, 31 passed in 4.2s"},
    {"k": "c", "t": "Two tests fail because they patch the old function name. Fixing those next."},
]
MANY = [dict(sc.SESSION, id=f"s000000{i}", project=p, name=n, state=st, since=sn, mode=m) for i, (p, n, st, sn, m) in enumerate([
    ("money-mind", "Fix tests", "working", 72, "auto"),
    ("api-gateway-service", "Add rate limiting to the public endpoints", "permission", 15, "default"),
    ("docs", "", "idle", 400, "plan"),
    ("mobile-app", "Upgrade navigation", "working", 9, "acceptEdits"),
    ("infra", "Terraform drift", "idle", 1200, "default")])]
TEN = {"t": "screen", "id": "q-10", "tpl": "select", "title": "Framework", "q": "Which framework should the new service use?",
       "project": "api", "esc": "pc", "items": ["FastAPI", "Django", "Flask", "Starlette", "Tornado", "aiohttp", "Sanic",
                                                  "Litestar", "Falcon", "Bottle"]}
LONG_CMD = dict(sc.SCREENS["permission"], id="p-long", body="Rebuild the docker image and push it to the registry\n" + "docker build -t registry.example.com/team/billing-service:2026.10.3 --build-arg ENV=production . && docker push registry.example.com/team/billing-service:2026.10.3 && kubectl rollout restart deployment/billing-service -n production")
LONG_DIFF = dict(sc.SCREENS["diff"], id="p-ldiff", body="billing/invoices.py\n" + "\n".join(
    [f"-    line_{i} = old_value_{i}" if i % 2 else f"+    line_{i} = new_value_{i}" for i in range(14)]))
FINISHED_LONG = dict(sc.SCREENS["finished"], id="s-long", items=["continue", "Run tests", "Commit", "Explain the change", "Write tests"],
                     notes=["keep going", "pytest -q", "git commit", "Explain what you changed.", "Add tests."])

with Sim() as kp:
    sheet("1-main")
    snap(kp, "boot done, no host yet (waiting)")
    kp.msg(sc.HELLO); kp.msg(sc.SETTINGS)
    kp.msg({"t": "status", "sessions": [], "sel": "", "pinned": False, "queue": 0, "paused": False, "menu": False})
    snap(kp, "host connected, no sessions")
    kp.msg(sc.status(log=LONG_LOG, mode="auto")); snap(kp, "long transcript (newest at the bottom)")
    kp.key(4); snap(kp, "key 4: scroll back one page?")
    kp.key(4); kp.key(4); snap(kp, "scrolled further")
    kp.key(5); snap(kp, "key 5: back to newest")
    kp.msg(sc.status(log=LONG_LOG, state="permission", queue=2)); snap(kp, "asking + 2 queued")
    kp.msg(sc.status(log=LONG_LOG, paused=True)); snap(kp, "paused")
    kp.msg(sc.status(log=[], mode="plan")); snap(kp, "session with no transcript yet")
    kp.msg({"t": "toast", "text": "Queued for api: Write tests", "level": "info", "ms": 2500}); snap(kp, "toast")

    sheet("2-sessions")
    kp.msg({**sc.status(sessions=MANY, log=LONG_LOG), "sel": "s0000000"}); snap(kp, "5 sessions, header 1/5")
    kp.key(6); snap(kp, "key 6: session list")
    kp.key(8); kp.key(8); snap(kp, "cursor on 2nd session")
    kp.key(7); snap(kp, "7: selected, back on main")
    kp.key(6); kp.key(4); kp.key(7); snap(kp, "follow latest again")
    for _ in range(3):
        kp.key(6); kp.key(8)
    snap(kp, "list, cursor lower down")
    kp.key(5)

    sheet("3-permission")
    kp.msg(sc.status()); kp.msg(sc.SCREENS["permission"]); snap(kp, "permission")
    kp.key(8); snap(kp, "8: cursor to option 2")
    kp.key(8); kp.key(8); snap(kp, "8 x2: bottom (wraps? stops?)")
    kp.msg({"t": "close", "id": "p-1", "why": "answered"})
    kp.msg(LONG_CMD); snap(kp, "long command")
    kp.key(4); snap(kp, "4 on first option: scroll command")
    kp.key(4); snap(kp, "4 again")
    kp.key(8); snap(kp, "8: back toward options")
    kp.msg({"t": "close", "id": "p-long", "why": "answered"})
    kp.msg(LONG_DIFF); snap(kp, "long diff")
    kp.key(4); snap(kp, "diff scrolled")
    kp.msg({"t": "close", "id": "p-ldiff", "why": "answered"})
    kp.msg({**sc.SCREENS["permission"], "id": "p-q", "title": "Bash command"}); snap(kp, "countdown + queue (no +n)")

    sheet("4-questions")
    kp.msg({"t": "close", "id": "p-q", "why": "answered"})
    kp.msg(TEN); snap(kp, "10 options")
    for _ in range(5):
        kp.key(8)
    snap(kp, "cursor on 6")
    for _ in range(4):
        kp.key(8)
    snap(kp, "cursor on 10")
    kp.msg({"t": "close", "id": "q-10", "why": "answered"})
    kp.msg(sc.SCREENS["multi"]); snap(kp, "multi")
    kp.key(1); kp.key(3); snap(kp, "ticked 1 and 3")
    kp.key(8); kp.key(8); kp.key(8); snap(kp, "cursor on Submit")
    kp.msg({"t": "close", "id": "q-2", "why": "answered"})
    kp.msg(FINISHED_LONG); snap(kp, "Claude finished, 5 options")
    kp.key(8); kp.key(8); kp.key(8); kp.key(8); snap(kp, "cursor on last option")

    sheet("5-states")
    kp.msg({"t": "close", "id": "s-long", "why": "answered"})
    kp.msg(sc.status(log=LONG_LOG)); kp.wait(61000); snap(kp, "after 61 s idle (dimmed?)")
    kp.key(7); snap(kp, "first key press only wakes")
    kp.msg(sc.SCREENS["permission"]); kp.wait(1000)
    snap(kp, "request wakes the screen")
    kp.msg({**sc.SETTINGS, "theme": "light"}); kp.msg({"t": "close", "id": "p-1", "why": "answered"})
    kp.msg(sc.status(log=LONG_LOG, mode="plan")); snap(kp, "light theme: main")
    kp.msg(sc.SCREENS["permission"]); snap(kp, "light theme: permission")
    kp.msg(sc.SCREENS["diff"]); snap(kp, "light theme: diff")
    kp.msg({"t": "close", "id": "p-2", "why": "answered"}); kp.msg({**sc.SETTINGS, "theme": "dark"})
    kp.msg({"t": "ping"})
    kp.wait(7000); snap(kp, "host silent 7 s")
    kp.hold(1, 1700); snap(kp, "hold 1: key test?")

w, h, pad, cols = 640, 340, 26, 2
for name, tiles in sheets.items():
    rows = (len(tiles) + cols - 1) // cols
    img = Image.new("RGB", (cols * (w + 10) + 10, rows * (h + pad + 10) + 10), (40, 40, 44))
    d = ImageDraw.Draw(img)
    for i, (label, im) in enumerate(tiles):
        x, y = 10 + (i % cols) * (w + 10), 10 + (i // cols) * (h + pad + 10)
        d.text((x, y + 6), f"{i + 1}. {label}", fill=(235, 235, 235))
        img.paste(im.resize((w, h), Image.NEAREST), (x, y + pad))
    img.save(out / f"walk-{name}.png")
    print("wrote", out / f"walk-{name}.png", len(tiles))
