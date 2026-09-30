// Everything the screen shows. The app fills it from host messages and key
// presses; the UI only reads it.
#pragma once

#include <stdint.h>

#include "config.h"
#include "link.h"

enum class Tone : uint8_t { Accent, Ok, Warn, Danger, Dim, Info };

struct KeyBind {
    bool set;
    char label[20];
    char act[20];
    Tone tone;
};

struct SessionInfo {
    char id[9];
    char project[40];
    char state[16];
    char title[64];
    char detail[240];
    uint32_t startedAt;   // millis() estimate from `since`
};

struct StatusModel {
    uint8_t n;
    SessionInfo s[MAX_SESSIONS];
    char sel[9];
    bool pinned;
    uint8_t queue;
    bool paused;
    KeyBind keys[9];      // index = key number (0 unused)
};

enum class Tpl : uint8_t { Prompt, List, Multi };

struct ScreenModel {
    bool active;
    char id[49];
    Tpl tpl;
    Tone tone;
    char title[128];
    char project[40];
    char body[1400];
    uint8_t nItems;
    char items[MAX_ITEMS][64];
    bool picked[MAX_ITEMS];
    KeyBind keys[9];
    char click[20];
    uint32_t shownAt;
    uint32_t expiresAt;   // 0 = no timeout
    uint8_t page;
    int16_t scroll;       // first visible body line (prompt)
    int16_t maxScroll;    // set by the renderer
};

enum class Mode : uint8_t { Boot, Waiting, Status, Screen, Test, Ota };

struct Model {
    Mode mode;
    bool dark;
    uint8_t brightness;
    char name[25];
    char id[10];
    char host[32];            // host name from hello_ack
    bool paired;

    bool usbHost, wifiHost;   // which links currently have a live host
    WifiState wifi;
    char ssid[33];
    char ip[16];
    int rssi;
    int8_t battery;           // percent, -1 unknown / on USB power

    StatusModel status;
    int8_t view;              // index into status.s shown on the status screen
    ScreenModel screen;

    char sent[24];            // "Allow" etc. shown briefly after a press
    uint32_t sentUntil;
    char toast[128];
    Tone toastTone;
    uint32_t toastUntil;

    uint16_t testKeys;        // bit n = key n held (test mode)
    int32_t testEncoder;
    int8_t otaPct;
};

extern Model model;
