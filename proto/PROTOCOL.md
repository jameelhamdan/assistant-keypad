# Keypad wire protocol v3

One message = one JSON object with a `t` (type) field. The keypad's working
link is Wi-Fi; USB is only for setup.

| Transport | Used for | Framing | Security |
|---|---|---|---|
| Wi-Fi, TCP port `7470` | everything | `u16` big-endian length + payload | pairing key, per-session AES-256-GCM |
| USB CDC (ESP32-S3 native USB, VID `0x303A`) | setup only: `hello`, `provision`, `unpair` | one compact JSON object per `\n`-terminated line | physical cable = trusted |

Limits: host → device ≤ 16000 bytes per message, device → host ≤ 1024 bytes.
Anything larger, anything that is not a JSON object with `t`, and any unknown
`t` is dropped and counted.

## Discovery and pairing

* The keypad advertises mDNS `_ckeypad._tcp` on port 7470, instance name =
  its id (`kp-` + last 3 MAC bytes in hex), TXT `id=`, `fw=`, `v=3`,
  `paired=0|1`.
* The **host connects to the keypad** (never the other way round). It only
  connects to ids it has paired, and it keeps the last IP of each keypad as
  a fallback when mDNS is blocked.
* Pairing happens **only over USB**: the host sends `provision` with Wi-Fi
  credentials, its host id and a fresh random 32-byte key. The keypad stores
  them in NVS and from then on accepts Wi-Fi connections only from that host
  with that key. A keypad serves one host; several keypads and several PCs
  on one network never interfere.
* The host opens the USB port only while pairing (or before the first keypad
  is paired), so `make flash` and serial monitors can use it the rest of the time.

### Secure channel

```
host   -> {"t":"hi","v":3,"host":"<host id>","n":"<16-byte nonce, hex>"}      (plain frame)
device -> {"t":"hi","v":3,"id":"kp-3fa21c","n":"<16-byte nonce, hex>"}         (plain frame)
                                                                                 or {"t":"no","why":"..."} and close
okm   = HKDF-SHA256(ikm = key, salt = nonce_host || nonce_device, info = "keypad v2", L = 64)
k_h2d = okm[0:32]    k_d2h = okm[32:64]
every later frame = AES-256-GCM(k_dir, nonce = 4 zero bytes || u64 BE counter, plaintext = JSON)
```

(The HKDF `info` string is a label, not the protocol version; it did not change.)

Counters start at 0 per direction and are never sent. A frame that fails to
decrypt closes the connection, so a wrong key, a replayed frame or a
reordered frame ends the session. Fresh nonces mean frames from an old
session can never be replayed into a new one. The keypad keeps an
authenticated session until another client completes the handshake
successfully, so an unauthenticated connection cannot knock the real host
off.

## Host → device

```json
{"t":"hello","v":3,"host":"MacBook","time":1727712000}          the host's greeting; the device answers with its own hello
{"t":"ping"}
{"t":"settings","brightness":80,"name":"Desk keypad"}
{"t":"status","sessions":[{"id":"abc12345","project":"money-mind","name":"Fix tests","state":"working","title":"Running","detail":"pytest","since":42,"mode":"acceptEdits"}],
 "sel":"abc12345","pinned":false,"queue":0,"paused":false,"menu":true}
{"t":"feed","sid":"abc12345","full":[{"k":"u","t":"fix the tests"},{"k":"c","t":"Running the tests first."},{"k":"t","t":"Bash(pytest)"}]}
{"t":"feed","sid":"abc12345","drop":0,"add":[{"k":"r","t":"Error: exit status 1"}]}
{"t":"screen","id":"p-82","tpl":"select","tone":"warn","title":"Bash command","project":"money-mind",
 "body":"Run tests\npytest","q":"Do you want to proceed?","timeout":300,
 "items":["Yes","Yes, and don't ask again for pytest:*","No"]}
{"t":"screen","id":"q-9","tpl":"select","title":"Database","q":"Which database?","project":"api","items":["Postgres","SQLite","MySQL"]}
{"t":"screen","id":"q-10","tpl":"multi","title":"Checks","q":"Which checks?","items":["lint","test","build"]}
{"t":"screen","id":"s-11","tpl":"prompt","title":"Claude finished","project":"api","items":["continue","Write tests"],
 "notes":["keep going","Add tests for the change."],"esc":"done"}
{"t":"close","id":"p-82","why":"timeout"}
{"t":"toast","text":"Queued for api: Write tests","level":"info","ms":2500}
{"t":"provision","ssid":"Home","pass":"...","host":"h-5c1e2a9b","key":"<64 hex>","name":"Desk keypad"}   USB only
{"t":"unpair"}                                                      USB, or the paired host over Wi-Fi
{"t":"ota_begin","size":1234567,"md5":"<32 hex>"}                   Wi-Fi only
{"t":"ota_data","off":0,"d":"<base64, <= 2048 bytes raw>"}
{"t":"ota_end"}
```

