# Wire protocol v3

One message = one JSON object with a `t` field. Wi-Fi is the working link; USB is for setup.

| Transport | Used for | Framing | Security |
|---|---|---|---|
| Wi-Fi, TCP port 7470 | everything | `u16` big-endian length + payload | pairing key, per-session AES-256-GCM |
| USB CDC (VID `0x303A`) | setup: `hello`, `provision`, `unpair` | one JSON object per `\n`-terminated line | the cable |

Limits: host → device ≤ 16000 bytes per message, device → host ≤ 1024. Larger messages, non-objects, messages without `t` and unknown `t` are dropped and counted. Each side ignores fields it does not know.

## Discovery and pairing

- The keypad advertises mDNS `_ckeypad._tcp` on 7470. Instance name = its id (`kp-` + last 3 MAC bytes, hex). TXT: `id`, `fw`, `v=3`, `paired=0|1`.
- The host connects to the keypad, only to ids it has paired, using mDNS and the last known IP as fallback.
- Pairing is over USB only: `provision` carries Wi-Fi credentials, the host id and a fresh random 32-byte key; the keypad stores them in NVS and then accepts Wi-Fi connections only from paired hosts with their keys. A keypad holds up to 3 hosts (one Wi-Fi network, shared) and serves one at a time; `provision` from another computer adds it, and the Wi-Fi credentials of the latest `provision` apply.
- The host opens USB ports only while a keypad is being set up.

### Secure channel

```
host   -> {"t":"hi","v":3,"host":"<host id>","n":"<16-byte nonce, hex>"}     plain frame ("take":1 added to replace another host)
device -> {"t":"hi","v":3,"id":"kp-3fa21c","n":"<16-byte nonce, hex>"}       plain frame
          or {"t":"no","code":"busy","why":"...","host":"Work PC"} and close
okm   = HKDF-SHA256(ikm = key, salt = nonce_host || nonce_device, info = "keypad v3", L = 64)
k_h2d = okm[0:32]   k_d2h = okm[32:64]
later frames = AES-256-GCM(k_dir, nonce = 4 zero bytes || u64 BE counter, plaintext = JSON)
```

`no.code` is one of `bad_hello` (protocol version), `not_paired`, `other_host` (paired, but not with this host), `busy` (another host holds the keypad; `host` is its name) and `bad_nonce`; `why` says the same in words. A second host gets `busy` while the first has been heard from in the last 6 s, unless it sends `take`; the same host reconnecting always replaces its old session. Older firmware sends only `why`.

`info` is a fixed label. Counters start at 0 per direction and are never sent; a frame that fails to decrypt closes the connection (wrong key, replay, reorder). The keypad keeps an authenticated session until another client completes a handshake, so an unauthenticated connection cannot drop the real host.

## Host → device

```json
{"t":"hello","v":3,"host":"MacBook","time":1727712000}
{"t":"ping"}
{"t":"settings","brightness":80,"name":"Desk keypad"}
{"t":"status","sessions":[{"id":"abc12345","project":"api","name":"Fix tests","state":"working","title":"Running","detail":"pytest","since":42,"mode":"acceptEdits"}],
 "sel":"abc12345","queue":0,"paused":false,"menu":true,"quick":["Tests","Commit"],"queued":""}
{"t":"feed","sid":"abc12345","full":[{"k":"u","t":"fix the tests"},{"k":"c","t":"Running the tests first."},{"k":"t","t":"Bash(pytest)"}]}
{"t":"screen","id":"p-82","tpl":"select","tone":"warn","title":"Bash command","project":"api",
 "body":"Run tests\npytest","q":"Do you want to proceed?","timeout":300,
 "items":["Yes","Yes, and don't ask again for pytest:*","No"]}
{"t":"screen","id":"q-10","tpl":"multi","title":"Checks","q":"Which checks?","items":["lint","test","build"]}
{"t":"screen","id":"s-11","tpl":"prompt","title":"Claude finished","project":"api",
 "items":["continue","Write tests"],"esc":"done"}
{"t":"close","id":"p-82","why":"timeout"}
{"t":"toast","text":"Queued for api: Write tests","level":"info","ms":2500}   also "Sent to api: Tests" (idle session, run at once), "Done in api: Tests" / "Failed in api: Tests" when that run ends, and "Claude finished: api" (level ok) when nothing is asked
{"t":"provision","ssid":"Home","pass":"...","host":"h-5c1e2a9b","key":"<64 hex>","name":"Desk keypad"}   USB only
{"t":"unpair"}                                           USB: forget every host and the Wi-Fi; over Wi-Fi: forget this host only
{"t":"ota_begin","size":1234567,"md5":"<32 hex>"}        Wi-Fi only
{"t":"ota_data","off":0,"d":"<base64, <= 2048 bytes raw>"}
{"t":"ota_end"}
```

**status.** Sent whenever state changes. `name` is the session title (terminal tab), `mode` the Claude Code permission mode (`default`, `acceptEdits`, `plan`, `auto`, `dontAsk`, `bypassPermissions`), `since` seconds since the turn started. `state` is one of `working`, `continuing` (busy after a continue from the keypad), `asking` (a request waits for you), `stopped` (Claude finished, waits for you), `idle`. `sel` is the shown session; while a request is on screen it is the session the request came from. A session chosen on the keypad stays shown until a request or a finished turn in another session needs you. `menu` means Enter on the status screen opens *Send to Claude* (saved prompts exist). `quick` (optional) lists up to 5 short labels: keys 1, 2, 3, 4 and 8 on the status screen send those saved prompts at once with `press` `{"id":"status","act":"quick","idx":0}`; the keypad ignores them for 1.5 s after a request screen goes away. `queued` is the label of a saved prompt waiting for the shown session (a busy session gets it with its next tool result or stop; an idle session is resumed headless by the host and never queues, unless `behavior.run_when_idle` is off); while it is set, Esc on the status screen sends `press` `{"id":"status","act":"unqueue"}` and the host takes it back.

