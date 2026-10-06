# Third-party software

Keypad itself is [MIT](LICENSE). It uses these projects under their own licenses.

## Host app (bundled in the macOS and Windows builds)

| Package | License |
|---|---|
| cryptography | Apache-2.0 OR BSD-3-Clause |
| pyserial | BSD-3-Clause |
| zeroconf, ifaddr | LGPL-2.1-or-later, MIT |
| pystray | LGPL-3.0 |
| Pillow | MIT-CMU |
| markdown-it-py, mdurl | MIT |
| PyInstaller (build tool, bootloader included in the app) | GPL-2.0-or-later with the PyInstaller exception, which allows any license for the built program |
| Python | PSF License |

zeroconf and pystray are LGPL libraries. The builds are PyInstaller one-folder bundles, so they ship as separate, replaceable files next to the program; their source is at the projects' own repositories. Windows adds pywin32 (PSF) and macOS adds PyObjC (MIT) through pystray.

## Firmware

| Library | License |
|---|---|
| Arduino core for ESP32 / ESP-IDF (pioarduino) | LGPL-2.1 / Apache-2.0 |
| GFX Library for Arduino | BSD-style, see the library |
| U8g2 | BSD-2-Clause |
| Spleen font (via U8g2) | BSD-2-Clause |
| ArduinoJson | MIT |
| Unity (native tests only) | MIT |

Each library's full license text is in its own repository and in `firmware/.pio/libdeps` after a build.

## Names

Claude and Claude Code are products and trademarks of Anthropic. LILYGO and T-Display are trademarks of their owners. This project is independent and not affiliated with or endorsed by them.
