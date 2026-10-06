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
    Sealer tx, rx;
    uint8_t *buf = nullptr;   // FRAME_MAX + 2 bytes
    size_t have = 0;

    void close() {
        if (used) c.stop();
        used = keyed = authed = false;
        have = 0;
    }
};

// Two fixed slots; promotion swaps the pointers (GCM contexts never move).
Session slots[2];
Session *active = &slots[0];
Session *pending = &slots[1];
uint8_t *plain;    // FRAME_MAX bytes
uint8_t *sealed;   // FRAME_MAX + 2 bytes

bool writeFrame(WiFiClient &c, const uint8_t *b, size_t n) {
    uint8_t hdr[2] = {(uint8_t)(n >> 8), (uint8_t)n};
    return c.write(hdr, 2) == 2 && c.write(b, n) == n;
}

void refuse(Session &s, const char *why) {
    char out[96];
    int n = snprintf(out, sizeof(out), "{\"t\":\"no\",\"why\":\"%s\"}", why);
    writeFrame(s.c, (const uint8_t *)out, n);
    s.close();
}

// Plain "hi" frame from a connecting host.
void handshake(Session &s, const uint8_t *f, size_t n) {
    JsonDocument doc;
    if (deserializeJson(doc, (const char *)f, n) || strcmp(doc["t"] | "", "hi") != 0 || doc["v"] != PROTOCOL_VERSION) {
        refuse(s, "bad hello");
        return;
    }
    if (!cfg.paired) { refuse(s, "keypad is not paired"); return; }
    if (strcmp(doc["host"] | "", cfg.host) != 0) { refuse(s, "paired with another computer"); return; }
    uint8_t nh[16], nd[16], h2d[32], d2h[32];
    if (!hexDecode(doc["n"] | "", nh, 16)) { refuse(s, "bad nonce"); return; }
    esp_fill_random(nd, sizeof(nd));
    if (!deriveKeys(cfg.key, nh, nd, h2d, d2h) || !s.tx.init(d2h) || !s.rx.init(h2d)) { s.close(); return; }
    memset(h2d, 0, 32);
    memset(d2h, 0, 32);
    char out[160], hex[33];
    for (int i = 0; i < 16; i++) sprintf(hex + 2 * i, "%02x", nd[i]);
    int len = snprintf(out, sizeof(out), "{\"t\":\"hi\",\"v\":%d,\"id\":\"%s\",\"n\":\"%s\"}", PROTOCOL_VERSION, devId, hex);
    if (!writeFrame(s.c, (const uint8_t *)out, len)) { s.close(); return; }
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
                MDNS.addServiceTxt("ckeypad", "tcp", "v", "3");
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
    if (!active->tx.seal((const uint8_t *)json, len, sealed)) return false;
    if (!writeFrame(active->c, sealed, len + GCM_TAG)) {
        active->close();
        return false;
    }
    return true;
}

bool linkNetAuthed() { return active->authed; }

WifiStatus linkWifi() { return wifi; }
