"""Drive the keypad simulator from Python.

    from keypad_sim import Sim
    with Sim() as kp:
        kp.msg({"t": "status", ...})   # a host message
        kp.key(7)                      # press key 7
        kp.shot("out/main.png")        # 3x PNG of the screen
        kp.tx                          # what the keypad sent to the host (list of dicts)
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
EXE = HERE / "out" / ("keypad-sim.exe" if os.name == "nt" else "keypad-sim")


class Sim:
    def __init__(self, exe: Path = EXE):
        if not exe.exists():
            raise SystemExit("build the simulator first: python firmware/sim/build.py")
        self.p = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
                                  encoding="utf-8", errors="replace")
        self.tx: list[dict] = []
        fd, self._frame_path = tempfile.mkstemp(suffix=".ppm", prefix="keypad-sim-")   # one per instance
        os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        try:
            self.p.stdin.write("quit\n")
            self.p.stdin.flush()
        except OSError:
            pass
        self.p.wait(timeout=5)
        try:
            os.remove(self._frame_path)
        except OSError:
            pass

    def _cmd(self, line: str, wait_for: str = ""):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()
        if wait_for:
            while True:
                out = self.p.stdout.readline()
                if not out:
                    raise RuntimeError("simulator exited")
                if out.startswith("TX "):
                    self.tx.append(json.loads(out[3:]))
                elif out.startswith(wait_for):
                    return
        # no marker for ordinary commands: a `ping` shot keeps stdout in step
        self._sync()

    def _sync(self):
        tmp = self._frame_path
        self.p.stdin.write(f"shot {tmp}\n")
        self.p.stdin.flush()
        while True:
            out = self.p.stdout.readline()
            if not out:
                raise RuntimeError("simulator exited")
            if out.startswith("TX "):
                self.tx.append(json.loads(out[3:]))
            elif out.startswith("SHOT"):
                self.frame = tmp
                return

    def msg(self, m: dict):
        """Sends a host message. A status carrying a "log" is sent the way the host does: status, then the feed."""
        if m.get("t") == "status" and "log" in m:
            m = dict(m)
            log = m.pop("log")
            self.msg(m)
            if m.get("sel"):
                self.msg({"t": "feed", "sid": m["sel"], "full": log})
            return
        self._cmd("msg " + json.dumps(m, ensure_ascii=False, separators=(",", ":")))

    def key(self, k: int, hold_ms: int = 120):
        self._cmd(f"key {k} {hold_ms}")

    def hold(self, k: int, ms: int):
        self._cmd(f"key {k} {ms}")

    def host(self, alive: bool):
        """The simulated host pings every 2 s while alive; silence it to see the keypad lose the host."""
        self._cmd("host " + ("on" if alive else "off"))

    def backlight(self) -> int:
        """The backlight percent the keypad would set right now (dimmed when idle on battery)."""
        self.p.stdin.write("backlight\n")
        self.p.stdin.flush()
        while True:
            out = self.p.stdout.readline()
            if not out:
                raise RuntimeError("simulator exited")
            if out.startswith("TX "):
                self.tx.append(json.loads(out[3:]))
            elif out.startswith("BACKLIGHT "):
                return int(out.split()[1])

    def power(self, source: str):
        """"usb" (a wire: the screen never dims) or "battery" (it dims when idle)."""
        self._cmd("power " + source)

    def turn(self, steps: int):
        self._cmd(f"turn {steps}")

    def spin(self, detents: int, gap_ms: int):
        """Turns the knob one detent at a time, gap_ms apart (+ clockwise): a short gap is a fast hand."""
        self._cmd(f"spin {detents} {gap_ms}")

    def wait(self, ms: int):
        self._cmd(f"wait {ms}")

    def image(self) -> Image.Image:
        self._sync()
        return Image.open(self.frame).convert("RGB")

    def shot(self, path: str | Path, scale: int = 3) -> Path:
        im = self.image()
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        im.save(path)
        return Path(path)