`name` is the session's title (as on its terminal tab), `mode` Claude Code's
permission mode (`default`, `acceptEdits`, `plan`, `auto`, `dontAsk`,
`bypassPermissions`). `state` is one of `idle`, `thinking`, `working`, `tool`,
`permission`, `question`, `input`, `done`, `stopped`, `continuing`, `failed`
(the keypad's wording for them lives in `ui.cpp`, the tray's in `tray.py`).

`feed` carries the selected session's transcript (`sid` = `sel`). `full` replaces
it; `drop` + `add` removes that many of the oldest entries and appends, so a new
message costs one small frame instead of the whole transcript. `status` stays
small and is sent whenever state changes; the host sends a `full` feed after
every connect and whenever `sel` changes (the keypad empties its transcript when
`sel` changes). A feed for a session that is not `sel` is ignored. The feed mirrors
the transcript, which the host follows live (it reads the session's transcript file, so Claude's text appears as it
is written, not only when a hook fires), oldest first: `u` the user's prompt
(≤ 2000 bytes), `c` Claude's text (whole, ≤ 8000 bytes), `t` a tool call, `r` an
error. Claude's Markdown arrives already laid out for the keypad's fixed-width
screen (`host/keypad/core/markdown.py`): headings, lists with hanging indents,
quotes, tables and code blocks are drawn as text, with three style markers:
`\u0001` toggles bold, `\u0002` code, `\u0003` dim (italics, quotes). Styles are
closed at the end of every line. Characters the keypad's font cannot draw are
mapped to look-alikes before they are sent. The host drops the oldest entries
until their text fits the keypad's 12000-byte transcript buffer and the
message fits; `since` counts from the start of the current turn. While a
request is on screen, `sel` is the session it came from. `menu` means Enter on
the status screen opens *Send to Claude* (there are saved prompts).

### Keys

The layout is fixed in the firmware, modelled on Claude Code's keyboard use:

```
[1] [2] [3] [4 up]        1-3  pick option 1-3 directly
[5] [6] [7] [8 down]      4/8  move the cursor / scroll    (encoder turn: same)
                          7    Enter                       6    session list (status screen)
                          5    Esc                         (knob press: Enter, sent as key 7)
```

### Templates

Every template is a list of `items` with a cursor, which starts on the first.

| `tpl` | Shows | Answer |
|---|---|---|
| `select` | a dialog box: `title`, `body` (scrollable: up from the first option), `q`, numbered `items` | `pick` with `idx` |
| `multi` | the same with checkboxes and a final *Submit* row; 1–3 and Enter tick | `submit` with `sel` |
| `prompt` | the transcript of `sel`, then numbered `items`, each with an optional dim `notes` entry | `pick` with `idx` |

`diff: true` marks a `body` that is a diff (edit approvals): the keypad
colors lines starting with `+` green and `-` red.

