// App logic: host messages -> model, key presses -> host messages, and the
// safety rules (clean presses only, stale-press guard, answer once per screen).
#include "app.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <stdarg.h>
#include <string.h>

#include "config.h"
#include "input.h"
#include "link.h"
#include "model.h"
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

char lastAnswered[49] = "";
char cachedPress[256] = "";
size_t cachedLen = 0;
uint32_t bootAt = 0;
uint32_t lastBattery = 0;

void markDirty() { dirty = true; }

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

void parseKeys(JsonObjectConst obj, KeyBind *keys) {
    for (int k = 0; k < 9; k++) keys[k].set = false;
    for (JsonPairConst kv : obj) {
        int k = atoi(kv.key().c_str());
        if (k < 1 || k > 8) continue;
        JsonObjectConst v = kv.value().as<JsonObjectConst>();
        keys[k].set = true;
        copyText(keys[k].label, sizeof(keys[k].label), v["label"] | "");
        strlcpy(keys[k].act, v["act"] | "", sizeof(keys[k].act));
        keys[k].tone = toneOf(v["tone"]);
    }
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
    st.n = 0;
    uint32_t now = millis();
    for (JsonObjectConst s : doc["sessions"].as<JsonArrayConst>()) {
        if (st.n >= MAX_SESSIONS) break;
        SessionInfo &x = st.s[st.n++];
        strlcpy(x.id, s["id"] | "", sizeof(x.id));
        copyText(x.project, sizeof(x.project), s["project"] | "");
        strlcpy(x.state, s["state"] | "", sizeof(x.state));
        copyText(x.title, sizeof(x.title), s["title"] | "");
        copyText(x.detail, sizeof(x.detail), s["detail"] | "");
        x.startedAt = now - (uint32_t)(s["since"] | 0) * 1000u;
    }
    strlcpy(st.sel, doc["sel"] | "", sizeof(st.sel));
    st.pinned = doc["pinned"] | false;
    st.queue = doc["queue"] | 0;
    st.paused = doc["paused"] | false;
    parseKeys(doc["keys"].as<JsonObjectConst>(), st.keys);
    model.view = 0;
    for (uint8_t i = 0; i < st.n; i++) if (!strcmp(st.s[i].id, st.sel)) model.view = i;
}

void showScreen(JsonDocument &doc) {
    ScreenModel &sc = model.screen;
    const char *tpl = doc["tpl"] | "prompt";
    sc.tpl = !strcmp(tpl, "list") ? Tpl::List : !strcmp(tpl, "multi") ? Tpl::Multi : Tpl::Prompt;
    strlcpy(sc.id, doc["id"] | "", sizeof(sc.id));
    sc.tone = toneOf(doc["tone"]);
    copyText(sc.title, sizeof(sc.title), doc["title"] | "");
    copyText(sc.project, sizeof(sc.project), doc["project"] | "");
    copyText(sc.body, sizeof(sc.body), doc["body"] | "");
    sc.nItems = 0;
    for (const char *it : doc["items"].as<JsonArrayConst>()) {
        if (sc.nItems >= MAX_ITEMS) break;
        copyText(sc.items[sc.nItems], sizeof(sc.items[0]), it);
        sc.picked[sc.nItems++] = false;
    }
    if (sc.tpl == Tpl::Multi && sc.nItems > 7) sc.nItems = 7;
    parseKeys(doc["keys"].as<JsonObjectConst>(), sc.keys);
    strlcpy(sc.click, doc["click"] | "", sizeof(sc.click));
    sc.shownAt = millis();
    int timeout = doc["timeout"] | 0;
    sc.expiresAt = timeout > 0 ? sc.shownAt + (uint32_t)timeout * 1000u : 0;
    sc.page = 0;
    sc.scroll = 0;
    sc.maxScroll = 0;
    sc.active = true;
    model.sentUntil = 0;
    model.mode = Mode::Screen;
}

void provision(JsonDocument &doc, Src src) {
    JsonDocument r;
    r["t"] = "provisioned";
    const char *key = doc["key"] | "";
    const char *host = doc["host"] | "";
    const char *ssid = doc["ssid"] | "";
    bool ok = strlen(key) == 64 && host[0] && ssid[0] && strlen(ssid) < sizeof(stored.ssid);
    for (int i = 0; ok && i < 32; i++) {
        unsigned v;
        ok = sscanf(key + 2 * i, "%2x", &v) == 1;
        stored.key[i] = (uint8_t)v;
    }
    if (!ok) {
        r["ok"] = false;
        r["err"] = "invalid settings";
        sendTo(src, r);
        return;
    }
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
    linkConfigure(stored);
    appToast("Wi-Fi pairing removed", Tone::Warn, 3000);
}

