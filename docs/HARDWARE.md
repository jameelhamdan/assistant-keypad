# Hardware

A LILYGO T-Display S3, 8 key switches wired as a 2×4 matrix, a rotary encoder with push switch, an optional LiPo battery, and a 3D-printed case.

## Parts

Links are Amazon searches, not product pages: listings change. Match the specs, not the brand. The repository does not record the exact parts the author used; the case files in [`hardware/case/`](../hardware/case/) define the switch, knob and battery sizes.

| Qty | Part | Spec that matters | Amazon |
|---|---|---|---|
| 1 | LILYGO T-Display S3 | ESP32-S3, 16 MB flash, 8 MB PSRAM, 1.9" 170×320 ST7789 display. Non-touch version. Pin headers soldered or loose | [search](https://www.amazon.com/s?k=LILYGO+T-Display-S3) |
| 8 | Key switches | plain switches, no diodes needed; MX-compatible if the case is | [search](https://www.amazon.com/s?k=mechanical+keyboard+switches+5+pin+cherry+mx+compatible) |
| 8 | Keycaps | to fit the switches | [search](https://www.amazon.com/s?k=mx+keycaps+blank) |
| 1 | Rotary encoder with push switch | EC11 type: A, B, common and two switch pins | [search](https://www.amazon.com/s?k=EC11+rotary+encoder+with+push+button) |
| 1 | Knob | fits the encoder shaft | [search](https://www.amazon.com/s?k=EC11+encoder+knob) |
| 1 | LiPo battery (optional) | 3.7 V single cell, JST 1.25 mm 2-pin, size to fit the case | [search](https://www.amazon.com/s?k=3.7V+lipo+battery+JST+1.25mm+2+pin) |
| 1 | USB-C cable | must carry data, not charge-only | [search](https://www.amazon.com/s?k=usb-c+data+cable) |
| – | Hookup wire | 26–30 AWG | [search](https://www.amazon.com/s?k=26+awg+silicone+wire+kit) |
| – | Soldering iron, solder | | [search](https://www.amazon.com/s?k=soldering+iron+kit) |
| – | Screws, heat-set inserts | as the case files require | – |

3D prints: the STL files go in [`hardware/case/`](../hardware/case/). Print settings, once known, go next to them.

## Layout

```
              +----------------------------------+
   knob       |                                  |
  .-----.     |             screen               |
 (   o   )    |                                  |==== USB-C cable
  '-----'     |                                  |
  turn: move +----------------------------------+
  press: Enter
              [1]    [2]    [3]    [4]
              [5]    [6]    [7]    [8]
```

Keys 1-3 pick an option, the knob moves and scrolls, 4 and 8 send saved prompts 4 and 5 from the status screen, 7 is Enter, 5 is back, 6 lists sessions. The layout is fixed in firmware (`KEY_MAP` in `config.h`).

## Wiring

No external resistors: the firmware enables the internal pull-ups. The matrix has no diodes, so three keys held at once can ghost a fourth; decision screens accept only a press made with no other key down.

### Keys

Each key connects its row wire to its column wire.

```
             GPIO43  GPIO44  GPIO18  GPIO21     (columns, input pull-up)
GPIO17 (top)    1       2       3       4
GPIO16 (bot)    5       6       7       8       (rows, driven low one at a time)
```

### Encoder

| Encoder pin | Connect to |
|---|---|
| common (C) | GND |
| CLK (A) | GPIO3 |
| DT (B) | GPIO2 |
| push switch, one side | GPIO1 |
| push switch, other side | GND |

If turning is reversed, swap the CLK and DT wires. One detent is one step.

### Battery

LiPo cells can burn if shorted, punctured or charged wrongly. Use a protected cell, keep it away from the screws and solder joints, and do not charge it unattended.

Plug the LiPo into the board's JST connector. Check polarity against the board's silkscreen first: connectors from different sellers are wired both ways. The firmware reads the cell through the board's 1:2 divider on GPIO4 (3.3 V = 0 %, 4.15 V = 100 %) and keeps the screen at full brightness while USB is plugged in.

### Fixed by the board

| GPIO | Use |
|---|---|
| 15, 38 | LCD power, backlight |
| 5, 6, 7, 8, 9 | LCD reset, CS, DC, WR, RD |
| 39–42, 45–48 | LCD data D0–D7 |
| 4 | battery voltage |
| 19, 20 | native USB |

GPIO43/44 are UART0 unless "USB CDC on boot" is on, so the firmware refuses to build without `ARDUINO_USB_CDC_ON_BOOT=1` (set in `platformio.ini`).

## Flashing

1. Quit anything holding the serial port (Keypad opens it only while pairing).
2. `make flash` (needs [PlatformIO](https://platformio.org/)).
3. If the port does not appear: hold **BOOT**, press **RST**, release **BOOT**, flash again. Press **RST** once afterwards if the screen stays dark.

Then pair it: [README](../README.md#install). Later updates go over Wi-Fi from the tray (*Update firmware*).

## Timing

30 ms debounce; a press counts only if it starts at least 150 ms after its screen appeared; holding 4 or 8 repeats after 450 ms, every 110 ms. Constants in `firmware/include/config.h`.
