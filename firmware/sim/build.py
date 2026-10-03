"""Builds the keypad simulator (firmware/sim/out/keypad-sim). Needs a C++ compiler: `ziglang` from pip
(python -m pip install ziglang) works anywhere, or set CXX/CC to g++/gcc. The libraries come from the
PlatformIO dependency folder, so run `pio run -e keypad` once first.  Usage: python sim/build.py"""
import os, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

here = Path(__file__).resolve().parent
fw = here.parent
libs = fw / ".pio" / "libdeps" / "keypad"
gfx = libs / "GFX Library for Arduino" / "src"
u8 = libs / "U8g2" / "src" / "clib"
out = here / "out"
out.mkdir(exist_ok=True)
zig = [sys.executable, "-m", "ziglang"]
cxx = os.environ.get("CXX", "").split() or zig + ["c++"]
cc = os.environ.get("CC", "").split() or zig + ["cc"]
inc = [f"-I{here / 'shim'}", f"-I{fw / 'src'}", f"-I{fw / 'include'}", f"-I{gfx}", f"-I{u8}", f"-I{libs / 'ArduinoJson' / 'src'}"]
flags = ["-O1", "-w", "-DNATIVE_TEST", "-DKEYPAD_FW_VERSION=\"sim\""]

cpp = [here / "sim.cpp", fw / "src" / "app.cpp", fw / "src" / "ui.cpp", *sorted((fw / "src" / "text").glob("*.cpp")),
       gfx / "Arduino_GFX.cpp", gfx / "Arduino_G.cpp", gfx / "Arduino_DataBus.cpp", gfx / "canvas" / "Arduino_Canvas.cpp"]
csrc = [u8 / "u8g2_fonts.c"]

def run(cmd, src, obj):
    if obj.exists() and obj.stat().st_mtime > max(src.stat().st_mtime, Path(__file__).stat().st_mtime):
        return obj
    r = subprocess.run(cmd + ["-c", str(src), "-o", str(obj)], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"{src.name}:\n{r.stderr[-3000:]}")
    return obj

with ThreadPoolExecutor(os.cpu_count()) as ex:
    objs = [ex.submit(run, cxx + ["-std=gnu++20"] + flags + inc, s, out / (s.stem + ".o")) for s in cpp]
    objs += [ex.submit(run, cc + flags + inc, s, out / (s.stem + ".c.o")) for s in csrc]
    objs = [o.result() for o in objs]
exe = out / ("keypad-sim.exe" if os.name == "nt" else "keypad-sim")
r = subprocess.run(cxx + [str(o) for o in objs] + ["-o", str(exe)], capture_output=True, text=True)
if r.returncode:
    sys.exit(r.stderr[-3000:])
print("built", exe)