`esc` is what key 5 sends, and only the screens that set it
accept it: `done` on the "Claude finished" screen (stop there, no continue) and
`back` on menus. **A decision (permission, question, multi-select) has no `esc`:
it is answered on the keypad, and Esc does nothing.** `tone` colors the dialog: `accent`
(default) · `ok` · `warn` · `danger` · `dim` · `info`.

The session list (key 6) is drawn from `sessions` on the device; choosing one
sends `session` with its `sid`, choosing *Follow latest activity* (or Esc on
the status screen while a session is pinned) sends `session` with
`act:"follow"`. Enter on the status screen sends a `press` with `act:"menu"`
when `menu` is set.

## Device → host

```json
{"t":"hello","v":3,"id":"kp-3fa21c","fw":"3.0.0","name":"Desk keypad","paired":true,
 "wifi":{"state":"up","ssid":"Home","ip":"192.168.1.40","rssi":-51},"bat":87}
{"t":"pong","bat":87,"wifi":"up","rssi":-51}                    (bat -1 = unknown / on USB power)
{"t":"press","id":"p-82","key":1,"act":"pick","idx":0}       (number key 1 = option 1)
{"t":"press","id":"q-9","key":7,"act":"pick","idx":1}          (Enter on the highlighted option)
{"t":"press","id":"q-10","key":7,"act":"submit","sel":[0,2]}
{"t":"press","id":"s-11","key":5,"act":"done"}                   (Esc on the "Claude finished" screen)
{"t":"press","id":"status","key":7,"act":"menu"}
{"t":"session","act":"select","sid":"9f0e1d2c"}
{"t":"session","act":"follow"}
{"t":"provisioned","ok":true}                                   (USB only)
{"t":"ota","off":2048}                                          (ack per chunk: off = that chunk's offset; "ok":false,"err" on failure; "ok":true|false after ota_end)
```

Battery and Wi-Fi state travel in `hello` (at connect) and `pong` (every ping),
so the host always has them; there are no separate reports.

## Liveness

* The host initiates every connection and sends `hello` first; the keypad
  answers with `hello`. Nothing else is accepted from a host that has not said
  hello. The keypad no longer announces itself.
* The host sends `ping` every 2 s and the keypad answers `pong`. The keypad
  shows *Waiting for your computer* after 6 s without host traffic. The host
  drops a link after 8 s without keypad traffic and reconnects.
* On (re)connect the host sends `hello`, then `settings`, `status` and the
  active `screen`, if there is one.
* A v2 keypad cannot talk to a v3 host (and the other way round): flash it
  once over USB. The host logs "incompatible keypad" with both versions.

## Reliability and safety rules

* **No acks:** Wi-Fi is TCP and every frame is authenticated with a counter,
  so frames arrive once and in order or the link ends. On reconnect the host
  sends the active `screen` again; the keypad dedupes by `id`.
* **One screen at a time:** the host queues interactive requests FIFO across
  all Claude Code sessions. A new `screen` replaces the current one.
* **Answer once:** after a `press` the device closes that screen locally
  (back to the status screen) and ignores it from then on. A `screen` whose
  id equals the last answered id makes the device re-send its cached
  `press` (the answer was lost on a reconnect).
* **Clean presses only:** a press counts only if no other key is held
  (matrix ghosting), and only if it started at least 150 ms after the screen
  appeared (stale presses).
* **Host side:** the host accepts a `press` only for the active screen id,
  and only if the screen offered it: Esc (key 5 or 0) with the screen's
  `esc` (when it has one), Enter (key 7) on an existing option, a number key 1–3 on its own option,
  or Submit with valid picks.
  Timeout, a lost keypad, `done`, or anything malformed → the hook returns `{}`
  and Claude Code shows its own UI. Nothing is ever auto-approved.
* **Answered elsewhere anyway:** the host follows the session's transcript. If
  Claude moves on (new conversation lines appear) while a request is open, the
  request was answered in the terminal after all: the host sends `close` with
  `why:"pc"` and the hook returns no decision. Typing at the PC does *not* take a
  request off the keypad.
