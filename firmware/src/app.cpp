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
#include "power.h"
#include "store.h"
#include "text/text.h"

Model model;

namespace {

Stored stored;
bool dirty = true;

// The host talks to us over Wi-Fi. USB is only for setup: it answers hello and
// accepts provision / unpair, nothing else (see proto/PROTOCOL.md).
struct HostState {
    bool greeted;        // the host's hello arrived on the Wi-Fi link
    uint32_t lastRx;     // last valid host message
};
HostState host;

char lastAnswered[49] = "";
char cachedPress[256] = "";
size_t cachedLen = 0;
uint32_t bootAt = 0;
uint32_t lastBattery = 0;

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
    if (host.greeted) sendTo(Src::Net, doc);
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
    JsonObject w = d["wifi"].to<JsonObject>();
    w["state"] = wifiName(model.wifi);
    if (model.ssid[0]) w["ssid"] = model.ssid;
    if (model.wifi == WifiState::Up) {
        w["ip"] = model.ip;
        w["rssi"] = model.rssi;
    }
    if (model.battery >= 0) d["bat"] = model.battery;
    sendTo(src, d);
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
    int b = doc["brightness"] | (int)stored.brightness;
    b = b < 5 ? 5 : b > 100 ? 100 : b;
    const char *name = doc["name"] | "";
    bool changed = b != stored.brightness || (name[0] && strcmp(name, stored.name) != 0);
    stored.brightness = (uint8_t)b;
    if (name[0]) strlcpy(stored.name, name, sizeof(stored.name));
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
    st.queue = doc["queue"] | 0;
    st.paused = doc["paused"] | false;
    st.menu = doc["menu"] | false;
    copyText(st.queued, sizeof(st.queued), doc["queued"] | "");
    st.nQuick = 0;
    for (const char *q : doc["quick"].as<JsonArrayConst>()) {
        if (st.nQuick >= MAX_QUICK) break;
        copyText(st.quick[st.nQuick++], sizeof(st.quick[0]), q);
    }
    model.view = 0;
    for (uint8_t i = 0; i < st.n; i++) if (!strcmp(st.s[i].id, st.sel)) model.view = i;
    if (strcmp(prevSel, st.sel) != 0) {   // another session: its transcript arrives as a new feed
        model.logScroll = 0;
        st.nLog = 0;
        st.logVer++;
    }
    if (model.pick >= st.n) model.pick = st.n ? st.n - 1 : 0;
}

// ---- the transcript feed ----------------------------------------------------------------
// Each entry's text sits in one pool, NUL-terminated, oldest first.
size_t logUsed(const StatusModel &st) {
    return st.nLog ? st.log[st.nLog - 1].off + strlen(st.logText + st.log[st.nLog - 1].off) + 1 : 0;
}

void dropOldest(StatusModel &st, int n) {
    if (n <= 0) return;
    if (n >= st.nLog) { st.nLog = 0; return; }
    size_t used = logUsed(st), shift = st.log[n].off;
    memmove(st.logText, st.logText + shift, used - shift);
    for (int i = n; i < st.nLog; i++) {
        st.log[i - n] = st.log[i];
        st.log[i - n].off = (uint16_t)(st.log[i].off - shift);
    }
    st.nLog -= n;
}

void appendLog(StatusModel &st, JsonObjectConst l) {
    const char *t = l["t"] | "";
    while (st.nLog > 0 && (st.nLog >= MAX_LOG || logUsed(st) + strlen(t) + 2 >= LOG_POOL)) dropOldest(st, 1);
    size_t used = logUsed(st);
    if (used + 2 >= LOG_POOL) return;
    LogEntry &e = st.log[st.nLog];
    const char *k = l["k"] | "c";
    e.k = k[0];
    e.off = (uint16_t)used;
    copyText(st.logText + used, LOG_POOL - used, t);
    st.nLog++;
}