void onKey(const KeyEvent &ev);

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
    // Everything below needs a greeted host, except provisioning over the cable.
    bool usbOnly = !strcmp(t, "provision") || !strcmp(t, "unpair");
    if (usbOnly) {
        if (src == Src::Usb) (!strcmp(t, "provision") ? provision(doc, src) : unpair());
        markDirty();
        return;
    }
    if (!L.acked) {
        sendHello(src);
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
    } else if (!strcmp(t, "test")) {
        model.mode = Mode::Test;
    } else if (!strcmp(t, "ota_begin")) {
        const char *err = nullptr;
        bool ok = otaBegin(doc["size"] | 0, doc["md5"] | "", &err);
        model.mode = ok ? Mode::Ota : Mode::Status;
        model.otaPct = 0;
        sendOta(src, 0, !ok, ok, err);
    } else if (!strcmp(t, "ota_data")) {
        const char *err = nullptr;
        int off = doc["off"] | -1;
        bool ok = off >= 0 && otaWrite((size_t)off, doc["d"] | "", &err);
        model.otaPct = otaPercent();
        sendOta(src, off, !ok, ok, err);
        if (!ok) model.mode = Mode::Status;
    } else if (!strcmp(t, "ota_end")) {
        const char *err = nullptr;
        bool ok = otaFinish(&err);
        sendOta(src, 0, true, ok, err);
        if (ok) {
            Serial.flush();
            delay(300);
            ESP.restart();
        }
        model.mode = Mode::Status;
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
    if (!strcmp(act, "confirm")) {
        JsonArray sel = d["sel"].to<JsonArray>();
        for (uint8_t i = 0; i < sc.nItems; i++) if (sc.picked[i]) sel.add(i);
    }
    cachedLen = serializeJson(d, cachedPress, sizeof(cachedPress));
    if (cachedLen >= sizeof(cachedPress)) cachedLen = 0;
    strlcpy(lastAnswered, sc.id, sizeof(lastAnswered));
    sendHost(d);
    sc.active = false;   // locked: further presses do nothing until the host moves on
    strlcpy(model.sent, label, sizeof(model.sent));
    model.sentUntil = millis() + SENT_MS;
    model.mode = Mode::Status;
}

void sendStatusPress(uint8_t key) {
    const KeyBind &kb = model.status.keys[key];
    if (!kb.set) return;
    JsonDocument d;
    d["t"] = "press";
    d["id"] = "status";
    d["key"] = key;
    d["act"] = kb.act;
    sendHost(d);
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
    if (ev.action != KeyAction::Press) return;
    if (model.mode == Mode::Screen && model.screen.active) {
        ScreenModel &sc = model.screen;
        // Safety: one key at a time, and never a press that began before the screen.
        if (!ev.clean || ev.pressedAt < sc.shownAt + STALE_PRESS_MS) return;
        if (ev.key == KEY_ENC) {
            if (sc.click[0]) answer(0, sc.click, -1, !strcmp(sc.click, "pc") ? "On the PC" : "Back");
            return;
        }
        switch (sc.tpl) {
            case Tpl::Prompt:
                if (sc.keys[ev.key].set) answer(ev.key, sc.keys[ev.key].act, -1, sc.keys[ev.key].label);
                break;
            case Tpl::List: {
                int idx = sc.page * ITEMS_PER_PAGE + ev.key - 1;
                if (idx < sc.nItems) answer(ev.key, "item", idx, "Sent");
                break;
            }
            case Tpl::Multi:
                if (ev.key == 8) {
                    bool any = false;
                    for (uint8_t i = 0; i < sc.nItems; i++) any |= sc.picked[i];
                    if (any) answer(8, "confirm", -1, "Sent");
                    else appToast("Pick at least one", Tone::Warn, 1500);
                } else if (ev.key - 1 < sc.nItems) {
                    sc.picked[ev.key - 1] = !sc.picked[ev.key - 1];
                }
                break;
        }
        return;
    }
    if (model.mode == Mode::Status) {
        if (!ev.clean) return;
        if (ev.key == KEY_ENC) {
            if (model.status.pinned) sendSession("follow", nullptr);
        } else {
            sendStatusPress(ev.key);
        }
    }
}

void onEncoder(int32_t steps) {
    if (model.mode == Mode::Test) {
        model.testEncoder += steps;
        return;
    }
    if (model.mode == Mode::Screen && model.screen.active) {
        ScreenModel &sc = model.screen;
        if (sc.tpl == Tpl::List) {
            int pages = (sc.nItems + ITEMS_PER_PAGE - 1) / ITEMS_PER_PAGE;
            if (pages > 1) sc.page = (uint8_t)((sc.page + steps % pages + pages) % pages);
        } else {
            int s = sc.scroll + steps;
            sc.scroll = (int16_t)(s < 0 ? 0 : s > sc.maxScroll ? sc.maxScroll : s);
        }
        return;
    }
    if (model.mode == Mode::Status && model.status.n > 1) {
        int n = model.status.n;
        model.view = (int8_t)(((model.view + steps) % n + n) % n);
        sendSession("select", model.status.s[model.view].id);
    }
}

// ---- periodic ----------------------------------------------------------------------------

int8_t readBattery() {
    uint32_t mv = analogReadMilliVolts(PIN_BATTERY) * 2;   // 1:2 divider
    if (mv > 4300 || mv < 2800) return -1;                 // on USB power / no battery
    int pct = (int)((mv - 3300) * 100 / (4150 - 3300));
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
    if (!connected && (model.mode == Mode::Status || model.mode == Mode::Screen)) {
        model.mode = Mode::Waiting;
        model.screen.active = false;
        otaAbort();
    }
    if (model.mode == Mode::Ota && !otaRunning()) model.mode = connected ? Mode::Status : Mode::Waiting;
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

    static WifiState lastWifi = WifiState::Off;
    if (model.wifi != lastWifi) {
        lastWifi = model.wifi;
        sendWifiReport();
        markDirty();
    }
    if (now - lastBattery > 10000 || lastBattery == 0) {
        lastBattery = now;
        model.battery = readBattery();
    }
}

}  // namespace

void appToast(const char *text, Tone tone, uint32_t ms) {
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
    copyText(model.name, sizeof(model.name), stored.name);
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
