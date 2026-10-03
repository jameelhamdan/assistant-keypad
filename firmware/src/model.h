// Everything the screen shows. The app fills it from host messages and key
// presses; the UI only reads it.
#pragma once

#include <stdint.h>

#include "config.h"
#include "link.h"

enum class Tone : uint8_t { Accent, Ok, Warn, Danger, Dim, Info };

struct SessionInfo {
    char id[9];
    char project[40];
    char name[40];        // session title, as on its terminal tab
    char state[16];
    char title[64];
    char detail[240];
    char mode[24];        // Claude Code permission mode: "" (unknown), default, acceptEdits, plan, auto, dontAsk, bypassPermissions
    uint32_t startedAt;   // millis() estimate from `since`
};

// One entry of the mirrored transcript, drawn like the terminal:
// 'u' > prompt   'c' ⏺ Claude's text   't' ⏺ Tool(args)   'r' ⎿ result
// Its text is NUL-terminated in StatusModel::logText. Claude's text may carry
// style markers: \x01 toggles bold (headings, **bold**), \x02 toggles code.
struct LogEntry {
    char k;
    uint16_t off;
};

constexpr char MARK_BOLD = '\x01', MARK_CODE = '\x02';

struct StatusModel {
    uint8_t n;
    SessionInfo s[MAX_SESSIONS];
    char sel[9];
    bool pinned;
    uint8_t queue;
    bool paused;
    bool menu;            // Enter on the status screen opens "Send to Claude"
    uint8_t nLog;
    LogEntry log[MAX_LOG]; // transcript of the selected session, oldest first
    char *logText;         // LOG_POOL bytes (PSRAM)
    uint16_t logVer;       // bumped whenever the transcript changes (the renderer re-wraps)
};

// select: a Claude Code dialog (permission, question): title, body, question, numbered options
// multi:  the same with checkboxes and a final Submit row
// prompt: the transcript with numbered choices below it (Claude finished, Send to Claude)
enum class Tpl : uint8_t { Select, Multi, Prompt };

struct ScreenModel {
    bool active;
    char id[49];
    Tpl tpl;
    Tone tone;
    char title[128];
    char project[40];
    char body[1400];
    bool diff;            // body is a diff: "+ " lines green, "- " lines red (edit approvals)
    char q[128];          // the question above the options ("Do you want to proceed?")
    uint8_t nItems;
    char items[MAX_ITEMS][64];
    char notes[MAX_ITEMS][48];   // prompt: a dim description after each suggestion
    bool picked[MAX_ITEMS];
    char esc[12];         // what Esc does: "pc" (leave it to the computer) or "back"
    uint32_t shownAt;
    uint32_t expiresAt;   // 0 = no timeout
    int8_t cursor;        // highlighted option; -1 = the body has focus (4/8 scroll it); nItems = Submit (multi)
    uint8_t top;          // first visible option (set by the renderer)
    int16_t scroll;       // first visible body line
    int16_t maxScroll;    // set by the renderer
};

enum class Mode : uint8_t { Boot, Waiting, Status, Sessions, Screen, Test, Ota };

struct Model {
    Mode mode;
    bool dark;
    uint8_t brightness;
    bool dimmed;              // idle: backlight down; the next key only wakes it, a request wakes it fully
    uint32_t lastActivity;    // millis() of the last key, request or toast
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
    int16_t logScroll;        // transcript rows scrolled back from the newest (status screen)
    int16_t logMax;           // set by the renderer
    int8_t pick;              // session picker cursor: 0 = follow latest, 1..n = status.s[pick - 1]
    ScreenModel screen;

    char sent[64];            // "Allow" or the picked item, shown briefly after a press
    uint32_t sentUntil;
    char toast[128];
    Tone toastTone;
    uint32_t toastUntil;

    uint16_t testKeys;        // bit n = key n held (test mode)
    int32_t testEncoder;
    int8_t otaPct;
};

extern Model model;
