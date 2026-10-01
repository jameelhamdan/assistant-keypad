# Design review

Every design and feature decision in Keypad, rated after the "mini Claude
Code" redesign. Ratings:

| Rating | Meaning |
|---|---|
| **Necessary** | Without it the product is unsafe or doesn't do its job |
| **Needed** | Core to using it day to day; removing it would be a clear loss |
| **Nice to have** | Real value, but optional |
| **Can be removed** | Little value for what it costs (code, screen space, attention); removing it is fine |
| **Must be removed** | Wrong for the product or broken; removed in this redesign |

## Keypad interface

| Decision | Rating | Why | Status |
|---|---|---|---|
| Fixed Claude Code key layout: 1–3 pick, 4 up, 8 down, 7 Enter, 5 Esc, 6 sessions | Necessary | One layout everywhere, the way Claude Code is driven from a keyboard. 4/8 as up/down was the requested change | New |
| Configurable key map (`keys:` in config, tray **Keys** menu, conflict checks) | Must be removed | Every screen could be laid out differently, and a fixed up/down pair can't coexist with remapping. It also needed conflict validation and a "PC key taken" special case | Removed; old `keys:` in config.yaml is ignored |
| Eight built-in default shortcuts | Must be removed | Requested. They were someone else's workflow, filled the Stop screen and the menus, and were written into every new config.yaml | Removed; an untouched old list is dropped on load |
| Paged lists (8 per page, key *n* = item *n* on the page, encoder pages) | Must be removed | Replaced by a cursor list that scrolls. Number = position on page was confusing past page 1 | Replaced |
| Mirrored transcript (`> prompt`, `⏺` text and tools, `⎿` results, `✻ Brewing…`) | Necessary | It is the "mini Claude Code" | Kept |
| Transcript scroll-back (4/8, 24 lines, 5 jumps to latest) | Needed | Lets you read what led up to a request | New |
| Session list on 6 (state, ✓ shown, *Follow latest activity*) | Necessary | Requested: which sessions are synced, which is active, how to switch | New (replaces the encoder cycling through sessions blindly) |
| Header `2/3` position and project | Needed | Always shows which session you're looking at | Kept, clarified |
| Keypad shows the asking session while its request is on screen | Needed | Otherwise a permission from session B appears over session A's context | New |
| Permission dialog in Claude Code's words (*Bash command*, *Do you want to proceed?*, 1. Yes / 2. Yes, and don't ask again / 3. No) | Needed | Same decision, same words as the terminal; option 2 uses Claude Code's own `permission_suggestions`, so the saved rule is identical | New |
| `❯` cursor and number keys, both | Needed | Number keys answer in one press; the cursor reaches option 4 and beyond | New |
| Scroll a long command before deciding (4 from the first option) | Needed | You should be able to read the full command you approve | New |
| Enter never decides while the body has focus | Necessary | Prevents approving by scrolling one step too far | New |
| Encoder click = Esc | Necessary | As Enter it caused unintended continues in testing (continue is often the only option). As Esc a stray knob press never decides | Changed after testing |
| *Claude finished* screen (continue, saved prompts, done) | Needed | The only way to keep Claude going from the keypad; now the transcript with numbered options below it instead of a modal | Redesigned |
| Prompt box on that screen repeating the highlighted option | Must be removed | Redundant: the `❯` cursor already marks the choice, and the box took three transcript lines | Removed after testing on the device |
| Prompt box on the main screen | Must be removed | The keypad can't type: it showed a `>` that did nothing and took three transcript lines. Paused moved to the status line, *7 send* to the hints | Removed |
| *done* option on that screen | Can be removed | Does what Esc does | Removed |
| Permission mode (`⏵⏵ accept edits on`, plan, bypass) | Nice to have | The same status line as Claude Code, from `permission_mode` | New |
| Welcome box for a session with no transcript yet | Nice to have | Claude Code's first screen; better than a blank area | New |
| Random spinner verbs (*Brewing*, *Noodling*…) | Nice to have | Flavour that matches Claude Code; costs nothing | Kept |
| Countdown, project and `+n` queued in each dialog | Needed | You need to know when the PC takes over and what is waiting | Kept, `+n` added |
| Toasts for subagent start/stop and compaction | Can be removed | Noise on a small screen that rarely matters; the transcript shows the work | Removed |
| Key test mode (hold 1 while waiting) | Nice to have | Diagnoses matrix wiring without a computer | Kept |
| Arabic shaping and RTL (≈450 lines + font) | Nice to have | Valuable if you or Claude write Arabic; dead weight otherwise | Kept |
| Dark / light / match computer, brightness, battery icon | Nice to have | Comfort; small cost | Kept |

## Safety and core behaviour

