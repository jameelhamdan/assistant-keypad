# Keypad

A hardware keypad for [Claude Code](https://claude.com/claude-code). A small color screen mirrors your open sessions; eight keys and a knob answer permission prompts and questions and tell Claude to continue. It talks to the PC over Wi-Fi (USB is for power, pairing and flashing). macOS and Windows.

![All keypad screens](docs/img/screens.png)

## Keys

| Key | Does |
|---|---|
| knob turn / press | move the cursor / select it (the only way to choose an option) |
| 1 | status screen: pause / resume the keypad |
| 2 | status screen: show the next session |
| 3 | status screen: "ask when finished" always / only when away / never |
| 4 | status screen: screen brightness (20 / 50 / 80 / 100 %) |
| 8 | status screen: "Claude finished" alert on / off |
| 7 | Enter (same as the knob press); on the status screen, open the model and effort sliders |
| 5 | back; *done* on the "Claude finished" screen; on the status screen it jumps to the newest line. Does nothing on a decision |
| 6 | session list |
| knob | turn = move / scroll, press = 7 |

## What it does

- **Live feed:** the transcript of the selected session: your prompts, Claude's text (Markdown laid out for the screen), tool calls, errors, elapsed time, permission mode.
- **Decisions:** permission prompts (*Yes / Yes, don't ask again / No*, edits shown as a diff), `AskUserQuestion` (single and multi-select), and when Claude finishes: *continue*.
- **Fallback:** if the keypad is paused, unreachable or not answered within 5 minutes, Claude Code asks in the terminal as usual. If you answer in the terminal first, the keypad dialog closes. Nothing is ever auto-approved.

## Install

**macOS 12+:** open `Keypad-<version>.dmg`, drag Keypad to Applications, open it. Builds are not notarized: *System Settings → Privacy & Security → Open Anyway* once, and allow *Local Network*.

**Windows 10/11:** run `Keypad-<version>-setup.exe` (no admin rights; *More info → Run anyway* if SmartScreen warns).

Keypad starts at login and lives in the menu bar / notification area. First run:

1. Click **Connect** to add its hooks to `~/.claude/settings.json` (backed up first). Restart open Claude Code sessions.
2. Flash the keypad once over USB (`make flash`, see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)). Later updates: tray → your keypad → *Update firmware* (Wi-Fi).
3. Plug the keypad in with USB, tray → **Add keypad…**. Three pop-ups ask for a name, the 2.4 GHz Wi-Fi network and its password. When it says the keypad is connected over Wi-Fi, unplug the cable.

A keypad can be paired with up to three PCs and serves one at a time (see below). One PC can use several keypads; the first answer wins.

Build the keypad: [docs/HARDWARE.md](docs/HARDWARE.md). Everything it does, and how far each part has been checked: [docs/FEATURES.md](docs/FEATURES.md).

## Settings

Tray → *Options*, or edit `config.json` (reloaded on save). Set `KEYPAD_LOG=debug` for a more detailed log.

| Key | Default | Meaning |
|---|---|---|
| `behavior.ask_when_finished` | 60 | seconds away from the PC before "Claude finished" is asked on the keypad; 0 always, -1 never |
| `behavior.timeout` | 300 | seconds the keypad waits for an answer before Claude Code asks in the terminal |
| `behavior.notify_when_finished` | true | light up the keypad when Claude finishes and nothing is asked on it |
| `behavior.always_for_session` | false | "don't ask again" lasts the Claude Code session only, not for good |
| `behavior.auto_update` | true | installed builds update themselves from the project's GitHub releases (see below) |

**Updates.** An installed Keypad checks the latest GitHub release a minute and a half after it starts and then every 6 hours. With `auto_update` on, it downloads the installer for your OS, checks its SHA-256 against the digest GitHub publishes for the file, and installs it once no request is waiting on the keypad: on Windows the setup runs silently, on macOS the app is replaced from the disk image and reopened. With it off, the tray shows *Update to x.y.z…*. Tray → Advanced → *Check for updates…* checks on demand. A development checkout never updates itself. Only HTTPS requests to GitHub are made.

A keypad can be paired with up to three computers (one Wi-Fi network); one holds it at a time. A second computer is told who has it; tray -> your keypad -> *Use keypad here* takes it over. `keypad status` also shows health counters: reconnects, ignored presses, hook times.

## Commands

`keypad tray` (default), `keypad agent` (no tray), `keypad install`, `keypad uninstall [--purge]`, `keypad status`, `keypad version`. `keypad hook <event>` is called by Claude Code.

## Files

| | macOS | Windows |
|---|---|---|
| settings, state (pairing keys), logs | `~/Library/Application Support/Keypad` | `%APPDATA%\Keypad` |
| hooks | `~/.claude/settings.json` | `%USERPROFILE%\.claude\settings.json` |
| login item | `~/Library/LaunchAgents/com.jameelhamdan.keypad.plist` | Task Scheduler task `Keypad` |
| program | `/Applications/Keypad.app/Contents/MacOS/keypad` | `%LOCALAPPDATA%\Programs\Keypad\keypad.exe` |

## Troubleshooting

| Problem | Try |
|---|---|
| *Waiting for your computer* | Keypad running (tray icon)? Same network? Paired? |
| Claude Code never asks on the keypad | Tray → *Claude Code integration* ticked; restart the session |
| Wi-Fi bars red | wrong password, or 5 GHz-only network: set up Wi-Fi again over USB |
| Paired, not found | same network; on macOS allow *Local Network*; `keypad status` |
| Anything else | tray → *Open logs folder* → `agent.log` |

Uninstall: macOS tray → *Uninstall Keypad…*, then trash the app. Windows: Apps & features. Settings and keys stay in the data folder unless you delete it (`keypad uninstall --purge`).

More: [architecture and security](docs/ARCHITECTURE.md) · [development](docs/DEVELOPMENT.md) · [wire protocol](proto/PROTOCOL.md)

## License

[MIT](LICENSE). Dependencies: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Independent project, not affiliated with Anthropic; Claude and Claude Code are Anthropic's trademarks. Provided as is, without warranty: you decide what the keypad approves.