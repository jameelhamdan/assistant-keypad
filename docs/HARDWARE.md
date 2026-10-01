# Hardware

LILYGO **T-Display S3** (ESP32-S3, 16 MB flash, 8 MB PSRAM, 1.9" 170×320 ST7789 IPS on an 8-bit parallel bus), a 2×4 key matrix and a rotary encoder with a push switch. This is the same hardware as the original Jameel project; its pin map was confirmed on the real board.

## Pins

| GPIO | Use |
|---|---|
| 17, 16 | matrix rows (top, bottom): outputs, driven LOW one at a time |
| 43, 44, 18, 21 | matrix columns: `INPUT_PULLUP` |
| 1 | encoder switch |
| 2, 3 | encoder DT, CLK (interrupts) |
| 15, 38 | LCD power, backlight (PWM brightness) |
| 5, 6, 7, 8, 9 | LCD RST, CS, DC, WR, RD |
| 39–42, 45–48 | LCD D0–D7 |
| 4 | battery voltage (1:2 divider) |
| 19, 20 | native USB, the serial link |

GPIO43/44 are UART0 when "USB CDC on boot" is off, so the firmware refuses to build without `ARDUINO_USB_CDC_ON_BOOT=1` (set in `platformio.ini`).

## Keys

```
             GPIO43  GPIO44  GPIO18  GPIO21
GPIO17 (top)    1       2       3       4
GPIO16 (bot)    5       6       7       8
```

The layout is fixed: 1–3 pick options, 4 up, 8 down, 5 Esc, 6 sessions, 7 Enter; the encoder turns like 4/8 and clicks like 5 (see [the protocol](../proto/PROTOCOL.md#keys)). The encoder switch is key 0 in the protocol. The matrix has no diodes, so three held keys can ghost a fourth. Decision screens therefore accept only *clean* presses, with no other key held.

- Debounce: 30 ms.
- The encoder uses a full-cycle state table, one step per detent.

## Flashing

```sh
cd firmware
pio run -t upload            # the Keypad agent must not hold the port: quit it from the tray
pio device monitor           # JSON lines from the keypad
```

If the port doesn't appear: hold **BOOT**, press **RST**, release **BOOT**, then upload again. After flashing, press **RST** once if the keypad doesn't show up.

Later updates go over the protocol itself (USB or Wi-Fi) from the tray menu (your keypad → *Update firmware*), or `keypad update <id> firmware.bin`. The image is written to the idle OTA slot and MD5-checked before the keypad reboots into it.
