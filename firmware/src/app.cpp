// App logic: host messages -> model, key presses -> host messages, and the
// safety rules (clean presses only, stale-press guard, answer once per screen).
#include "app.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <stdarg.h>
#include <string.h>

#include "config.h"
#include "crypto.h"
#include "input.h"
#include "link.h"
#include "model.h"
#include "mem.h"
#include "ota.h"
#include "store.h"
#include "text/text.h"

Model model;

namespace {

Stored stored;
bool dirty = true;

struct LinkState {
    bool acked;          // hello_ack received on this link
    uint32_t lastRx;     // last valid host message
    uint32_t lastHello;
};
LinkState links[2];
Src hostSrc = Src::Usb;  // link that last completed a hello
Src screenSrc = Src::Usb;  // link the current screen came from: its answer goes back there

char lastAnswered[49] = "";
char cachedPress[256] = "";
size_t cachedLen = 0;
uint32_t bootAt = 0;
uint32_t lastBattery = 0;
uint32_t logHash = 0;   // of the transcript last applied

void markDirty() { dirty = true; }

void wake() {
    model.lastActivity = millis();
    if (model.dimmed) {
        model.dimmed = false;
        markDirty();
    }
}

// A key or knob turn: true if it only woke a dim screen (then it does nothing else).
bool wakeOnly() {
    bool was = model.dimmed;
    wake();
    return was;
}

// ---- sending ----------------------------------------------------------------------

void sendTo(Src src, JsonDocument &doc) {
    char out[1024];
    size_t n = serializeJson(doc, out, sizeof(out));
    if (n > 0 && n < sizeof(out)) linkSend(src, out, n);
}

void sendHost(JsonDocument &doc) {
    // Prefer the link the host last greeted us on; fall back to any live one.
    Src order[2] = {hostSrc, hostSrc == Src::Usb ? Src::Net : Src::Usb};
    for (Src s : order) {
        if (links[(int)s].acked) {
            sendTo(s, doc);
            return;
        }
    }
}

const char *wifiName(WifiState w) {
    switch (w) {
        case WifiState::Up: return "up";
        case WifiState::Connecting: return "connecting";
        case WifiState::Failed: return "fail";
        default: return "off";
    }
}

void sendHello(Src src) {
    JsonDocument d;
    d["t"] = "hello";
    d["v"] = PROTOCOL_VERSION;
    d["id"] = model.id;
    d["fw"] = KEYPAD_FW_VERSION;
    d["name"] = model.name;
    d["paired"] = stored.paired;
    d["link"] = src == Src::Usb ? "usb" : "wifi";
    JsonObject w = d["wifi"].to<JsonObject>();
    w["state"] = wifiName(model.wifi);
    if (model.ssid[0]) w["ssid"] = model.ssid;
    if (model.wifi == WifiState::Up) {
        w["ip"] = model.ip;
        w["rssi"] = model.rssi;
    }
    if (model.battery >= 0) d["bat"] = model.battery;
    sendTo(src, d);
    links[(int)src].lastHello = millis();
}

void sendWifiReport() {
    JsonDocument d;
    d["t"] = "wifi";
    d["state"] = wifiName(model.wifi);
    d["ssid"] = model.ssid;
    if (model.wifi == WifiState::Up) {
        d["ip"] = model.ip;
        d["rssi"] = model.rssi;
    }
    sendHost(d);
}

// The reply to a screen goes to the host that showed it (USB and Wi-Fi can
// be different computers); if that link is gone, to any live host.
void sendAnswer(JsonDocument &doc) {
    if (links[(int)screenSrc].acked) sendTo(screenSrc, doc);
    else sendHost(doc);
}

void sendOta(Src src, int off, bool haveOk, bool ok, const char *err) {
    JsonDocument d;
    d["t"] = "ota";
    d["off"] = off;
    if (haveOk) d["ok"] = ok;
    if (err) d["err"] = err;
    sendTo(src, d);
}

// ---- parsing helpers ----------------------------------------------------------------------

void copyText(char *dst, size_t n, const char *src) {
    strlcpy(dst, src ? src : "", n);
    textSanitize(dst);
}

Tone toneOf(const char *s) {
    if (!s) return Tone::Accent;
    if (!strcmp(s, "ok")) return Tone::Ok;
    if (!strcmp(s, "warn")) return Tone::Warn;
    if (!strcmp(s, "danger")) return Tone::Danger;
    if (!strcmp(s, "dim")) return Tone::Dim;
    if (!strcmp(s, "info")) return Tone::Info;
    return Tone::Accent;
}

// ---- host messages ---------------------------------------------------------------------------

void applySettings(JsonDocument &doc) {
    bool dark = strcmp(doc["theme"] | "dark", "light") != 0;
    int b = doc["brightness"] | (int)stored.brightness;
    b = b < 5 ? 5 : b > 100 ? 100 : b;
    const char *name = doc["name"] | "";
    bool changed = dark != stored.dark || b != stored.brightness || (name[0] && strcmp(name, stored.name) != 0);
    stored.dark = dark;
    stored.brightness = (uint8_t)b;
    if (name[0]) strlcpy(stored.name, name, sizeof(stored.name));
    model.dark = dark;
    model.brightness = stored.brightness;
    copyText(model.name, sizeof(model.name), stored.name);
    if (changed) storeSave(stored);
}

void applyStatus(JsonDocument &doc) {
    StatusModel &st = model.status;
    char prevSel[sizeof(st.sel)];
    strlcpy(prevSel, st.sel, sizeof(prevSel));
    st.n = 0;
    uint32_t now = millis();
    for (JsonObjectConst s : doc["sessions"].as<JsonArrayConst>()) {
        if (st.n >= MAX_SESSIONS) break;
        SessionInfo &x = st.s[st.n++];
        strlcpy(x.id, s["id"] | "", sizeof(x.id));
        copyText(x.project, sizeof(x.project), s["project"] | "");
        copyText(x.name, sizeof(x.name), s["name"] | "");
        strlcpy(x.state, s["state"] | "", sizeof(x.state));
        copyText(x.title, sizeof(x.title), s["title"] | "");
        copyText(x.detail, sizeof(x.detail), s["detail"] | "");
        strlcpy(x.mode, s["mode"] | "", sizeof(x.mode));
        x.startedAt = now - (uint32_t)(s["since"] | 0) * 1000u;
    }
    strlcpy(st.sel, doc["sel"] | "", sizeof(st.sel));
    st.pinned = doc["pinned"] | false;
    st.queue = doc["queue"] | 0;
    st.paused = doc["paused"] | false;
    st.menu = doc["menu"] | false;
    // the transcript: each entry's text goes into the pool, NUL-terminated.
    // Status arrives several times a second while Claude works; the renderer
    // re-wraps only when the transcript actually changed (FNV-1a over it).
    st.nLog = 0;
    size_t used = 0;
    uint32_t hash = 2166136261u;
    for (JsonObjectConst l : doc["log"].as<JsonArrayConst>()) {
        if (st.nLog >= MAX_LOG || !st.logText || used + 1 >= LOG_POOL) break;
        LogEntry &e = st.log[st.nLog++];
        const char *k = l["k"] | "c";
        e.k = k[0];
        e.off = (uint16_t)used;
        copyText(st.logText + used, LOG_POOL - used, l["t"] | "");
        size_t n = strlen(st.logText + used) + 1;
        hash = (hash ^ (uint8_t)e.k) * 16777619u;
        for (size_t i = 0; i < n; i++) hash = (hash ^ (uint8_t)st.logText[used + i]) * 16777619u;
        used += n;
    }
    if (hash != logHash) st.logVer++;
    logHash = hash;
    model.view = 0;
    for (uint8_t i = 0; i < st.n; i++) if (!strcmp(st.s[i].id, st.sel)) model.view = i;
    if (strcmp(prevSel, st.sel) != 0) model.logScroll = 0;   // another session: start at its newest line
    if (model.pick > st.n) model.pick = st.n;
}

void showScreen(JsonDocument &doc) {
    ScreenModel &sc = model.screen;
    const char *tpl = doc["tpl"] | "select";
    sc.tpl = !strcmp(tpl, "multi") ? Tpl::Multi : !strcmp(tpl, "prompt") ? Tpl::Prompt : Tpl::Select;
    strlcpy(sc.id, doc["id"] | "", sizeof(sc.id));
    sc.tone = toneOf(doc["tone"]);
    copyText(sc.title, sizeof(sc.title), doc["title"] | "");
    copyText(sc.project, sizeof(sc.project), doc["project"] | "");
    copyText(sc.body, sizeof(sc.body), doc["body"] | "");
    copyText(sc.q, sizeof(sc.q), doc["q"] | "");
    sc.diff = doc["diff"] | false;
    sc.nItems = 0;
    JsonArrayConst notes = doc["notes"].as<JsonArrayConst>();
    for (const char *it : doc["items"].as<JsonArrayConst>()) {
        if (sc.nItems >= MAX_ITEMS) break;
        copyText(sc.items[sc.nItems], sizeof(sc.items[0]), it);
        copyText(sc.notes[sc.nItems], sizeof(sc.notes[0]), notes[sc.nItems] | "");
        sc.picked[sc.nItems++] = false;
    }
    strlcpy(sc.esc, doc["esc"] | "", sizeof(sc.esc));
    sc.shownAt = millis();
    int timeout = doc["timeout"] | 0;
    sc.expiresAt = timeout > 0 ? sc.shownAt + (uint32_t)timeout * 1000u : 0;
    sc.cursor = 0;   // like Claude Code: the first option is highlighted
    sc.top = 0;
    sc.scroll = 0;
    sc.maxScroll = 0;
    sc.active = true;
    model.sentUntil = 0;
    model.mode = Mode::Screen;
    wake();   // a request: full brightness, so it catches your eye
}

void applyWifiConfig() { strlcpy(model.ssid, stored.ssid, sizeof(model.ssid)); }

void provision(JsonDocument &doc, Src src) {
    JsonDocument r;
    r["t"] = "provisioned";
    const char *key = doc["key"] | "";
    const char *host = doc["host"] | "";
    const char *ssid = doc["ssid"] | "";
    uint8_t newKey[sizeof(stored.key)];
    bool ok = host[0] && strlen(host) < sizeof(stored.host) && ssid[0] && strlen(ssid) < sizeof(stored.ssid) &&
              hexDecode(key, newKey, sizeof(newKey));
    if (!ok) {
        r["ok"] = false;
        r["err"] = "invalid settings";
        sendTo(src, r);
        return;
    }
    memcpy(stored.key, newKey, sizeof(newKey));
    strlcpy(stored.ssid, ssid, sizeof(stored.ssid));
    strlcpy(stored.pass, doc["pass"] | "", sizeof(stored.pass));
    strlcpy(stored.host, host, sizeof(stored.host));
    if (const char *n = doc["name"]; n && n[0]) strlcpy(stored.name, n, sizeof(stored.name));
    stored.paired = true;
    storeSave(stored);
    model.paired = true;
    copyText(model.name, sizeof(model.name), stored.name);
    r["ok"] = true;
    sendTo(src, r);
    applyWifiConfig();
    linkConfigure(stored);
    appToast("Paired. Joining Wi-Fi...", Tone::Ok, 3000);
}

void unpair() {
    stored.paired = false;
    stored.host[0] = stored.ssid[0] = stored.pass[0] = '\0';
    memset(stored.key, 0, sizeof(stored.key));
    storeSave(stored);
    model.paired = false;
    links[(int)Src::Net].acked = false;
    applyWifiConfig();
    linkConfigure(stored);
    appToast("Wi-Fi pairing removed", Tone::Warn, 3000);
}

void onKey(const KeyEvent &ev);

// Where to go when an update ends or fails: back to a pending screen if any.
Mode afterOta() { return model.screen.active ? Mode::Screen : Mode::Status; }

void onMessage(char *json, size_t len, Src src) {
    JsonDocument doc;
    if (deserializeJson(doc, json, len)) return;
    const char *t = doc["t"] | "";
    if (!t[0]) return;
    LinkState &L = links[(int)src];
    L.lastRx = millis();

    if (!strcmp(t, "who")) {   // a (new) host process wants our hello
        L.acked = false;
        sendHello(src);
        return;
    }
#ifdef KEYPAD_DEBUG
    if (!strcmp(t, "key")) {   // debug builds only: simulate a key press
        uint32_t now = millis();
        KeyEvent ev{(uint8_t)(doc["key"] | 0), KeyAction::Press, now, now, true};
        onKey(ev);
        markDirty();
        return;
    }
#endif
    if (!strcmp(t, "ping")) {
        if (!L.acked) sendHello(src);
        JsonDocument p;
        p["t"] = "pong";
        p["bat"] = model.battery;   // -1 = unknown / on USB power
        sendTo(src, p);
        return;
    }
    if (!strcmp(t, "hello_ack")) {
        L.acked = true;
        hostSrc = src;
        copyText(model.host, sizeof(model.host), doc["host"] | "");
        if (model.mode == Mode::Waiting || model.mode == Mode::Boot) model.mode = Mode::Status;
        markDirty();
        return;
    }
    // Pairing needs the cable (physical access).
    if (!strcmp(t, "provision")) {
        if (src == Src::Usb) provision(doc, src);
        markDirty();
        return;
    }
    // Everything below needs a greeted host.
    if (!L.acked) {
        sendHello(src);
        return;
    }
    // Forgetting works over the cable or from the paired host over Wi-Fi.
    if (!strcmp(t, "unpair")) {
        unpair();
        markDirty();
        return;
    }

    if (!strcmp(t, "settings")) {
        applySettings(doc);
    } else if (!strcmp(t, "status")) {
        applyStatus(doc);
    } else if (!strcmp(t, "screen")) {
        const char *id = doc["id"] | "";
        JsonDocument a;
        a["t"] = "ack";
        a["id"] = id;
        sendTo(src, a);
        if (id[0] && !strcmp(id, lastAnswered)) {
            if (cachedLen) linkSend(src, cachedPress, cachedLen);   // our answer was lost: repeat it
        } else if (id[0] && !(model.screen.active && !strcmp(id, model.screen.id))) {
            showScreen(doc);
            screenSrc = src;
        }
    } else if (!strcmp(t, "close")) {
        const char *id = doc["id"] | "";
        JsonDocument a;
        a["t"] = "ack";
        a["id"] = id;
        sendTo(src, a);
        if (model.screen.active && !strcmp(id, model.screen.id)) model.screen.active = false;
        if (!model.screen.active && model.mode == Mode::Screen) model.mode = Mode::Status;
    } else if (!strcmp(t, "toast")) {
        const char *lv = doc["level"] | "info";
        appToast(doc["text"] | "", !strcmp(lv, "info") ? Tone::Info : toneOf(lv), doc["ms"] | 2500);
    } else if (!strcmp(t, "ota_begin")) {
        const char *err = nullptr;
        bool ok = otaBegin(doc["size"] | 0, doc["md5"] | "", &err);
        model.mode = ok ? Mode::Ota : afterOta();
        model.otaPct = 0;
        sendOta(src, 0, !ok, ok, err);
    } else if (!strcmp(t, "ota_data")) {
        const char *err = nullptr;
        int off = doc["off"] | -1;
        bool ok = off >= 0 && otaWrite((size_t)off, doc["d"] | "", &err);
        model.otaPct = otaPercent();
        sendOta(src, off, !ok, ok, err);
        if (!ok) model.mode = afterOta();
    } else if (!strcmp(t, "ota_end")) {
        const char *err = nullptr;
        bool ok = otaFinish(&err);
        sendOta(src, 0, true, ok, err);
        if (ok) {
            Serial.flush();
            delay(300);
            ESP.restart();
        }
        model.mode = afterOta();
    }
    markDirty();
}

// ---- keys ----------------------------------------------------------------------------------

void answer(uint8_t key, const char *act, int idx, const char *label) {
    ScreenModel &sc = model.screen;
    JsonDocument d;
    d["t"] = "press";
    d["id"] = sc.id;
    d["key"] = key;
    d["act"] = act;
    if (idx >= 0) d["idx"] = idx;
    if (!strcmp(act, "submit")) {
        JsonArray sel = d["sel"].to<JsonArray>();
        for (uint8_t i = 0; i < sc.nItems; i++) if (sc.picked[i]) sel.add(i);
    }
    cachedLen = serializeJson(d, cachedPress, sizeof(cachedPress));
    if (cachedLen >= sizeof(cachedPress)) cachedLen = 0;
    strlcpy(lastAnswered, sc.id, sizeof(lastAnswered));
    sendAnswer(d);
    sc.active = false;   // locked: further presses do nothing until the host moves on
    strlcpy(model.sent, label, sizeof(model.sent));
    model.sentUntil = millis() + SENT_MS;
    model.mode = Mode::Status;
}

void sendSession(const char *act, const char *sid) {
    JsonDocument d;
    d["t"] = "session";
    d["act"] = act;
    if (sid) d["sid"] = sid;
    sendHost(d);
}

void debugLog(const char *fmt, ...) {
#ifdef KEYPAD_DEBUG
    char msg[160];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    JsonDocument d;
    d["t"] = "log";
    d["level"] = "debug";
    d["msg"] = msg;
    sendTo(Src::Usb, d);
#else
    (void)fmt;
#endif
}

int clampInt(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

// ---- dialogs: cursor, Enter, number keys, Esc ----

int lastRow(const ScreenModel &sc) { return sc.tpl == Tpl::Multi ? sc.nItems : sc.nItems - 1; }   // multi: + Submit

// Up/down. Above the first option the text takes focus and scrolls (a long
// command, or Claude's message on the prompt screen, can be read in full);
// past its end the options take focus again. `scroll` counts lines from the
// top; the prompt screen's transcript starts at its end, like the terminal.
void screenMove(int d) {
    ScreenModel &sc = model.screen;
    if (sc.cursor < 0) {
        int s = sc.scroll + d;
        if (s > sc.maxScroll) sc.cursor = 0;
        else sc.scroll = (int16_t)(s < 0 ? 0 : s);
        return;
    }
    int c = sc.cursor + d;
    if (c < 0) {
        c = sc.maxScroll > 0 ? -1 : 0;
        if (c < 0 && sc.tpl == Tpl::Prompt) sc.scroll = sc.maxScroll - 1;   // one line up from the end
    }
    sc.cursor = (int8_t)clampInt(c, -1, lastRow(sc));
}

void togglePick(ScreenModel &sc, int idx) {
    if (idx >= 0 && idx < sc.nItems) sc.picked[idx] = !sc.picked[idx];
}

void submitMulti(uint8_t key) {
    ScreenModel &sc = model.screen;
    bool any = false;
    for (uint8_t i = 0; i < sc.nItems; i++) any |= sc.picked[i];
    if (any) answer(key, "submit", -1, "Submitted");
    else appToast("Pick at least one", Tone::Warn, 1500);
}

void screenEnter(uint8_t key) {
    ScreenModel &sc = model.screen;
    if (sc.cursor < 0) {   // reading the body: Enter goes back to the options, never decides
        sc.cursor = 0;
        return;
    }
    if (sc.tpl == Tpl::Multi) {
        if (sc.cursor >= sc.nItems) submitMulti(key);
        else togglePick(sc, sc.cursor);
        return;
    }
    if (sc.cursor < sc.nItems) answer(key, "pick", sc.cursor, sc.items[sc.cursor]);
}

void screenKey(uint8_t key) {
    ScreenModel &sc = model.screen;
    if (key == KEY_ENTER) {
        screenEnter(key);
    } else if (key == KEY_ESC || key == KEY_ENC) {
        if (sc.esc[0]) answer(key, sc.esc, -1, !strcmp(sc.esc, "pc") ? "On the PC" : "Back");
    } else if (key == KEY_UP || key == KEY_DOWN) {
        screenMove(key == KEY_UP ? -1 : 1);
    } else if (key >= 1 && key <= DIRECT_PICKS) {   // number keys pick at once, like Claude Code
        int idx = key - 1;
        if (idx >= sc.nItems) return;
        sc.cursor = (int8_t)idx;
        if (sc.tpl == Tpl::Multi) togglePick(sc, idx);
        else answer(key, "pick", idx, sc.items[idx]);
    }
}

// ---- the status screen and the session picker ----

void openSessions() {
    model.pick = model.status.pinned ? model.view + 1 : 0;
    model.mode = Mode::Sessions;
}

void chooseSession(int row) {   // 0 = follow latest, 1..n = that session
    StatusModel &st = model.status;
    if (row < 0 || row > st.n) return;
    if (row == 0) {
        sendSession("follow", nullptr);
    } else {
        model.view = (int8_t)(row - 1);
        st.nLog = 0;   // that transcript belonged to the previous session; the host sends the new one
        st.logVer++;
        logHash = 0;   // whatever arrives next is new to the screen
        sendSession("select", st.s[row - 1].id);
    }
    model.logScroll = 0;
    model.mode = Mode::Status;
}

void sendMenu(uint8_t key) {
    JsonDocument d;
    d["t"] = "press";
    d["id"] = "status";
    d["key"] = key;
    d["act"] = "menu";
    sendHost(d);
}

void statusKey(uint8_t key) {
    if (key == KEY_SESSIONS) {
        openSessions();
    } else if (key == KEY_UP || key == KEY_DOWN) {
        model.logScroll = (int16_t)clampInt(model.logScroll + (key == KEY_UP ? 1 : -1), 0, model.logMax);
    } else if (key == KEY_ESC || key == KEY_ENC) {   // back to live: the newest line of the latest activity
        model.logScroll = 0;
        if (model.status.pinned) sendSession("follow", nullptr);
    } else if (key == KEY_ENTER && model.status.menu) {
        sendMenu(key);
    }
}

void sessionsKey(uint8_t key) {
    if (key == KEY_SESSIONS || key == KEY_ESC || key == KEY_ENC) model.mode = Mode::Status;
    else if (key == KEY_UP || key == KEY_DOWN) model.pick = (int8_t)clampInt(model.pick + (key == KEY_UP ? -1 : 1), 0, model.status.n);
    else if (key == KEY_ENTER) chooseSession(model.pick);
    else if (key >= 1 && key <= DIRECT_PICKS && key <= model.status.n) chooseSession(key);
}

void onKey(const KeyEvent &ev) {
    debugLog("key %u action %u clean %d mode %u active %d age %ld acked %d/%d", ev.key, (unsigned)ev.action, ev.clean,
             (unsigned)model.mode, model.screen.active, (long)(ev.pressedAt - model.screen.shownAt), links[0].acked,
             links[1].acked);
    if (model.mode == Mode::Test) {
        uint16_t bit = 1u << ev.key;
        if (ev.action == KeyAction::Press) model.testKeys |= bit;
        else if (ev.action == KeyAction::Release) model.testKeys &= ~bit;
        return;
    }
    if (ev.action != KeyAction::Press || !ev.clean) return;   // one key at a time (the matrix has no diodes)
    if (wakeOnly()) return;   // the first press on a dim screen only wakes it
    if (model.mode == Mode::Screen && model.screen.active) {
        // Safety: never a press that began before the screen appeared.
        if (ev.pressedAt < model.screen.shownAt + STALE_PRESS_MS) return;
        screenKey(ev.key);
    } else if (model.mode == Mode::Status) {
        statusKey(ev.key);
    } else if (model.mode == Mode::Sessions) {
        sessionsKey(ev.key);
    }
}

// The encoder turns like 4 (up) and 8 (down), one step per detent.
void onEncoder(int32_t steps) {
    if (model.mode == Mode::Test) {
        model.testEncoder += steps;
        return;
    }
    if (wakeOnly()) return;
    uint8_t key = steps < 0 ? KEY_UP : KEY_DOWN;
    for (int32_t i = steps < 0 ? -steps : steps; i > 0; i--) {
        if (model.mode == Mode::Screen && model.screen.active) screenKey(key);
        else if (model.mode == Mode::Status) statusKey(key);
        else if (model.mode == Mode::Sessions) sessionsKey(key);
    }
}

// ---- periodic ----------------------------------------------------------------------------

int8_t readBattery() {
    int mv = (int)analogReadMilliVolts(PIN_BATTERY) * 2;   // 1:2 divider
    if (mv > 4300 || mv < 2800) return -1;                 // on USB power / no battery
    int pct = (mv - 3300) * 100 / (4150 - 3300);
    return (int8_t)(pct < 0 ? 0 : pct > 100 ? 100 : pct);
}

void tick() {
    uint32_t now = millis();
    // Host liveness per link.
    for (int i = 0; i < 2; i++) {
        LinkState &L = links[i];
        if (L.acked && now - L.lastRx > HOST_TIMEOUT_MS) {
            L.acked = false;
            markDirty();
        }
    }
    if (links[1].acked && !linkNetAuthed()) links[1].acked = false;
    model.usbHost = links[0].acked;
    model.wifiHost = links[1].acked;
    bool connected = model.usbHost || model.wifiHost;

    // Announce ourselves until greeted.
    if (!links[0].acked && now - links[0].lastHello > HELLO_EVERY_MS) sendHello(Src::Usb);
    if (!links[1].acked && linkNetAuthed() && now - links[1].lastHello > HELLO_EVERY_MS) sendHello(Src::Net);

    if (model.mode == Mode::Boot && now - bootAt > 1200) model.mode = connected ? Mode::Status : Mode::Waiting;
    if (!connected && (model.mode == Mode::Status || model.mode == Mode::Sessions || model.mode == Mode::Screen ||
                       model.mode == Mode::Ota)) {
        model.mode = Mode::Waiting;   // includes an update whose host went away
        model.screen.active = false;
        otaAbort();
    }
    if (model.mode == Mode::Ota && !otaRunning()) model.mode = connected ? afterOta() : Mode::Waiting;
    if (model.mode == Mode::Screen && model.screen.expiresAt && (int32_t)(now - model.screen.expiresAt) > 0) {
        model.screen.active = false;
        model.mode = Mode::Status;
    }
    if (model.mode == Mode::Screen && !model.screen.active) model.mode = Mode::Status;

    // Key test: hold 1 while waiting for the PC; hold 8 to leave.
    if (model.mode == Mode::Waiting && inputHeld(1, TEST_HOLD_MS)) {
        model.mode = Mode::Test;
        model.testKeys = 0;
        model.testEncoder = 0;
    } else if (model.mode == Mode::Test && inputHeld(8, TEST_EXIT_MS)) {
        model.mode = connected ? Mode::Status : Mode::Waiting;
    }

    // Idle: dim the backlight (never with a request on screen, an update or the key test).
    bool canDim = model.mode == Mode::Status || model.mode == Mode::Sessions || model.mode == Mode::Waiting;
    bool dim = canDim && now - model.lastActivity > DIM_AFTER_MS;
    if (dim != model.dimmed) {
        model.dimmed = dim;
        markDirty();
    }

    WifiStatus w = linkWifi();
    if (w.state != model.wifi || strcmp(w.ip, model.ip) != 0) {
        model.wifi = w.state;
        strlcpy(model.ip, w.ip, sizeof(model.ip));
        sendWifiReport();
        markDirty();
    }
    model.rssi = w.rssi;
    if (now - lastBattery > 10000 || lastBattery == 0) {
        lastBattery = now;
        model.battery = readBattery();
    }
}

}  // namespace

void appToast(const char *text, Tone tone, uint32_t ms) {
    wake();
    copyText(model.toast, sizeof(model.toast), text);
    model.toastTone = tone;
    model.toastUntil = millis() + ms;
    markDirty();
}

void appBegin() {
    bootAt = millis();
    storeLoad(stored);
    uint64_t mac = ESP.getEfuseMac();   // bytes in little-endian order
    snprintf(model.id, sizeof(model.id), "kp-%02x%02x%02x", (uint8_t)(mac >> 24), (uint8_t)(mac >> 32),
             (uint8_t)(mac >> 40));
    if (!stored.name[0]) strlcpy(stored.name, model.id, sizeof(stored.name));
    model.mode = Mode::Boot;
    model.dark = stored.dark;
    model.brightness = stored.brightness;
    model.paired = stored.paired;
    model.battery = -1;
    model.status.logText = (char *)bigAlloc(LOG_POOL);
    copyText(model.name, sizeof(model.name), stored.name);
    applyWifiConfig();
    linkBegin(onMessage, model.id);
    linkConfigure(stored);
}

bool appLoop() {
    linkPoll();
    inputPoll();
    KeyEvent ev;
    while (inputNext(ev)) {
        onKey(ev);
        markDirty();
    }
    if (int32_t steps = inputTakeSteps()) {
        onEncoder(steps);
        markDirty();
    }
    tick();
    bool d = dirty;
    dirty = false;
    return d;
}
