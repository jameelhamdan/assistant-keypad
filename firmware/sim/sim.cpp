// Keypad simulator: the real app.cpp, ui.cpp and text/ run on the PC against stand-ins for the hardware.
// It reads commands on stdin and answers on stdout, so a script, a test or a web page can drive it:
//   msg <json>        a message from the host (screen, status, settings, ...)
//   key <n> [ms]      press and release key n (0 = encoder click, 9 = mic) after holding it for ms
//   down <n> / up <n> press / release key n
//   turn <steps>      turn the encoder (+ clockwise)
//   wait <ms>         let simulated time pass
//   shot <file.ppm>   write the current frame (320x170, binary PPM)
// Everything the keypad would send to the host is printed as "TX <json>".
#include <Arduino.h>
#include <stdio.h>
#include <string.h>


#include "app.h"
#include "config.h"
#include "input.h"
#include "link.h"
#include "model.h"
#include "ui.h"

uint32_t sim_now = 1000;
uint16_t sim_frame[320 * 170];
EspClass ESP;
SerialClass Serial;

// ---- stand-ins for link / input / store / ota / crypto ----
static MessageHandler handler;
void linkBegin(MessageHandler h, const char *) { handler = h; }
void linkPoll() {}
bool linkSend(Src, const char *json, size_t len) { printf("TX %.*s\n", (int)len, json); fflush(stdout); return true; }
void linkConfigure(const Stored &) {}
bool linkNetAuthed() { return false; }
WifiStatus linkWifi() { return {WifiState::Up, "192.168.1.40", -52}; }

void storeLoad(Stored &s) { memset(&s, 0, sizeof s); s.dark = true; s.brightness = 80; strcpy(s.name, "Sim keypad"); }
void storeSave(const Stored &) {}

bool otaBegin(size_t, const char *, const char **) { return false; }
bool otaWrite(size_t, const char *, const char **) { return false; }
bool otaFinish(const char **) { return false; }
bool otaRunning() { return false; }
int otaPercent() { return 0; }
void otaAbort() {}

bool hexDecode(const char *, uint8_t *, size_t) { return false; }
bool cryptoSelfTest() { return true; }

static KeyEvent q[64];
static int qn = 0, qh = 0;
static int32_t steps = 0;
static bool down_[16];
static uint32_t pressedAt[16];
void inputBegin() {}
void inputPoll() {}
bool inputNext(KeyEvent &ev) { if (qh == qn) return false; ev = q[qh++]; return true; }
int32_t inputTakeSteps() { int32_t s = steps; steps = 0; return s; }
bool inputHeld(uint8_t key, uint32_t ms) { return key < 16 && down_[key] && sim_now - pressedAt[key] >= ms; }

static void push(uint8_t key, KeyAction a) {
    bool others = false;
    for (int i = 0; i < 16; i++) if (i != key && down_[i] && i != KEY_MIC) others = true;
    if (a == KeyAction::Press) { down_[key] = true; pressedAt[key] = sim_now; } else down_[key] = false;
    if (qn < 64) q[qn++] = {key, a, sim_now, pressedAt[key], !others};
}


// ---- frame loop, like main.cpp ----
static uint32_t lastFrame = 0;
static void step(uint32_t ms) {
    for (uint32_t t = 0; t < ms; t += 20) {
        sim_now += 20;
        bool changed = appLoop();
        if (changed || sim_now - lastFrame > (uiAnimating(model) ? 120u : 1000u)) { uiRender(model); lastFrame = sim_now; }
    }
}

int main() {
    uiBegin();
    appBegin();
    if (getenv("SIM_TRACE")) fprintf(stderr, "[sim] booted\n");
    step(1400);   // past the splash
    if (getenv("SIM_TRACE")) fprintf(stderr, "[sim] splash done\n");
    char line[20000];
    while (fgets(line, sizeof line, stdin)) {
        size_t n = strlen(line);
        while (n && (line[n - 1] == '\n' || line[n - 1] == '\r')) line[--n] = 0;
        if (!strncmp(line, "msg ", 4)) {
            static char buf[20000];
            strcpy(buf, line + 4);
            handler(buf, strlen(buf), Src::Usb);
            step(300);
        } else if (!strncmp(line, "key ", 4)) {
            int k = 0, hold = 120;
            sscanf(line + 4, "%d %d", &k, &hold);
            push(k, KeyAction::Press); step(hold); push(k, KeyAction::Release); step(200);
        } else if (!strncmp(line, "down ", 5)) { push(atoi(line + 5), KeyAction::Press); step(60);
        } else if (!strncmp(line, "up ", 3)) { push(atoi(line + 3), KeyAction::Release); step(60);
        } else if (!strncmp(line, "turn ", 5)) { steps += atoi(line + 5); step(200);
        } else if (!strncmp(line, "wait ", 5)) { step(atoi(line + 5));
        } else if (!strncmp(line, "shot ", 5)) {
            step(100);
            FILE *f = fopen(line + 5, "wb");
            if (f) {
                fprintf(f, "P6\n320 170\n255\n");
                for (int i = 0; i < 320 * 170; i++) {
                    uint16_t c = sim_frame[i];
                    uint8_t px[3] = {(uint8_t)((c >> 8) & 0xF8), (uint8_t)((c >> 3) & 0xFC), (uint8_t)((c << 3) & 0xF8)};
                    fwrite(px, 1, 3, f);
                }
                fclose(f);
            }
            printf("SHOT %s\n", line + 5); fflush(stdout);
        } else if (!strcmp(line, "quit")) break;
    }
    return 0;
}
