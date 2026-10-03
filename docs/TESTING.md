# Testing

## Automated

```sh
make test
```

- `host/`: `pytest` (in `host/.venv`; `make test-host`)
  - Hook decisions against a simulated keypad: Yes, Yes and don't ask again, No, hand to PC, timeout, pause, no keypad, continue limit, saved prompts, AskUserQuestion single and multi-select, project filters, session pinning, the status following the asking session, the permission mode, and presses the screen didn't offer (ignored).
  - Secure channel: handshake, wrong key, unknown host, replay, and a fixed vector shared with the firmware.
  - Claude settings merge and unmerge, keeping foreign hooks.
  - Session registry (pruning, per-turn timer) and config handling (clamping, old key maps, default shortcuts and removed settings, damaged state.json).
  - Hook shim (transcript reader, payload filtering, no agent → `{}`) and the private IPC channel (round trip, owner-only socket, hung-up clients).
- `firmware/`: `pio test -e native`: word wrap, glyph sanitising, Arabic shaping and bidi.
- On boot the firmware checks its HKDF and AES-GCM against the same vector as the host tests (`host/tests/test_secure.py`). On failure it shows *Crypto self-test failed*.

## Firmware simulator (no hardware)

`firmware/sim/` compiles the real `app.cpp`, `ui.cpp` and text code for the PC, against stand-ins for the screen, keys and links. Everything drawn and every key press is the firmware's own logic.

```sh
pio run -e keypad                 # once: fetches the libraries the simulator reuses
pip install ziglang pillow pytest # a C++ compiler (or set CXX/CC) and the test tools
python firmware/sim/build.py      # builds firmware/sim/out/keypad-sim
python firmware/sim/live.py       # open http://127.0.0.1:8765: click or type keys, send host messages
python firmware/sim/shots.py      # firmware/sim/out/gallery.png: every screen and mode in one image
python -m pytest firmware/sim -q  # key logic and screens, also run by CI
```

`live.py` plays the host (pings, hello) so the keypad stays connected, shows what the keypad sends back, and has the example screens of `scenarios.py` in a drop-down. `keypad_sim.Sim` drives it from Python for new tests.

## Without hardware (host side)

```sh
KEYPAD_HOME=/tmp/kp host/.venv/bin/keypad agent --fake-device allow   # isolated from your real setup
echo '{"session_id":"s1","cwd":"/tmp/demo","tool_name":"Bash","tool_input":{"command":"ls"}}' \
  | KEYPAD_HOME=/tmp/kp host/.venv/bin/keypad hook PermissionRequest   # -> {"hookSpecificOutput":{"decision":{"behavior":"allow"},...}}
```

## On the keypad, automated

`tools/hw_regress.py` drives real hook calls through the agent and the Wi-Fi link to a keypad running the debug firmware, which lets the script press keys over USB. It covers:

- permission: number keys, Enter, Esc and the encoder click to the PC, down + Enter for *don't ask again*, and keys that pick nothing being ignored
- AskUserQuestion single choice, multi-select (tick, down to Submit) and a cursor past the 4th of 10 options
- Stop: continue, Esc, and a saved prompt
- Enter on the status screen sending a saved prompt on the next tool call

See the script header for how to run it.

Firmware update over the link: `keypad update <id> firmware/.pio/build/keypad/firmware.bin`.

## On the keypad, by hand

- [ ] Boot splash, then *Waiting for your computer*
- [ ] Hold 1: key test. Every key 1–8 lights in the right place, the encoder counts both ways and its click registers. Hold 8 to leave.
- [ ] USB: plugging in shows the status screen within ~3 s; the tray shows the keypad
- [ ] Permission from a real Claude Code session: 1 = Yes, 7 = the highlighted option, the knob click = to the PC, 2 = don't ask again (the rule lands in Claude Code's settings), 5 leaves it to the PC; a long command scrolls with 4 from the first option
- [ ] AskUserQuestion with 3 options, with 10 options (the cursor scrolls the list), and multi-select
- [ ] Claude finishes: the transcript with continue and saved prompts below; 5 leaves it to the PC
- [ ] Main screen: 4/8 scroll the transcript, 5 jumps back; the permission mode shows bottom right
- [ ] Two sessions: 6 lists both with their states; picking one pins it (✓), Follow latest unpins; the tray and menu bar show the same
- [ ] Theme dark / light / match computer; brightness slider
- [ ] Tray → Add keypad…: the three dialogs (name, network, hidden password) pair it; unplug: it reconnects over Wi-Fi within ~10 s
- [ ] Router gives a new IP: the keypad is found again by mDNS
- [ ] A second PC on the same network can't connect to the keypad until it is paired there (which moves it)
- [ ] Two keypads on one PC: both show the request, the first answer wins, the other returns to status
- [ ] Tray → keypad → Update firmware over Wi-Fi and over USB: progress in the menu, a notification when done
- [ ] Tray: saved prompts (add, edit, move, remove), options (including *When Claude finishes*), the wait time and the continue limit take effect
- [ ] First launch: the *Connect Claude Code?* dialog, then the *Keypad is running* notification (macOS and Windows)
- [ ] Tray icon: outlined keys with a keypad and no sessions, filled keys (orange / blue / green) with sessions, dim and slashed with no keypad or when paused
- [ ] Mic button (once wired, `MIC_PIN` in `config.h`): hold and release; `agent.log` shows `mic start` then `mic stop`
- [ ] Arabic question text renders right-to-left
- [ ] Fresh install and uninstall on clean macOS and Windows machines
