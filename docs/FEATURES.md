# Keypad features

Everything Keypad does today. **Status** says how far each feature has been checked: **HW** = used on a real keypad, **Sim** = tested against the PC simulator with the real host, **Test** = automated host test only, **Untested** = written but never run end to end.

## 1. The keypad (ESP32, 320×170 screen, 8 keys, knob)

| Feature | What it does | Status |
|---|---|---|
| Live transcript | The selected Claude Code session: your prompts, Claude's text laid out as Markdown (bold, code, dim, lists), tool calls, errors, elapsed time, permission mode | HW, Sim |
| Keys 1, 2, 3, 4, 8 on the status screen | 1 pause/resume, 2 next session, 3 "ask when finished" (always / only when away / never), 4 brightness, 8 "Claude finished" alert on/off. Never active on a dialog | HW, Sim, Test |
| Knob | Turn: move the cursor, scroll long text (faster when turned fast; always one row per click in an option list). Press: Enter, the way to choose an option | Sim (acceleration feel: untested on HW) |
| Key 7 / Enter | Select the highlighted option, tick/submit on multi-select; on the status screen, open the model and effort sliders | HW, Sim |
| Model and effort sliders | Key 7 on the status screen: the knob moves between the two sliders, a press lets it change the value, a second press saves it to the project's `.claude/settings.local.json` for its next session; Esc keeps the old value | Test, Sim |
| Key 5 / Esc | "Done" on the Claude-finished screen; jumps to the newest line on the status screen; does nothing on a decision | HW, Sim |
| Key 6 | Session list; the knob walks it, press shows the session | Sim |
| Dialog screens | Permission (edits shown as a coloured diff, scrollable body), single question, multi-select, "Claude finished" prompt list, countdown, "+N waiting" | HW, Sim |
| Status screen | Spinner with timer, session state, permission-mode label, paused banner, toasts | HW, Sim |
| "Waiting for your computer" screen | Wi-Fi state, how long ago the PC was last seen | Sim |
| Dimming | Dims on battery when idle; the next key only wakes it; requests and toasts wake it fully | Sim |
| Safety rules | Press counts only if clean (no other key held) and ≥150 ms after its screen appeared; answered once per screen; stale answers are repeated, not re-asked | HW, Sim |

## 2. Claude Code integration

| Feature | What it does | Status |
|---|---|---|
| Permission prompts | Yes / "Yes, don't ask again…" / No on the keypad; the hook returns allow/deny | HW, Sim |
| Session-only "don't ask again" | Option `always_for_session` makes the rule last only for the Claude Code session | Test |
| Questions (`AskUserQuestion`) | Single and multi-select answered on the keypad, with option descriptions; an "Other…" row hands free text back to the PC | HW, Sim |
| Claude finished | *continue* (keeps Claude working); asked only when you have been away from the PC for a while (`ask_when_finished`: always / only when away / never) | Sim |
| Light-up on finish | A toast wakes the keypad when Claude finishes and nothing is asked on it (`notify_when_finished`) | Sim |
| Answered in the terminal first | The keypad dialog closes by itself | Test |
| Fallback, never auto-approve | Paused, unreachable, no answer in 5 minutes (`timeout`), or a lost keypad past a 20 s grace: Claude Code asks in the terminal | Sim, Test |
| Transcript privacy | Tool output and file contents are not read into the feed; secrets in prompts are redacted | Test |
| Hooks installer | Hooks: SessionStart/End, UserPromptSubmit, PreToolUse (AskUserQuestion only), PermissionRequest, Stop. Adds/removes only its own hooks in `~/.claude/settings.json` (backup first, exact round trip), raises Claude Code's Stop-hook cap to 20 | Test, HW (installed) |

## 3. Sessions

| Feature | What it does | Status |
|---|---|---|
| Several sessions | Up to 8 listed; the keypad shows the latest activity | Test, Sim |
| Choosing a session | Picked from the keypad or the tray; stays shown until a request or a finished turn elsewhere needs you | Test, Sim |
| A request from another session | Pulls the display to that session while it is on screen; requests queue one at a time (FIFO) | Sim |
| Pick-up of running sessions | Sessions already running when Keypad starts are found from their transcripts | Test |