**feed.** The transcript of the session `sel`, oldest first, always whole (`full`). Sent after every connect, when `sel` changes and when the transcript changes. A feed for any other session is ignored; the keypad clears its transcript when `sel` changes. Entry kinds: `u` your prompt (≤ 2000 bytes), `c` Claude's text (≤ 8000), `t` tool call, `r` error. The host drops the oldest entries until the text fits the keypad's 12000-byte buffer and the message fits the limit. Claude's Markdown is already laid out for the fixed-width screen (`host/keypad/core/markdown.py`) with three style markers: `\u0001` toggles bold, `\u0002` code, `\u0003` dim. Styles close at each line end. Characters the font cannot draw are mapped to look-alikes.

### Keys

```
[1] [2] [3] [4]          1-3  pick option 1-3        7  Enter
[5] [6] [7] [8]          4/8  saved prompts 4, 5     5  Esc
                         knob: turn = cursor / scroll (faster when turned fast), press = key 7        6  session list
```

### Templates

Every template is a list of `items` with a cursor starting on the first.

| `tpl` | Shows | Answer |
|---|---|---|
| `select` | dialog: `title`, scrollable `body` (up from the first option), `q`, numbered `items` | `pick` with `idx` |
| `multi` | same with checkboxes and a final *Submit* row; 1–3 and Enter tick | `submit` with `sel` |
| `prompt` | the transcript of `sel`, then numbered `items` | `pick` with `idx` |

`diff: true` marks a `body` that is a diff: lines starting `+` are green, `-` red. `tone`: `accent` (default), `ok`, `warn`, `danger`, `dim`, `info`.

`esc` is what key 5 sends, and only screens that set it accept key 5: `done` on "Claude finished" (stop, no continue), `back` on menus. A decision (permission, question, multi-select) has no `esc`: Esc does nothing.

Key 6 opens the session list, drawn from `status.sessions`. Choosing a session sends `session` with `act:"select"` and its `sid`. Enter on the status screen sends `press` with `act:"menu"` when `menu` is set.

## Device → host

```json
{"t":"hello","v":3,"id":"kp-3fa21c","fw":"3.0.0","name":"Desk keypad","paired":true,
 "wifi":{"state":"up","ssid":"Home","ip":"192.168.1.40","rssi":-51},"bat":87}
{"t":"pong","bat":87,"wifi":"up","rssi":-51}
{"t":"press","id":"p-82","key":1,"act":"pick","idx":0}
{"t":"press","id":"q-9","key":7,"act":"pick","idx":1}
{"t":"press","id":"q-10","key":7,"act":"submit","sel":[0,2]}
{"t":"press","id":"s-11","key":5,"act":"done"}
{"t":"press","id":"status","key":7,"act":"menu"}
{"t":"press","id":"status","key":1,"act":"quick","idx":0}
{"t":"press","id":"status","key":5,"act":"unqueue"}
{"t":"session","act":"select","sid":"9f0e1d2c"}
{"t":"provisioned","ok":true}                  USB only
{"t":"ota","off":2048}                         ack per chunk; "ok":false,"err" on failure; "ok":true|false after ota_end
```

`bat` is a percentage; -1 = unknown or on USB power. Battery and Wi-Fi state travel only in `hello` and `pong`.

## Liveness

- The host opens the connection and sends `hello`; the keypad answers with its own. Nothing else is accepted before that.
- The host pings every 2 s. The keypad shows *Waiting for your computer* after 6 s without host traffic; the host drops the link after 8 s without keypad traffic and reconnects.
- On (re)connect the host sends `hello`, `settings`, `status`, `feed` and the active `screen`.
- Both sides must speak the same `v`, otherwise the host logs "incompatible keypad" and the tray says to flash it over USB. Adding fields or message types does not bump `v`.

## Safety rules

- **No acks.** TCP plus counters: frames arrive once, in order, or the link ends. A frame leaves in one write; if the write is cut short the session ends, because the stream could not be resynchronised. On reconnect the host re-sends the active `screen`; the keypad dedupes by `id`.
- **One screen at a time.** Requests queue FIFO across all sessions; a new `screen` replaces the current one.
- **Answer once.** After a `press` the keypad closes the screen and ignores it. A `screen` with the last answered id makes it re-send the cached `press` (the answer was lost in a reconnect).
- **Clean presses only.** A press counts only if no other key is held and it started ≥ 150 ms after the screen appeared.
- **Host validation.** A `press` is accepted only for the active screen id and only if the screen offered it: key 5 with the screen's `esc`; Enter (7) on an existing option; a number key 1–3 on its own option; Submit with valid picks. A press for any earlier screen is dropped. Timeout, lost keypad, `done` or anything malformed makes the hook return `{}`, and Claude Code shows its own UI.
- **Answered elsewhere.** If new conversation lines appear in the transcript while a request is open, it was answered in the terminal: the host sends `close` with `why:"pc"` and the hook returns no decision. Typing at the PC does not remove a request.
- **Why a screen goes away.** `close.why` is `answered` (a keypad answered), `pc` (answered or handed back at the PC), `timeout`, `disconnected`, `done` (the dialog is over) or `stale` (a press for a screen the host no longer has).
