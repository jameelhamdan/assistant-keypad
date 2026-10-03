# Keypad wire protocol v2

One message = one JSON object with a `t` (type) field. The same messages
travel over two transports:

| Transport | Framing | Security |
|---|---|---|
| USB CDC (ESP32-S3 native USB, VID `0x303A`) | one compact JSON object per `\n`-terminated line | physical cable = trusted |
| Wi-Fi, TCP port `7470` | `u16` big-endian length + payload | pairing key, per-session AES-256-GCM |

Limits: host → device ≤ 16000 bytes per message, device → host ≤ 1024 bytes.
Anything larger, anything that is not a JSON object with `t`, and any unknown
`t` is dropped and counted.

## Discovery and pairing (Wi-Fi)

* The keypad advertises mDNS `_ckeypad._tcp` on port 7470, instance name =
  its id (`kp-` + last 3 MAC bytes in hex), TXT `id=`, `fw=`, `v=2`,
  `paired=0|1`.
* The **host connects to the keypad** (never the other way round). It only
  connects to ids it has paired, and it keeps the last IP of each keypad as
  a fallback when mDNS is blocked.
* Pairing happens **only over USB**: the host sends `provision` with Wi-Fi
  credentials, its host id and a fresh random 32-byte key. The keypad stores
  them in NVS and from then on accepts Wi-Fi connections only from that host
  with that key. A keypad serves one host; several keypads and several PCs
  on one network never interfere.

### Secure channel

```
host   -> {"t":"hi","v":2,"host":"<host id>","n":"<16-byte nonce, hex>"}      (plain frame)
device -> {"t":"hi","v":2,"id":"kp-3fa21c","n":"<16-byte nonce, hex>"}         (plain frame)
                                                                                 or {"t":"no","why":"..."} and close
okm   = HKDF-SHA256(ikm = key, salt = nonce_host || nonce_device, info = "keypad v2", L = 64)
k_h2d = okm[0:32]    k_d2h = okm[32:64]
every later frame = AES-256-GCM(k_dir, nonce = 4 zero bytes || u64 BE counter, plaintext = JSON)
```

Counters start at 0 per direction and are never sent. A frame that fails to
decrypt closes the connection, so a wrong key, a replayed frame or a
reordered frame ends the session. Fresh nonces mean frames from an old
session can never be replayed into a new one. The keypad keeps an
authenticated session until another client completes the handshake
successfully, so an unauthenticated connection cannot knock the real host
off.

## Host → device

```json
{"t":"who"}                                                     the device answers with hello
{"t":"hello_ack","v":2,"host":"MacBook","time":1727712000}
{"t":"ping"}
{"t":"settings","theme":"dark","brightness":80,"name":"Desk keypad"}
{"t":"status","sessions":[{"id":"abc12345","project":"money-mind","name":"Fix tests","state":"working","title":"Running","detail":"pytest","since":42,"mode":"acceptEdits"}],
 "sel":"abc12345","pinned":false,"queue":0,"paused":false,"menu":true,
 "log":[{"k":"u","t":"fix the tests"},{"k":"c","t":"Running the tests first."},{"k":"t","t":"Bash(pytest)"},{"k":"r","t":"Error: exit status 1"}]}
{"t":"screen","id":"p-82","tpl":"select","tone":"warn","title":"Bash command","project":"money-mind",
 "body":"Run tests\npytest","q":"Do you want to proceed?","timeout":300,"esc":"pc",
 "items":["Yes","Yes, and don't ask again for pytest:*","No"]}
{"t":"screen","id":"q-9","tpl":"select","title":"Database","q":"Which database?","project":"api","items":["Postgres","SQLite","MySQL"],"esc":"pc"}
{"t":"screen","id":"q-10","tpl":"multi","title":"Checks","q":"Which checks?","items":["lint","test","build"],"esc":"pc"}
{"t":"screen","id":"s-11","tpl":"prompt","title":"Claude finished","project":"api","items":["continue","Write tests"],
 "notes":["keep going","Add tests for the change."],"esc":"pc"}
{"t":"close","id":"p-82","why":"timeout"}
{"t":"toast","text":"Queued for api: Write tests","level":"info","ms":2500}
{"t":"provision","ssid":"Home","pass":"...","host":"h-5c1e2a9b","key":"<64 hex>","name":"Desk keypad"}   USB only
{"t":"unpair"}                                                      USB, or the paired host over Wi-Fi
{"t":"ota_begin","size":1234567,"md5":"<32 hex>"}
{"t":"ota_data","off":0,"d":"<base64, <= 2048 bytes raw>"}
{"t":"ota_end"}
```

