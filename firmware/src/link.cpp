#include "link.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <WiFi.h>
#include <esp_random.h>
#include <string.h>

#include "config.h"
#include "crypto.h"
#include "mem.h"

namespace {

MessageHandler handler = nullptr;
const char *devId = "";

// ---- USB -----------------------------------------------------------------------
char *usbBuf;   // MSG_MAX bytes
size_t usbLen = 0;
bool usbOverflow = false;

void pollUsb() {
    int budget = 2048;   // bytes per loop, keeps the UI responsive
    while (Serial.available() > 0 && budget-- > 0) {
        char c = (char)Serial.read();
        if (c == '\n') {
            if (!usbOverflow && usbLen > 0) {
                usbBuf[usbLen] = '\0';
                handler(usbBuf, usbLen, Src::Usb);
            }
            usbLen = 0;
            usbOverflow = false;
        } else if (c != '\r') {
            if (usbLen < MSG_MAX - 1) usbBuf[usbLen++] = c;
            else usbOverflow = true;
        }
    }
}

// ---- Wi-Fi -----------------------------------------------------------------------
Stored cfg;
bool haveCfg = false;
uint32_t wifiStartedAt = 0;
bool serving = false;
WifiStatus wifi = {WifiState::Off, "", 0};
WiFiServer server(TCP_PORT);

constexpr size_t FRAME_MAX = MSG_MAX;

struct Session {
    WiFiClient c;
    bool used = false;
    bool keyed = false;   // handshake done, waiting for the first encrypted frame
    bool authed = false;  // first frame decrypted: the peer holds the key
    uint32_t since = 0;
    uint32_t lastRx = 0;  // last decrypted frame
    char host[24] = "";   // the paired computer this session belongs to
    char peer[32] = "";   // its name, once it said hello (shown to a second computer that finds the keypad busy)
    Sealer tx, rx;
    uint8_t *buf = nullptr;   // FRAME_MAX + 2 bytes
    size_t have = 0;

