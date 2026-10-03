# PyInstaller spec: one folder (fast start: the hook runs on every Claude Code
# tool call, and a one-file build would unpack itself each time).
#   macOS:   dist/Keypad.app            (pyinstaller packaging/keypad.spec)
#   Windows: dist/Keypad/keypad.exe     console build: MCP server, CLI
#            dist/Keypad/keypadw.exe    windowless build: agent and tray
#   both:    keypad-hook(.exe)          Claude Code's command hook, standard library only
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
HOST = os.path.join(ROOT, "host")
VERSION = os.environ.get("KEYPAD_VERSION", "dev").lstrip("v")
ICON = os.environ.get("KEYPAD_ICON")  # .icns / .ico made by packaging/mkicon.py

hidden = collect_submodules("keypad") + collect_submodules("zeroconf") + ["pystray._darwin" if sys.platform == "darwin" else "pystray._win32"]
datas = [(os.path.join(HOST, "keypad", "data"), "keypad/data")]

a = Analysis([os.path.join(SPECPATH, "entry.py")], pathex=[HOST], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "pytest", "setuptools", "pkg_resources"], noarchive=False)
pyz = PYZ(a.pure)

# keypad-hook: the command hook alone, standard library only, so its start is
# quick. It shares the bundle's Python runtime.
h = Analysis([os.path.join(SPECPATH, "hook_entry.py")], pathex=[HOST], hiddenimports=[],
             excludes=["tkinter", "pytest", "setuptools", "pkg_resources", "multiprocessing",
                       "cryptography", "serial", "zeroconf", "yaml", "PIL", "pystray", "mcp", "pydantic", "AppKit",
                       "objc", "inspect", "pkgutil", "unittest", "email", "http", "xml", "asyncio"],
             noarchive=False)
hpyz = PYZ(h.pure)

common = dict(debug=False, strip=False, upx=False, icon=ICON, exclude_binaries=True,
              target_arch=os.environ.get("KEYPAD_ARCH") or None)
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo

    parts = [int(p) for p in VERSION.split(".") if p.isdigit()][:4]
    parts += [0] * (4 - len(parts))
    common["version"] = VSVersionInfo(
        ffi=FixedFileInfo(filevers=tuple(parts), prodvers=tuple(parts)),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Jameel Hamdan"), StringStruct("FileDescription", "Keypad"),
            StringStruct("FileVersion", VERSION), StringStruct("ProductName", "Keypad"),
            StringStruct("ProductVersion", VERSION), StringStruct("OriginalFilename", "keypad.exe"),
        ])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])])
exes = [EXE(pyz, a.scripts, name="keypad", console=sys.platform == "win32", **common),
        EXE(hpyz, h.scripts, name="keypad-hook", console=True, **common)]
if sys.platform == "win32":
    exes.append(EXE(pyz, a.scripts, name="keypadw", console=False, **common))

coll = COLLECT(*exes, a.binaries, a.datas, h.binaries, h.datas, name="Keypad", strip=False, upx=False)

if sys.platform == "darwin":
    app = BUNDLE(coll, name="Keypad.app", icon=ICON, bundle_identifier="com.jameelhamdan.keypad", info_plist={
        "CFBundleName": "Keypad", "CFBundleDisplayName": "Keypad",
        "CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0", "LSUIElement": True, "NSHighResolutionCapable": True,
        "NSLocalNetworkUsageDescription": "Keypad connects to your hardware keypad over the local network.",
        "NSBonjourServices": ["_ckeypad._tcp"],
    })
