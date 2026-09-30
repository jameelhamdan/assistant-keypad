# Keypad

A small hardware keypad for [Claude Code](https://claude.com/claude-code): see
what Claude is doing on a color screen, and allow or deny tool use, answer
questions, or tell it to keep going, with one press. It works over USB or
Wi-Fi, on macOS and Windows, with any number of Claude Code sessions.

It runs on the same hardware as [Jameel](https://github.com/AbdelrhmanFrehat/Jameel)
(LILYGO T-Display S3, 2×4 key matrix, rotary encoder). The software is a
clean rewrite.

The screen speaks Claude Code's visual language: a monospace terminal grid,
its colors, `✻` `⏺` `⎿` `❯`, rounded permission dialogs and numbered options.

```
 ╭──────────────────────────────────────╮
 │ Allow Bash?            money-mind 4:59│      keys:  1 2 3 4
 │                                      │             5 6 7 8   + encoder
 │   Run tests, then push               │
 │   go test ./... && git push          │
 │ ❯ 1. Allow                           │
 │   4. Deny                            │
 │   8. PC                              │
 ╰──────────────────────────────────────╯
   click: answer on the PC   turn: scroll
```

## Features

- **Status at a glance:** `⏺ Bash(go test ./...)`, `✻ Brewing… (1m 12s)`, waiting on you, done. Every session is listed; the encoder switches between them.
- **Permissions:** Allow or Deny. They sit at opposite ends of the top row.
- **Questions:** Claude's `AskUserQuestion` (single or multi-select) and the `ask_user` MCP tool. Options are numbered like the keys, so key *n* picks option *n*. The encoder pages through more than eight.
- **When Claude stops:** Continue, Done, or send a **shortcut** ("Write tests", "Review", …). Shortcuts for an idle project can start a `claude -p` worker there.
- **Hand-back to the PC:** start typing on the computer and the pending request goes back to the normal Claude Code prompt. The encoder click (or key 8) does the same.
- **Dark and light themes:** set per keypad, or follow the computer's appearance.
- **Wi-Fi:** pair over USB once. After that it works anywhere on the LAN, encrypted, with no conflicts between keypads or PCs.
- **Arabic / RTL text** is shaped and laid out right-to-left on the device.
- **Configured from the tray:** keys, shortcuts, timeouts, per-keypad theme, brightness and project filter.

Nothing is ever auto-approved. When the keypad isn't there, is paused, times out, or anything goes wrong, Claude Code behaves exactly as it does without it.

## Install

| | |
|---|---|
| **macOS** | Open `Keypad-<version>.dmg`, drag **Keypad** to Applications, open it. |
| **Windows** | Run `Keypad-<version>-setup.exe`. No administrator rights are needed. |

Keypad starts at login and lives in the menu bar or tray. The first time, it opens **Settings**:

1. **Claude Code:** click *Install*. This adds hooks and the `keypad` MCP server to `~/.claude`, after backing the file up. Restart any open Claude Code sessions.
2. **Keypads:** plug the keypad in with USB. It appears within a few seconds and works right away over the cable.
3. **Wi-Fi (optional):** click *Set up Wi-Fi…*, confirm the network and enter its password. The keypad pairs with this computer and joins the network. Unplug it whenever you like.

**First flash:** a keypad still running the old Jameel firmware needs this firmware once, over USB: `make flash` (PlatformIO). After that, updates come from Settings → *Update firmware*.

## Keys

The keys are numbered like the keypad: **1 2 3 4** on top, **5 6 7 8** below. Every option on screen carries its key number.

| Screen | Default |
|---|---|
| Status | 1 Shortcuts · encoder: switch session · click: follow latest |
| Permission | 1 Allow · 4 Deny · 8 PC |
| Yes / No | 1 Yes · 4 No · 8 PC |
| Choice | 1–8 pick · encoder: page · click: PC |
| Multi-select | 1–7 toggle · 8 Confirm · click: PC |
| Claude finished | 1 Continue · 2 Shortcut… · 4 Done · 8 PC |

Change them in Settings → Keys. While the keypad is waiting for the computer, hold **1** to test the keys; hold **8** to leave.

## How it fits together

```
Claude Code sessions ── hooks: keypad hook <Event> ─┐
                     ── MCP:   keypad mcp ──────────┤  Unix socket / named pipe (this user only)
Tray + Settings ────────────────────────────────────┤
                                                    ▼
                                             keypad agent  (per-user, starts at login)
                                     sessions · request queue · shortcuts · workers
                                   USB serial │                  │ Wi-Fi: TCP + AES-GCM, mDNS
                                              ▼                  ▼
                                           keypad             keypad, keypad …
```

- **One binary** (`keypad`) does everything: the agent, the tray, the Claude Code hook and MCP shims, and the CLI.
- **Hooks → agent** over a private local socket, never a TCP port. If the agent isn't running, the hook prints `{}` right away.
- **Agent → keypads:** the same JSON messages over USB or Wi-Fi ([protocol](proto/PROTOCOL.md)). The PC connects to keypads it has paired. A keypad accepts only the computer that paired it over USB.
- **Firmware is a smart terminal.** The PC sends screens and key bindings. The keypad renders them and enforces the safety rules: clean single presses, a stale-press guard, and one answer per screen. The top-right corner shows `usb` and Wi-Fi bars for the live links.
- **Several sessions:** requests from every session are queued and shown one at a time, each labelled with its project. **Several keypads** mirror each other and the first answer wins. Each keypad can be limited to certain projects.

## Build from source

Requirements: Go 1.24+, PlatformIO, and on macOS the Xcode command-line tools.

```sh
make test            # host tests (race detector) + firmware unit tests (native)
make firmware        # build the firmware
make flash           # flash over USB (quit the Keypad agent first: it holds the port)
                     # pio run -e keypad-debug -t upload: logs keys, accepts simulated presses
make host            # dist/keypad for this machine
make mac             # dist/Keypad.app + DMG
make windows         # dist/windows/keypad.exe + keypadw.exe; then iscc packaging/windows/keypad.iss
```

Develop without hardware: `dist/keypad agent --fake-device allow` attaches a simulated keypad that answers everything (policies: `first`, `allow`, `deny`, `continue`, `done`, `pc`, `none`). `KEYPAD_HOME=<dir>` isolates settings and state; `KEYPAD_NO_USB=1` makes the agent reach keypads over Wi-Fi only.

```
host/       Go: cmd/keypad + internal/{core,device,secure,proto,server,webui,ipc,claudecfg,service,osutil,config}
firmware/   PlatformIO: src/{app,ui,link,input,crypto,ota,store}, src/text (wrapping, Arabic)
proto/      wire protocol
packaging/  macOS app bundle + DMG, Windows installer
docs/       hardware, security, testing
```

## Where things live

| | macOS | Windows |
|---|---|---|
| Settings, pairing keys, logs | `~/Library/Application Support/Keypad` | `%APPDATA%\Keypad` |
| Claude Code hooks | `~/.claude/settings.json` | `%USERPROFILE%\.claude\settings.json` |
| Login items | `~/Library/LaunchAgents/com.jameelhamdan.keypad.*.plist` | Task Scheduler "Keypad Agent" + Run key |

`keypad status` shows keypads and sessions. `keypad claude status` checks the integration. `keypad uninstall` removes the hooks and login items.
