# Security

The keypad sits between a person and a coding agent that can run commands. The rules below are enforced in code.

## What it can do

It can answer a question, allow or deny a single permission prompt that Claude Code was already going to show, or tell Claude to continue after it stopped. Each of these needs a fresh, clean key press on a screen with a unique id.

It never:
- changes permission modes, or adds an allow rule on its own: *Yes, and don't ask again* saves exactly the rule Claude Code itself suggested for that request, and only when you pick it
- starts Claude Code processes, or passes `--dangerously-skip-permissions`

The knob click is Esc, never a decision. The agent keeps the sessions it mirrors, with their (redacted) transcripts, in `sessions.json` in its data folder, readable only by you, so they survive a restart.

The installer touches only our own hook entries, the `keypad` MCP server and `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`, and backs up `settings.json` first.

## Enforcement

| Rule | Where |
|---|---|
| No decision without a key press | `HookMixin.permission` / `stop` / `ask` (`keypad/core/hooks.py`) return a decision only for a `press` on the active screen id that matches what the screen offered (Esc with the screen's `esc` action, Enter on an existing option, a number key on its own option, or Submit with valid picks: `dialogs.press_matches`); timeout, `pc`, disconnect, pause or error → `{}` |
| Local API reachable only by this user | `keypad/ipc.py`: Unix socket mode 0600 / named pipe whose ACL admits only its owner (this user) and SYSTEM; no TCP listener |
| Settings | changed only from the tray menu and native dialogs, over the same private socket; there is no web page or network-facing settings server |
| Pairing needs physical access | `provision` / `unpair` are accepted only over USB |
| Wi-Fi link | the host connects out; the keypad accepts only its paired host id; keys from HKDF-SHA256 over the pairing key and both nonces; AES-256-GCM with implicit counters, so any forged, replayed or reordered frame ends the session; a connection that has not proven the key can't displace the current host |
| Nothing sensitive reaches the device | the hook shim drops tool output and file contents. From the transcript it takes only Claude's own text messages (the prose you see in the terminal, last 4, 400 characters each) and the session title, never thinking, tool calls or their results; `core.text.redact` hides secret-looking strings before display and logs |
| Stale and ghost presses | a press must start ≥150 ms after its screen appeared, with no other key held; the device answers each screen once and replays that answer if the host asks again |
| Loop protection | consecutive keypad continues are counted; at the limit the keypad asks for an explicit confirmation |
| Test hooks | the simulated keypad exists only behind `keypad agent --fake-device` |

## Fail-safe

| Failure | Behaviour |
|---|---|
| Keypad unplugged / out of range | requests fail immediately; hooks return `{}`; Claude Code shows its own prompt |
| Agent not running | hook shim can't connect and prints `{}` in milliseconds; MCP tool says `KEYPAD UNAVAILABLE` |
| No press in time | the screen closes, Claude Code falls back |
| You use the PC | the pending request is handed back when you type after it appeared (keyboard on macOS; keyboard or mouse on Windows) |
| Keypad reboots mid-request | it re-announces itself; the host re-sends the active screen |
