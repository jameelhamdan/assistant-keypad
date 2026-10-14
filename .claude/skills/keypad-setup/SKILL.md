---
name: keypad-setup
description: Flash the keypad firmware and pair a USB-connected keypad with Wi-Fi from the command line, without the tray dialogs. Use when asked to set up, pair, provision or flash a keypad.
---

# Keypad setup without the UI

Run from the repo root on the machine the keypad is plugged into (USB-C data cable). Windows paths use `host/.venv/Scripts/`.

1. **Agent running.** `host/.venv/Scripts/keypad status` must work; if it says Keypad is not running, start `keypad tray` (or `keypad agent`) in the background first.
2. **Flash (only if the firmware must change).** `make flash`. If PlatformIO cannot download its upload tool (SSL), build with `make firmware` and write the images with the bundled esptool: bootloader `0x0`, partitions `0x8000`, `boot_app0.bin` `0xe000`, `firmware.bin` `0x10000` (from `firmware/.pio/build/keypad/` and `~/.platformio/packages`). Quit nothing else holds the port.
3. **Pair + Wi-Fi.** `host/.venv/Scripts/keypad setup` reads `WIFI_SSID` and `WIFI_PASSWORD` from the repo's git-ignored `.env` (or the environment as `KEYPAD_WIFI_SSID` / `KEYPAD_WIFI_PASS`, `KEYPAD_NAME` for the name). Never print or echo them. If neither exists, ask the user and use `--ssid '<network>'` with the env var (not `--pass`: command lines are visible to other processes).
   `--ssid` falls back to the network this PC is on; the name to "Desk keypad".
4. **Verify.** `keypad status` shows the keypad with a `wifi` connection and "paired here: true".

The Wi-Fi must be 2.4 GHz. A keypad that is "paired but not connected" usually has a wrong name/password; run `keypad setup` again.