`name` is the session's title (as on its terminal tab), `mode` Claude Code's
permission mode when it isn't the default (`acceptEdits`, `plan`,
`bypassPermissions`). `log` mirrors the selected session's transcript, oldest
first: `u` the user's prompt (≤ 2000 bytes), `c` Claude's text (whole, ≤ 8000
bytes), `t` a tool call, `r` an outcome. Claude's Markdown arrives styled:
`\u0001` toggles bold (headings, `**bold**`), `\u0002` toggles code (`` `code` ``,
code blocks); links are reduced to their text and fences and table separator
rows are dropped. The host drops the oldest entries until their text fits the
keypad's 12000-byte transcript buffer and the message fits; `since`
counts from the start of the current turn. While a request is on screen,
`sel` is the session it came from. `menu` means Enter on the status screen
opens *Send to Claude* (there are saved prompts).

### Keys

The layout is fixed in the firmware, modelled on Claude Code's keyboard use:

```
[1] [2] [3] [4 up]        1-3  pick option 1-3 directly
[5] [6] [7] [8 down]      4/8  move the cursor / scroll    (encoder turn: same)
                          7    Enter                       6    session list (status screen)
                          5    Esc                         (encoder click: same, key 0: never a decision)
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

`esc` is what key 5 sends (`pc`: leave it to the computer, `back`: close a
menu); without it, Esc does nothing. `tone` colors the dialog: `accent`
(default) · `ok` · `warn` · `danger` · `dim` · `info`.

Debug firmware builds (`pio run -e keypad-debug`) also accept
`{"t":"key","key":n}` over USB to simulate a press, and report key events
as `log` messages. Release builds do not.

The session list (key 6) is drawn from `sessions` on the device; choosing one
sends `session` with its `sid`, choosing *Follow latest activity* (or Esc on
the status screen while a session is pinned) sends `session` with
`act:"follow"`. Enter on the status screen sends a `press` with `act:"menu"`
when `menu` is set.

## Device → host

```json
{"t":"hello","v":2,"id":"kp-3fa21c","fw":"2.0.0","name":"Desk keypad","paired":true,"link":"usb",
 "wifi":{"state":"up","ssid":"Home","ip":"192.168.1.40","rssi":-51},"bat":87}
{"t":"pong","bat":87}                                           (bat -1 = unknown / on USB power)
{"t":"ack","id":"p-82"}
{"t":"press","id":"p-82","key":1,"act":"pick","idx":0}       (number key 1 = option 1)
{"t":"press","id":"q-9","key":7,"act":"pick","idx":1}          (Enter on the highlighted option)
{"t":"press","id":"q-10","key":7,"act":"submit","sel":[0,2]}
{"t":"press","id":"p-82","key":5,"act":"pc"}                   (Esc; key 0 = the encoder click, which is Esc too)
{"t":"press","id":"status","key":7,"act":"menu"}
{"t":"session","act":"select","sid":"9f0e1d2c"}
{"t":"session","act":"follow"}
{"t":"mic","act":"start"}                                      (push-to-talk: sent when the mic button goes down, "stop" when it comes up)
{"t":"provisioned","ok":true}
{"t":"wifi","state":"up","ssid":"Home","ip":"192.168.1.40","rssi":-51}
{"t":"ota","off":2048}                                          (ack per chunk: off = that chunk's offset; "ok":false,"err" on failure; "ok":true|false after ota_end)
{"t":"log","level":"info","msg":"..."}
```

## Liveness

* The device sends `hello` at boot and every 3 s until it gets `hello_ack`.
  A host that opens a link sends `who` first, and the device answers with
  `hello` at once, even if it still thinks an earlier host process is
  connected.
* The host sends `ping` every 2 s and the device answers `pong`. The device
  shows *Waiting for your computer* after 6 s without host traffic. The host drops a
  link after 8 s without device traffic and reconnects.
* On (re)connect the host sends `hello_ack`, `settings`, `status` and the
  active `screen`, if there is one.

## Reliability and safety rules

* **Ack:** the device acks every `screen` and `close`. If there is no ack
  within 1.5 s the host re-sends it once. The device dedupes by `id`.
* **One screen at a time:** the host queues interactive requests FIFO across
  all Claude Code sessions. A new `screen` replaces the current one.
* **Answer goes back:** a `press` is sent on the link its `screen` came
  from (USB and Wi-Fi may lead to different computers), or on any live host
  link if that one is gone.
* **Answer once:** after a `press` the device closes that screen locally
  (back to the status screen) and ignores it from then on. A `screen` whose
  id equals the last answered id makes the device re-send its cached
  `press` (the answer was lost on a reconnect).
* **Clean presses only:** a press counts only if no other key is held
  (matrix ghosting), and only if it started at least 150 ms after the screen
  appeared (stale presses).
* **Host side:** the host accepts a `press` only for the active screen id,
  and only if the screen offered it: Esc (key 5 or 0) with the screen's
  `esc`, Enter (key 7) on an existing option, a number key 1–3 on its own option,
  or Submit with valid picks.
  Timeout, `pc`, disconnect or anything malformed → the hook returns `{}`
  and Claude Code shows its own UI. Nothing is ever auto-approved.
