# Development

Needs Python 3.12+ with [uv](https://docs.astral.sh/uv/), [PlatformIO](https://platformio.org/), and on macOS the Xcode command-line tools.

## Layout

```
host/       Python: keypad/{core,device}, tests/
firmware/   PlatformIO: src/ (+ src/text), include/config.h, test/ (native), sim/ (PC simulator)
proto/      wire protocol
packaging/  PyInstaller spec, icon, macOS DMG, Windows installer
hardware/   case files (STL)
docs/       hardware, architecture, this file
```

## Commands

```sh
make setup      # host/.venv (uv sync)
make test       # host pytest + firmware native tests
make lint       # ruff
make firmware   # build
make flash      # flash over USB
make sim        # firmware simulator tests
make mac        # dist/Keypad.app + DMG
```

Windows: `packaging\windows\build.ps1 -Version 1.2.3` builds `dist\Keypad` (`keypad.exe` console, `keypadw.exe` windowless) and, with Inno Setup installed, the installer.

Run from source: `host/.venv/bin/keypad tray` (`Scripts\keypad` on Windows). `keypad install` points the hooks and the login item at that program. `KEYPAD_HOME=<dir>` isolates settings and state.

## Tests

- **Host** (`host/tests`): hook decisions against `tests/fakekeypad.py` (an in-process keypad with a press policy), transcript and Markdown, secure channel (a test vector shared with the firmware), config, API, tray and platform helpers.
- **Firmware, native** (`firmware/test`): word wrap and glyph mapping.
- **Simulator** (`firmware/sim`): compiles the real `app.cpp`, `ui.cpp` and text code for the PC. `pip install ziglang pillow pytest`, run `pio run -e keypad` once (fetches libraries), then `python firmware/sim/build.py` and `python -m pytest firmware/sim -q`. `python firmware/sim/shots.py` renders every screen to `firmware/sim/out/gallery.png`; `docs/img/screens.png` comes from it.
- **By hand on a keypad:** number keys and Enter on a real permission dialog (Esc must do nothing; the knob press selects); `AskUserQuestion` with 3 and 10 options and multi-select; Claude finishing with *continue* and a saved prompt; two sessions and the session list; answering in the terminal while a request is open (the keypad dialog must vanish); pairing and reconnect after a router IP change; firmware update over Wi-Fi; install and uninstall on clean macOS and Windows machines.

## Release

GitHub → Actions → *ci* → *Run workflow* on the commit to release, enter the version (`3.1.0`). CI runs the tests, builds the firmware with that version, bundles it into the app (so *Update firmware* works), builds the DMG and the Windows installer, then creates the tag `v3.1.0` and a GitHub release with both installers attached. If any step fails nothing is published. It refuses a version that is malformed or whose tag exists. Pushing a tag `v*` does the same.

The version exists only in the tag: `pyproject.toml` has a placeholder and the firmware defaults to `dev`. Builds are not signed; set `CODESIGN_ID` and `NOTARY_PROFILE` for `packaging/macos/build.sh` to sign and notarize the DMG.

## Rules for changes

- The hook command imports only the standard library, `keypad.ipc` and `keypad.dirs`.
- A protocol change that only adds fields or message types keeps `v`. Anything else bumps `PROTOCOL_VERSION` (`firmware/include/config.h`) and `proto.VERSION`, and needs a USB reflash.
- Session states reach the keypad as the five words in `core/sessions.py` `PHASES`; `tests/test_states.py` checks that `ui.cpp` uses no others.