## 4. Connectivity and security

| Feature | What it does | Status |
|---|---|---|
| Wi-Fi link | The PC connects to the keypad (mDNS, last IP as fallback); AES-256-GCM frames with HKDF keys from the pairing key and both nonces | HW, Test (real socket rig) |
| USB | Setup only: pairing, unpairing, flashing | HW |
| Pairing | Tray → Add keypad…: three native pop-ups (name, Wi-Fi network, password); key stored in the keypad's NVS and the PC's state file | HW (Windows) |
| Up to 3 computers per keypad | One is served at a time; a second is told who has it (`busy`); tray → Use keypad here takes it over | Test (real socket rig) |
| Reconnect | A dropped link comes back by itself; an open screen is shown again; a lost answer is repeated | HW, Test |
| Health counters | `keypad status` shows reconnects, ignored presses, hook timings | HW |
| Protocol versioning | Fields can be added without a version bump; mismatches show "flash the keypad" | Test |
| No web components | No HTTP server, page or browser anywhere; only outgoing HTTPS to GitHub for updates | By design |

## 5. The tray app (Windows notification area, macOS menu bar)

| Feature | What it does | Status |
|---|---|---|
| Status icon and tooltip | Colour shows what needs you; shows the shown session and keypad count | HW |
| Keypads menu | Rename, Set up / change Wi-Fi, Update firmware (over Wi-Fi, with progress), Use keypad here, Forget | Test, HW (firmware via cable) |
| Sessions menu | Pick the session the keypad shows | Test |
| Pause keypad | Decisions stay on the PC | Test |
| Options | Ask when finished (Always / Only when away / Never), light up on finish, session-only "don't ask again", update automatically | Test |
| Advanced | Edit settings file, open logs folder, check for updates, version | Test |
| Claude Code integration and Start at login | Toggles; login item is a Task Scheduler task (Windows) or LaunchAgent (macOS), restarted on crash | HW (Windows), Test |
| Uninstall (macOS) | Removes hooks and login item, then quits | Untested |

## 6. Install, update, uninstall

| Feature | What it does | Status |
|---|---|---|
| Windows installer | Per-user, no admin; installs hooks and the login task; stops the old copy first; upgrades in place | HW (Windows: 3.0.0 → 3.1.1, silent) |
| macOS app and DMG | Keypad.app in a disk image (ad-hoc signed unless a signing identity is set) | Untested (no Mac) |
| Uninstall | Windows: Apps & features removes hooks, login task and files; settings and pairing keys are kept (asked about only in an interactive uninstall); macOS: tray Uninstall | HW (Windows, silent); macOS untested |
| Auto-update of the PC/macOS client | Checks GitHub releases at start and every 6 h; verifies the SHA-256 GitHub publishes; installs silently when nothing is waiting on the keypad; manual check and opt-out in the tray | HW (Windows: 3.0.5 → 3.1.0 on its own in 33 s); macOS untested |
| Firmware update | Over Wi-Fi from the tray (MD5-checked chunks; the update only counts as done if the keypad returns with the new version) | HW (3.1.0-dev → 3.1.1 over Wi-Fi) |
| Config | `config.json`, reloaded when edited; unknown old keys are ignored | Test |

## 7. Development tooling

| Feature | What it does |
|---|---|
| PC simulator | The real `app.cpp` and `ui.cpp` on the PC: tests, screenshots, a live viewer |
| Whole-product tests | The real host (hooks, dialogs, hub) drives the simulated keypad and gets key presses back as decisions |
| Wi-Fi test rig | A fake keypad over a real encrypted TCP socket with the firmware's handshake rules |
| CI | Host tests, firmware build, simulator tests, Windows and macOS installers, a GitHub release per version tag |
| Screenshot gallery | `firmware/sim/shots.py` renders every screen; the README image comes from it |