    void close() {
        if (used) c.stop();
        used = keyed = authed = false;
        have = 0;
        host[0] = peer[0] = '\0';
    }
};

// Two fixed slots; promotion swaps the pointers (GCM contexts never move).
Session slots[2];
Session *active = &slots[0];
Session *pending = &slots[1];
uint8_t *plain;    // FRAME_MAX bytes
uint8_t *sealed;   // FRAME_MAX + 2 bytes

// A frame goes out in one write: header and body together, so a stalled host cannot leave half a frame
// on the wire (a short write ends the session; the stream could not be resynchronised).
bool writeWhole(WiFiClient &c, const uint8_t *frame, size_t total) { return c.write(frame, total) == total; }

bool writePlain(WiFiClient &c, const char *json, size_t n) {   // handshake frames: small
    uint8_t buf[2 + 192];
    if (n > sizeof(buf) - 2) return false;
    buf[0] = (uint8_t)(n >> 8);
    buf[1] = (uint8_t)n;
    memcpy(buf + 2, json, n);
    return writeWhole(c, buf, n + 2);
}

// code: a stable reason for the host (see proto.Refusal); why: the same in words.
void refuse(Session &s, const char *code, const char *why, const char *host = nullptr) {
    char out[192];
    int n = host && host[0] ? snprintf(out, sizeof(out), "{\"t\":\"no\",\"code\":\"%s\",\"why\":\"%s\",\"host\":\"%s\"}", code, why, host)
                            : snprintf(out, sizeof(out), "{\"t\":\"no\",\"code\":\"%s\",\"why\":\"%s\"}", code, why);
    if (n > 0 && n < (int)sizeof(out)) writePlain(s.c, out, n);
    s.close();
}

// Plain "hi" frame from a connecting host.
void handshake(Session &s, const uint8_t *f, size_t n) {
    JsonDocument doc;
    if (deserializeJson(doc, (const char *)f, n) || strcmp(doc["t"] | "", "hi") != 0 || doc["v"] != PROTOCOL_VERSION) {
        refuse(s, "bad_hello", "bad hello");
        return;
    }
    if (!cfg.paired) { refuse(s, "not_paired", "keypad is not paired"); return; }
    const char *host = doc["host"] | "";
    int slot = storeFindHost(cfg, host);
    if (slot < 0) { refuse(s, "other_host", "not paired with this computer"); return; }
    // One computer at a time. A second one is told who has the keypad, unless that session has gone quiet,
    // it is the same computer coming back, or the newcomer asks to take over.
    if (active->authed && strcmp(active->host, host) != 0 && millis() - active->lastRx < HOST_TIMEOUT_MS && !(doc["take"] | 0)) {
        refuse(s, "busy", "in use by another computer", active->peer);
        return;
    }
    uint8_t nh[16], nd[16], h2d[32], d2h[32];
    if (!hexDecode(doc["n"] | "", nh, 16)) { refuse(s, "bad_nonce", "bad nonce"); return; }
    esp_fill_random(nd, sizeof(nd));
    if (!deriveKeys(cfg.hosts[slot].key, nh, nd, h2d, d2h) || !s.tx.init(d2h) || !s.rx.init(h2d)) { s.close(); return; }
    memset(h2d, 0, 32);
    memset(d2h, 0, 32);
    char out[160], hex[33];
    for (int i = 0; i < 16; i++) sprintf(hex + 2 * i, "%02x", nd[i]);
    int len = snprintf(out, sizeof(out), "{\"t\":\"hi\",\"v\":%d,\"id\":\"%s\",\"n\":\"%s\"}", PROTOCOL_VERSION, devId, hex);
    if (len <= 0 || !writePlain(s.c, out, len)) { s.close(); return; }
    strlcpy(s.host, host, sizeof(s.host));
    s.keyed = true;
}

// Returns false when the session must be closed.
bool frame(Session &s, const uint8_t *f, size_t n) {
    if (!s.keyed) {
        handshake(s, f, n);
        return s.used;
    }
    if (n <= GCM_TAG || !s.rx.open(f, n, plain)) return false;   // wrong key, replay, tampering
    size_t len = n - GCM_TAG;
    s.lastRx = millis();
    if (!s.authed) {
        s.authed = true;
        if (&s == pending) {   // promote: this host proved it holds the key
            active->close();
            Session *t = active;
            active = pending;
            pending = t;
        }
    }
    plain[len] = '\0';
    handler((char *)plain, len, Src::Net);
    return true;
}

void pollSession(Session &s) {
    if (!s.used) return;
    if (!s.c.connected()) { s.close(); return; }
    if (!s.authed && millis() - s.since > HANDSHAKE_MS) { s.close(); return; }
    int avail = s.c.available();
    while (avail > 0 && s.used) {
        size_t room = FRAME_MAX + 2 - s.have;
        int got = s.c.read(s.buf + s.have, min((size_t)avail, room));
        if (got <= 0) break;
        s.have += got;
        avail -= got;
        while (s.used && s.have >= 2) {
            size_t n = ((size_t)s.buf[0] << 8) | s.buf[1];
            if (n == 0 || n > FRAME_MAX) { s.close(); return; }
            if (s.have < n + 2) break;
            if (!frame(s, s.buf + 2, n)) { s.close(); return; }
            memmove(s.buf, s.buf + 2 + n, s.have - (n + 2));
            s.have -= n + 2;
        }
    }
}

void pollWifi() {
    if (!haveCfg || !cfg.ssid[0]) {
        wifi.state = WifiState::Off;
        return;
    }
    wl_status_t st = WiFi.status();
    if (st == WL_CONNECTED) {
        if (wifi.state != WifiState::Up) {
            wifi.state = WifiState::Up;
            strlcpy(wifi.ip, WiFi.localIP().toString().c_str(), sizeof(wifi.ip));
            if (!serving) {
                server.begin();
                server.setNoDelay(true);
                serving = true;
            }
            MDNS.end();
            if (MDNS.begin(devId)) {
                MDNS.addService("ckeypad", "tcp", TCP_PORT);
                MDNS.addServiceTxt("ckeypad", "tcp", "id", devId);
                MDNS.addServiceTxt("ckeypad", "tcp", "fw", KEYPAD_FW_VERSION);
                String ver(PROTOCOL_VERSION);
                MDNS.addServiceTxt("ckeypad", "tcp", "v", ver.c_str());
                MDNS.addServiceTxt("ckeypad", "tcp", "paired", cfg.paired ? "1" : "0");
            }
        }
        wifi.rssi = WiFi.RSSI();
    } else {
        if (wifi.state == WifiState::Up) {
            active->close();
            pending->close();
            wifiStartedAt = millis();   // lost the network: give auto-reconnect the same grace as a first join
        }
        wifi.state = (millis() - wifiStartedAt > 20000 && st != WL_IDLE_STATUS) ? WifiState::Failed : WifiState::Connecting;
        wifi.ip[0] = '\0';
    }
    if (!serving) return;
    WiFiClient c = server.accept();
    if (c) {
        pending->close();   // the newest attempt replaces an unfinished one
        pending->c = c;
        pending->c.setNoDelay(true);
        pending->used = true;
        pending->since = millis();
    }
    Session *a = active, *p = pending;   // a promotion during polling swaps them
    pollSession(*p);
    pollSession(*a);
}

}  // namespace