// {"t":"feed","sid":..,"full":[entries]} replaces the transcript.
void applyFeed(JsonDocument &doc) {
    StatusModel &st = model.status;
    if (!st.logText || strcmp(doc["sid"] | "", st.sel) != 0) return;   // not the session on screen
    st.nLog = 0;
    for (JsonObjectConst l : doc["full"].as<JsonArrayConst>()) appendLog(st, l);
    st.logVer++;
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
    for (const char *it : doc["items"].as<JsonArrayConst>()) {
        if (sc.nItems >= MAX_ITEMS) break;
        copyText(sc.items[sc.nItems], sizeof(sc.items[0]), it);
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
    uint8_t newKey[sizeof(HostKey::key)];
    bool ok = host[0] && strlen(host) < sizeof(HostKey::id) && ssid[0] && strlen(ssid) < sizeof(stored.ssid) &&
              hexDecode(key, newKey, sizeof(newKey));
    if (!ok) {
        r["ok"] = false;
        r["err"] = "invalid settings";
        sendTo(src, r);
        return;
    }
    if (!storeSetHost(stored, host, newKey)) {   // this computer is added next to the others
        r["ok"] = false;
        r["err"] = "paired with 3 other computers: forget one first";
        sendTo(src, r);
        return;
    }
    strlcpy(stored.ssid, ssid, sizeof(stored.ssid));
    strlcpy(stored.pass, doc["pass"] | "", sizeof(stored.pass));
    if (const char *n = doc["name"]; n && n[0]) strlcpy(stored.name, n, sizeof(stored.name));
    storeSave(stored);
    model.paired = true;
    copyText(model.name, sizeof(model.name), stored.name);
    r["ok"] = true;
    sendTo(src, r);
    applyWifiConfig();
    linkConfigure(stored);
    appToast("Paired. Joining Wi-Fi...", Tone::Ok, 3000);
}

// From the cable: forget every computer and the Wi-Fi. From a computer over Wi-Fi: forget that one only.
void unpair(bool fromHost) {
    char who[sizeof(HostKey::id)];
    strlcpy(who, fromHost ? linkHost() : "", sizeof(who));
    if (fromHost && !who[0]) return;   // no session to name: never mistaken for "forget everything"
    storeRemoveHost(stored, who);
    if (!stored.paired) stored.ssid[0] = stored.pass[0] = '\0';
    storeSave(stored);
    model.paired = stored.paired;
    host.greeted = false;
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
    bool net = src == Src::Net;
    if (net) model.lastHostAt = host.lastRx = millis();

    // The host's hello: it introduces itself, we introduce ourselves. Over USB that is all it does.
    if (!strcmp(t, "hello")) {
        if (net) {
            host.greeted = true;
            copyText(model.host, sizeof(model.host), doc["host"] | "");
            linkNamePeer(model.host);   // a second computer that finds the keypad busy is told who has it
            if (model.mode == Mode::Waiting || model.mode == Mode::Boot) model.mode = Mode::Status;
            markDirty();
        }
        sendHello(src);
        return;
    }
    if (!strcmp(t, "ping")) {
        if (net && !host.greeted) sendHello(src);
        JsonDocument p;
        p["t"] = "pong";
        p["bat"] = model.battery;   // -1 = unknown / on USB power
        p["wifi"] = wifiName(model.wifi);
        p["rssi"] = model.rssi;
        sendTo(src, p);
        return;
    }
    // Pairing needs the cable (physical access).
    if (!strcmp(t, "provision")) {
        if (!net) provision(doc, src);
        markDirty();
        return;
    }
    // Forgetting works over the cable or from the paired host over Wi-Fi.
    if (!strcmp(t, "unpair")) {
        if (!net || host.greeted) unpair(net);
        markDirty();
        return;
    }
    // Everything below is for the host on Wi-Fi, once it has said hello.
    if (!net) return;
    if (!host.greeted) {
        sendHello(src);
        return;
    }

    if (!strcmp(t, "settings")) {
        applySettings(doc);
    } else if (!strcmp(t, "status")) {
        applyStatus(doc);
    } else if (!strcmp(t, "feed")) {
        applyFeed(doc);
    } else if (!strcmp(t, "screen")) {
        const char *id = doc["id"] | "";
        if (id[0] && !strcmp(id, lastAnswered)) {
            if (cachedLen) linkSend(src, cachedPress, cachedLen);   // our answer was lost: repeat it
        } else if (id[0] && !(model.screen.active && !strcmp(id, model.screen.id))) {
            showScreen(doc);
        }
    } else if (!strcmp(t, "close")) {
        const char *id = doc["id"] | "";
        if (model.screen.active && !strcmp(id, model.screen.id)) model.screen.active = false;
        if (!model.screen.active && model.mode == Mode::Screen) {
            model.mode = Mode::Status;
            model.screenEndedAt = millis();
        }
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
    sendHost(d);
    sc.active = false;   // locked: further presses do nothing until the host moves on
    model.screenEndedAt = millis();
    strlcpy(model.sent, label, sizeof(model.sent));
    model.sentUntil = millis() + SENT_MS;
    model.mode = Mode::Status;
}

void sendSession(const char *sid) {
    JsonDocument d;
    d["t"] = "session";
    d["act"] = "select";
    d["sid"] = sid;
    sendHost(d);
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
    } else if (key == KEY_ESC) {
        if (sc.esc[0]) answer(key, sc.esc, -1, !strcmp(sc.esc, "done") ? "Done" : "Back");
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
    model.pick = model.view;
    model.mode = Mode::Sessions;
}

void chooseSession(int row) {   // 0..n-1: that session; it stays shown until a request or a finished turn elsewhere needs you
    StatusModel &st = model.status;
    if (row < 0 || row >= st.n) return;
    if (row != model.view) {
        model.view = (int8_t)row;
        st.nLog = 0;   // that transcript belonged to the previous session; the host sends the new one
        st.logVer++;
    }
    sendSession(st.s[row].id);   // also when it is the one shown: hold it there
    model.logScroll = 0;
    model.mode = Mode::Status;
}

void sendMenu(uint8_t key, const char *act, int idx = -1) {
    JsonDocument d;
    d["t"] = "press";
    d["id"] = "status";
    d["key"] = key;
    d["act"] = act;
    if (idx >= 0) d["idx"] = idx;
    sendHost(d);
}

void quickKey(uint8_t key);

void statusKey(uint8_t key) {
    if (key == KEY_SESSIONS) {
        openSessions();
    } else if (key == KEY_UP || key == KEY_DOWN) {
        model.logScroll = (int16_t)clampInt(model.logScroll + (key == KEY_UP ? 1 : -1), 0, model.logMax);
    } else if (key == KEY_ESC) {   // takes back a queued saved prompt, else back to live: the newest line
        if (model.status.queued[0]) sendMenu(key, "unqueue");
        else model.logScroll = 0;
    } else if (key == KEY_ENTER && model.status.menu) {
        sendMenu(key, "menu");
    } else if (key >= 1 && key <= DIRECT_PICKS) {
        quickKey(key);
    }
}

// A saved prompt in one press: keys 1-3, 4 and 8 are prompts 1-5. Not right after a request went away
// (that press meant "Yes" or "No"), and never on a dialog.
void quickKey(uint8_t key) {
    int idx = key <= DIRECT_PICKS ? key - 1 : key == KEY_QUICK_4 ? 3 : 4;
    if (idx < model.status.nQuick && millis() - model.screenEndedAt > QUICK_GUARD_MS) sendMenu(key, "quick", idx);
}

void sessionsKey(uint8_t key) {
    if (key == KEY_SESSIONS || key == KEY_ESC) model.mode = Mode::Status;
    else if (key == KEY_UP || key == KEY_DOWN) model.pick = (int8_t)clampInt(model.pick + (key == KEY_UP ? -1 : 1), 0, model.status.n ? model.status.n - 1 : 0);
    else if (key == KEY_ENTER) chooseSession(model.pick);
    else if (key >= 1 && key <= DIRECT_PICKS && key <= model.status.n) chooseSession(key - 1);
}

void onKey(const KeyEvent &ev) {
    if (ev.action != KeyAction::Press || !ev.clean) return;   // one key at a time (the matrix has no diodes)
    if (wakeOnly()) return;   // the first press on a dim screen only wakes it
    uint8_t key = ev.key == KEY_ENC ? KEY_ENTER : ev.key;   // pressing the knob is Enter
    if (key == KEY_QUICK_4 || key == KEY_QUICK_5) {   // moving is the knob's job: these two are saved prompts, on the status screen only
        if (model.mode == Mode::Status) quickKey(key);
        return;
    }
    if (model.mode == Mode::Screen && model.screen.active) {
        // Safety: never a press that began before the screen appeared.
        if (ev.pressedAt < model.screen.shownAt + STALE_PRESS_MS) return;
        screenKey(key);
    } else if (model.mode == Mode::Status) {
        statusKey(key);
    } else if (model.mode == Mode::Sessions) {
        sessionsKey(key);
    }
}

// The knob moves the cursor and scrolls, one step per detent; turned fast, text scrolls faster
// (a long diff or transcript). A cursor in a list of options never jumps: every detent is one row.
bool scrolling() { return model.mode == Mode::Status || (model.mode == Mode::Screen && model.screen.active && model.screen.cursor < 0); }

void onEncoder(int32_t steps) {
    if (wakeOnly()) return;
    static uint32_t lastTurn = 0;
    uint32_t now = millis();
    uint32_t gap = now - lastTurn;
    lastTurn = now;
    int32_t n = steps < 0 ? -steps : steps;
    int32_t total = n * (gap < KNOB_FAST_MS ? 4 : gap < KNOB_QUICK_MS ? 2 : 1);   // speed up only while scrolling text
    uint8_t key = steps < 0 ? KEY_UP : KEY_DOWN;
    for (int32_t i = 0; i < total; i++) {
        if (i >= n && !scrolling()) break;
        if (model.mode == Mode::Screen && model.screen.active) screenKey(key);
        else if (model.mode == Mode::Status) statusKey(key);
        else if (model.mode == Mode::Sessions) sessionsKey(key);
    }
}

// ---- periodic ----------------------------------------------------------------------------

void tick() {
    uint32_t now = millis();
    // Host liveness: the host pings every couple of seconds.
    if (host.greeted && (now - host.lastRx > HOST_TIMEOUT_MS || !linkNetAuthed())) {
        host.greeted = false;
        markDirty();
    }
    model.wifiHost = host.greeted;
    bool connected = host.greeted;

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
        model.screenEndedAt = now;
    }
    if (model.mode == Mode::Screen && !model.screen.active) {
        model.mode = Mode::Status;
        model.screenEndedAt = now;
    }

    // Idle on battery: dim the backlight (never with a request on screen or an update, and never
    // when running from a wire). A key press or a request lights it up again.
    bool canDim = model.mode == Mode::Status || model.mode == Mode::Sessions || model.mode == Mode::Waiting;
    bool dim = canDim && !model.usbPower && now - model.lastActivity > DIM_AFTER_MS;
    if (dim != model.dimmed) {
        model.dimmed = dim;
        markDirty();
    }

    WifiStatus w = linkWifi();
    if (w.state != model.wifi || strcmp(w.ip, model.ip) != 0) {
        model.wifi = w.state;
        strlcpy(model.ip, w.ip, sizeof(model.ip));
        markDirty();
    }
    model.rssi = w.rssi;
    if (now - lastBattery > 10000 || lastBattery == 0) {
        lastBattery = now;
        model.battery = batteryPercent();
        model.usbPower = usbPowered();
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
    model.brightness = stored.brightness;
    model.paired = stored.paired;
    model.battery = -1;
    model.usbPower = usbPowered();
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
