# Keypad wire protocol v2

One message = one JSON object with a `t` (type) field. The same messages
travel over two transports:

| Transport | Framing | Security |
|---|---|---|
| USB CDC (ESP32-S3 native USB, VID `0x303A`) | one compact JSON object per `\n`-terminated line | physical cable = trusted |
| Wi-Fi, TCP port `7470` | `u16` big-endian length + payload | pairing key, per-session AES-256-GCM |

Limits: host → device ≤ 3800 bytes per message, device → host ≤ 1024 bytes.
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
{"t":"status","sessions":[{"id":"abc12345","project":"money-mind","state":"working","title":"Running","detail":"go test ./...","since":42}],
 "sel":"abc12345","pinned":false,"queue":0,"paused":false,
 "keys":{"1":{"label":"Shortcuts","act":"menu"}}}
{"t":"screen","id":"p-82","tpl":"prompt","tone":"warn","title":"Permission","project":"money-mind",
 "body":"Bash: go test ./...","timeout":300,"click":"pc",
 "keys":{"1":{"label":"Allow","act":"allow","tone":"ok"},"4":{"label":"Deny","act":"deny","tone":"danger"},"8":{"label":"PC","act":"pc"}}}
{"t":"screen","id":"q-9","tpl":"list","title":"Which database?","project":"api","items":["Postgres","SQLite","MySQL"],"click":"pc"}
{"t":"screen","id":"q-10","tpl":"multi","title":"Which checks?","items":["lint","test","build"],"click":"pc"}
{"t":"close","id":"p-82","why":"timeout"}
{"t":"toast","text":"Worker started in api","level":"info","ms":2500}
{"t":"provision","ssid":"Home","pass":"...","host":"h-5c1e2a9b","key":"<64 hex>","name":"Desk keypad"}   USB only
{"t":"unpair"}                                                      USB, or the paired host over Wi-Fi
{"t":"ota_begin","size":1234567,"md5":"<32 hex>"}
{"t":"ota_data","off":0,"d":"<base64, <= 2048 bytes raw>"}
{"t":"ota_end"}
```

### Templates

| `tpl` | Keys | Encoder |
|---|---|---|
| `prompt` | the `keys` map (key `"1"`–`"8"` → label, act, tone) | rotate scrolls `body`, click → `click` act |
| `list` | 1–8 pick the item at that position on the current page (8 per page) | rotate changes page, click → `click` act |
| `multi` | 1–7 toggle items, 8 confirms | click → `click` act |

`tone`: `accent` (default) · `ok` · `warn` · `danger` · `dim` · `info`.

Debug firmware builds (`pio run -e keypad-debug`) also accept
`{"t":"key","key":n}` over USB to simulate a press, and report key events
as `log` messages. Release builds do not.

On the status screen the encoder rotates through `sessions` locally and
sends `session`; a click sends `session` with `act:"follow"`.

## Device → host

```json
{"t":"hello","v":2,"id":"kp-3fa21c","fw":"2.0.0","name":"Desk keypad","paired":true,"link":"usb",
 "wifi":{"state":"up","ssid":"Home","ip":"192.168.1.40","rssi":-51},"bat":87}
{"t":"pong"}
{"t":"ack","id":"p-82"}
{"t":"press","id":"p-82","key":1,"act":"allow"}
{"t":"press","id":"q-9","key":2,"act":"item","idx":1}
{"t":"press","id":"q-10","key":8,"act":"confirm","sel":[0,2]}
{"t":"press","id":"p-82","key":0,"act":"pc"}                  (encoder click, key 0)
{"t":"press","id":"status","key":1,"act":"menu"}
{"t":"session","act":"select","sid":"9f0e1d2c"}
{"t":"session","act":"follow"}
{"t":"provisioned","ok":true}
{"t":"wifi","state":"up","ssid":"Home","ip":"192.168.1.40","rssi":-51}
{"t":"ota","off":2048}                                          (ack per chunk; "ok":true|false on end)
{"t":"log","level":"info","msg":"..."}
```

## Liveness

* The device sends `hello` at boot and every 3 s until it gets `hello_ack`.
  A host that opens a link sends `who` first, and the device answers with
  `hello` at once, even if it still thinks an earlier host process is
  connected.
* The host sends `ping` every 2 s and the device answers `pong`. The device
  shows *Waiting for PC* after 6 s without host traffic. The host drops a
  link after 8 s without device traffic and reconnects.
* On (re)connect the host sends `hello_ack`, `settings`, `status` and the
  active `screen`, if there is one.

## Reliability and safety rules

* **Ack:** the device acks every `screen` and `close`. If there is no ack
  within 1.5 s the host re-sends it once. The device dedupes by `id`.
* **One screen at a time:** the host queues interactive requests FIFO across
  all Claude Code sessions. A new `screen` replaces the current one.
* **Answer once:** after a `press` the device closes that screen locally
  (back to the status screen) and ignores it from then on. A `screen` whose
  id equals the last answered id makes the device re-send its cached
  `press` (the answer was lost on a reconnect).
* **Clean presses only:** a press counts only if no other key is held
  (matrix ghosting), and only if it started at least 150 ms after the screen
  appeared (stale presses).
* **Host side:** the host accepts a `press` only for the active screen id.
  Timeout, `pc`, disconnect or anything malformed → the hook returns `{}`
  and Claude Code shows its own UI. Nothing is ever auto-approved.
