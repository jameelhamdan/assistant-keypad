"""Development helper: pairs the USB-connected keypad for Wi-Fi using WIFI_SSID and
WIFI_PASSWORD from the repo's .env (git-ignored), through the running Keypad agent.

    host/.venv/Scripts/python tools/dev_pair.py [device name]

Keypad (the tray, or `keypad agent`) must be running and the keypad plugged in."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from keypad import ipc  # noqa: E402


def load_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    env = load_env(Path(__file__).resolve().parents[1] / ".env")
    ssid, password = env.get("WIFI_SSID", ""), env.get("WIFI_PASSWORD", "")
    if not ssid:
        print("WIFI_SSID is missing in .env")
        return 1
    ipc.request("POST", "/pairing", {})  # open the USB port for pairing
    deadline = time.time() + 40
    usb = None
    while time.time() < deadline and not usb:
        usb = next((k for k in ipc.request("GET", "/status")["keypads"] if k["link"] == "usb"), None)
        time.sleep(1)
    if not usb:
        print("no keypad answered over USB: is it plugged in?")
        return 1
    name = " ".join(sys.argv[1:]) or usb.get("name") or usb["id"]
    ipc.request("POST", f"/devices/{usb['id']}/provision", {"ssid": ssid, "pass": password, "name": name}, timeout=30)
    print(f"{usb['id']} paired as {name!r} for Wi-Fi {ssid!r}; waiting for it to join...")
    deadline = time.time() + 60
    while time.time() < deadline:
        k = next((k for k in ipc.request("GET", "/status")["keypads"] if k["id"] == usb["id"] and k["link"] == "wifi"), None)
        if k:
            print(f"connected over Wi-Fi at {k['addr']}  battery={k.get('battery')}  wifi={k.get('wifi')}")
            return 0
        time.sleep(2)
    print("paired, but not seen over Wi-Fi yet (same network? 2.4 GHz?). Check the keypad's Wi-Fi bars.")
    return 2


sys.exit(main())
