# PyInstaller spec: one folder (fast start: the hook runs for every prompt, permission
# and stop, and a one-file build would unpack itself each time).
#   macOS:   dist/Keypad.app            (pyinstaller packaging/keypad.spec)
#   Windows: dist/Keypad/keypad.exe     console build: Claude Code's hook, CLI
#            dist/Keypad/keypadw.exe    windowless build: the tray (it runs the agent)
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
HOST = os.path.join(ROOT, "host")
VERSION = os.environ.get("KEYPAD_VERSION", "dev").lstrip("v")
ICON = os.environ.get("KEYPAD_ICON")  # .icns / .ico made by packaging/mkicon.py

# keypad.device.fake is the development keypad that answers requests by itself: never shipped
hidden = [m for m in collect_submodules("keypad") if m != "keypad.device.fake"] + collect_submodules("zeroconf") + ["pystray._darwin" if sys.platform == "darwin" else "pystray._win32"]
datas = [(os.path.join(HOST, "keypad", "data"), "keypad/data")]

a = Analysis([os.path.join(SPECPATH, "entry.py")], pathex=[HOST], datas=datas, hiddenimports=hidden,
             excludes=["tkinter", "pytest", "setuptools", "pkg_resources", "keypad.device.fake"], noarchive=False)
pyz = PYZ(a.pure)

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
exes = [EXE(pyz, a.scripts, name="keypad", console=sys.platform == "win32", **common)]
if sys.platform == "win32":
    exes.append(EXE(pyz, a.scripts, name="keypadw", console=False, **common))

coll = COLLECT(*exes, a.binaries, a.datas, name="Keypad", strip=False, upx=False)

if sys.platform == "darwin":
    app = BUNDLE(coll, name="Keypad.app", icon=ICON, bundle_identifier="com.jameelhamdan.keypad", info_plist={
        "CFBundleName": "Keypad", "CFBundleDisplayName": "Keypad",
        "CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "12.0", "LSUIElement": True, "NSHighResolutionCapable": True,
        "NSLocalNetworkUsageDescription": "Keypad connects to your hardware keypad over the local network.",
        "NSBonjourServices": ["_ckeypad._tcp"],
    })
