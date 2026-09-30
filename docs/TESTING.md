# Testing

## Automated

```sh
make test
```

- `host/`: `go test -race ./...`
  - Hook decisions against a simulated keypad: allow, deny, hand to PC, timeout, pause, no keypad, continue limit, shortcuts, AskUserQuestion single and multi-select, project filters, session pinning.
  - Secure channel: handshake, wrong key, unknown host, replay, and a fixed vector shared with the firmware.
  - Claude settings merge and unmerge, keeping foreign hooks.
- `firmware/`: `pio test -e native`: word wrap, glyph sanitising, Arabic shaping and bidi.
- On boot the firmware checks its HKDF and AES-GCM against the same vector as the Go tests. On failure it shows *Crypto self-test failed*.

## Without hardware

```sh
dist/keypad agent --fake-device allow     # in one terminal (or quit the real agent first)
echo '{"session_id":"s1","cwd":"/tmp/demo","tool_name":"Bash","tool_input":{"command":"ls"}}' \
  | dist/keypad hook PermissionRequest   # -> {"hookSpecificOutput":{"decision":{"behavior":"allow"},...}}
```

## On the keypad, automated

`tools/hw_regress.py` drives real hook calls through the agent and the Wi-Fi link to a keypad running the debug firmware, which lets the script press keys over USB. It covers:

- permission allow, deny and hand to PC, and unbound keys being ignored
- AskUserQuestion single choice, multi-select and paging past 8 options
- Stop: continue, done, a shortcut, and back out of the shortcut menu
- the status-screen shortcut menu being delivered on the next tool call

See the script header for how to run it.

Firmware update over the link: `keypad update <id> firmware/.pio/build/keypad/firmware.bin`.

## On the keypad, by hand

- [ ] Boot splash, then *Waiting for your computer*
- [ ] Hold 1: key test. Every key 1–8 lights in the right place, the encoder counts both ways and its click registers. Hold 8 to leave.
- [ ] USB: plugging in shows the status screen within ~3 s; the tray shows the keypad
- [ ] Permission from a real Claude Code session: 1 allows, 4 denies, 8 and the encoder click leave it to the PC
- [ ] AskUserQuestion with 3 options, with 10 options (pages), and multi-select
- [ ] Claude finishes: Continue, Done, Shortcut → pick one
- [ ] Two sessions: the encoder switches, the click follows latest again
- [ ] Theme dark / light / match computer; brightness slider
- [ ] Set up Wi-Fi from Settings, unplug: the keypad reconnects over Wi-Fi within ~10 s
- [ ] Router gives a new IP: the keypad is found again by mDNS
- [ ] A second PC on the same network can't connect to the keypad (Settings shows it as *paired with another computer*)
- [ ] Two keypads on one PC: both show the request, the first answer wins, the other returns to status
- [ ] Update firmware from Settings over Wi-Fi and over USB
- [ ] Arabic question text renders right-to-left
- [ ] Fresh install and uninstall on clean macOS and Windows machines