void linkBegin(MessageHandler h, const char *deviceId) {
    handler = h;
    devId = deviceId;
    usbBuf = (char *)bigAlloc(MSG_MAX);
    plain = (uint8_t *)bigAlloc(FRAME_MAX);
    sealed = (uint8_t *)bigAlloc(FRAME_MAX + 2);
    for (Session &s : slots) s.buf = (uint8_t *)bigAlloc(FRAME_MAX + 2);
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.setHostname(deviceId);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(false);   // modem sleep (the default) delays or drops packets: the link looks dead while idle
}

void linkConfigure(const Stored &st) {
    cfg = st;
    haveCfg = true;
    active->close();
    pending->close();
    wifi = {WifiState::Off, "", 0};
    WiFi.disconnect(true);
    if (st.ssid[0]) {
        WiFi.mode(WIFI_STA);
        WiFi.begin(st.ssid, st.pass);
        WiFi.setSleep(false);
        wifiStartedAt = millis();
        wifi.state = WifiState::Connecting;
    }
}

void linkPoll() {
    pollUsb();
    pollWifi();
}

bool linkSend(Src src, const char *json, size_t len) {
    if (src == Src::Usb) {
        if (len + 1 > MSG_MAX) return false;
        Serial.write((const uint8_t *)json, len);
        Serial.write('\n');
        return true;
    }
    if (!active->authed || len > FRAME_MAX - GCM_TAG) return false;
    if (!active->tx.seal((const uint8_t *)json, len, sealed + 2)) return false;   // after room for the length
    size_t n = len + GCM_TAG;
    sealed[0] = (uint8_t)(n >> 8);
    sealed[1] = (uint8_t)n;
    if (!writeWhole(active->c, sealed, n + 2)) {
        active->close();
        return false;
    }
    return true;
}

bool linkNetAuthed() { return active->authed; }

const char *linkHost() { return active->authed ? active->host : ""; }

void linkNamePeer(const char *name) {
    strlcpy(active->peer, name ? name : "", sizeof(active->peer));
    for (char *p = active->peer; *p; p++) if (*p == '"' || *p == '\\' || (uint8_t)*p < 32) *p = ' ';   // it goes into a JSON refusal
}

WifiStatus linkWifi() { return wifi; }