| Decision | Rating | Why | Status |
|---|---|---|---|
| Nothing auto-approved; any failure, timeout or pause returns `{}` | Necessary | The product's central promise | Kept |
| Clean presses only, 150 ms stale-press guard, answer once per screen | Necessary | The matrix has no diodes; a screen can appear under a finger | Kept |
| Host accepts only presses the screen offered (`press_matches`) | Necessary | A buggy or hostile device can't claim a decision it wasn't shown | Kept, rewritten for the cursor model |
| Secret redaction before text reaches the screen or logs | Necessary | Commands and prompts often carry tokens | Kept |
| Hand back to the PC when you type there | Needed | You never fight the keypad while at the keyboard | Kept |
| `present_window` / `present_wait` hand-back tuning | Can be removed | Off by default, not in the tray, adds a second hand-back rule nobody sees | Removed |
| Ask when Claude stops (blocking Stop hook) | Needed | Enables continue and saved prompts; can be switched off in Options | Kept |
| Continue limit and keeping Claude Code's own cap in step | Necessary | Stops a runaway continue loop | Kept |
| Answering `AskUserQuestion` on the keypad | Needed | Claude's questions are the most common keypad decision | Kept |
| `ask_user` MCP tool | Nice to have | Mostly covered by `AskUserQuestion`; useful for yes/no and more than four options | Kept |
| `notify` MCP tool | Can be removed | Lets Claude put text on the screen; the transcript already shows Claude's messages | Removed |
| Telling Claude a keypad is connected (context on SessionStart / each prompt) | Nice to have | Steers Claude towards keypad-friendly questions; costs a few tokens per prompt | Kept |
| Saved prompts (none by default), delivered on the next hook | Nice to have | The keypad's stand-in for typing; useful only once you add your own | Kept, renamed from "shortcuts" |
| Workers (`claude -p` started in an idle project) | Can be removed | A hidden background session you can't watch in a terminal, its output only in a log file; overlaps saved prompts. ≈150 lines plus config, tray and a dialog | Removed |
| Session pinning vs following the latest activity | Needed | You choose a session, or let the keypad follow your work | Kept, now visible |
| Session limits (32 tracked, 6 h stale, 8 on a keypad) | Needed | Bounded memory and message size; sessions past the keypad's 8 are now marked in the tray | Kept |
| Transcript tail read by the hook (window widening to 4 MB) | Needed | Mirrors Claude's prose; runs on every hook, so watch its cost on huge transcripts | Kept |

## Connectivity and devices

| Decision | Rating | Why | Status |
|---|---|---|---|
| USB serial link | Necessary | Works with no setup | Kept |
| Wi-Fi: pairing over USB, HKDF + AES-GCM, mDNS, host connects out | Needed | A stated feature, and done safely | Kept |
| Last-known IP fallback when mDNS is blocked | Nice to have | Rescues networks that filter mDNS | Kept |
| Firmware updates from the tray (OTA) | Needed | Otherwise every update needs PlatformIO | Kept |
| Several keypads mirroring, first answer wins | Nice to have | Rare setup; little extra code | Kept |
| Per-keypad project filter | Nice to have | Only matters with several keypads or many projects | Kept |
| Identify | Nice to have | Tells keypads and computers apart; tiny | Kept |

## Tray and app

| Decision | Rating | Why | Status |
|---|---|---|---|
| Shown session in the menu (*Showing X · 2 of 3*), menu bar text (`money-mind 2/3`, macOS), tooltip (Windows) | Necessary | Requested: visible status of which session is active | New |
| Session list in the menu: states, ✓ shown, *not on the keypad*, how to switch | Necessary | Requested: which sessions are synced, and how to switch | New |
| Icon colour (working, waiting, idle, off) | Needed | State at a glance | Kept |
| One wait time for all requests (tray) over three in config | Can be removed | config.yaml keeps separate question / permission / stop timeouts that the tray always sets together; one value would do | Removed |
| Options submenu, edit settings file with live reload, logs folder | Nice to have | Power-user access without a settings window | Kept |
| Claude Code integration toggle, start at login, first-run welcome | Needed | Install and uninstall without a terminal | Kept |
| CLI (`status`, `claude status`, `update`, `install`) | Nice to have | Scripting and support | Kept |

## Found during the review

| Issue | Rating | Status |
|---|---|---|
| `hook.py` defined `_scan` and `TRANSCRIPT_MAX` twice (copy and paste) | Must be removed | Removed |
| Two requirement notes left at the end of README.md | Must be removed | Removed (now implemented) |
| Eight old shortcuts in your config.yaml (one renamed, so not dropped automatically) | Must be removed | Removed at your request |
| Permission dialogs cut commands at 400 characters, so the end of a long command could be approved unseen | Must be removed | Fixed: up to the dialog's 1,400 characters |
| Claude's questions cut at 127 bytes, and their option descriptions dropped | Must be removed | Fixed: long questions and descriptions go in the scrollable body |
| A question under a body got one line, cutting "Do you want to make this edit to …?" | Must be removed | Fixed: two lines |
| Scrolled back on the main screen, new output moved the text being read | Must be removed | Fixed: the view holds still, like a terminal |
| The first transcript line could not be reached by scrolling (off by one) | Must be removed | Fixed |
| The whole transcript was re-wrapped on every status update (several a second) | Must be removed | Fixed: only when it changed |
| Dialog bodies wrapped at most 48 lines | Must be removed | Fixed: 128 |

## Second pass

Every *Can be removed* item above has been removed: workers (a saved prompt
for an idle session now waits for your next prompt), the hidden hand-back
tuning, the three timeouts (now one `behavior.timeout`), the subagent and
compaction toasts with their hook registrations, the `notify` MCP tool, and
the *done* option. Old `workers:` and `timeouts:` entries in config.yaml are
ignored.

## Third pass (after testing on the device)

| Change | Rating | Why |
|---|---|---|
| Ask when Claude finishes only after you've been away (any input, 1 min by default; tray: 1/2/5 min, always, never) | Needed | At the PC the question held up your next prompt for nothing |
| Diff in edit approvals (`+` green, `-` red) | Needed | You approve the change, not just the file name |
| Sessions and transcripts kept across agent restarts (`sessions.json`, owner-only) | Nice to have | The keypad no longer goes blank after an update or restart |
| Screen dims after a minute idle and wakes fully for requests; the first press on a dim screen only wakes it | Nice to have | Requests catch the eye; the screen isn't glaring all day |

