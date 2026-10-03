// Renderer in Claude Code's visual language: a monospace terminal grid, the
// Claude Code theme colors, and its glyphs (✻ ⏺ ⎿ ❯, rounded dialog boxes,
// numbered option lists). The whole frame is drawn into
// an off-screen canvas and flushed at once.
#include "ui.h"

// U8g2lib.h must come first: Arduino_GFX enables its U8g2 font engine only
// when that header is available.
#include <U8g2lib.h>
#include <Arduino_GFX_Library.h>
#include <string.h>

#include "config.h"
#include "text/arabic.h"
#include "text/text.h"

namespace {

// ---- display -----------------------------------------------------------------------
Arduino_DataBus *bus = new Arduino_ESP32LCD8(PIN_LCD_DC, PIN_LCD_CS, PIN_LCD_WR, PIN_LCD_RD, PIN_LCD_D[0],
                                             PIN_LCD_D[1], PIN_LCD_D[2], PIN_LCD_D[3], PIN_LCD_D[4], PIN_LCD_D[5],
                                             PIN_LCD_D[6], PIN_LCD_D[7]);
Arduino_GFX *panel = new Arduino_ST7789(bus, PIN_LCD_RST, 0, true, 170, 320, 35, 0, 35, 0);
Arduino_Canvas *cv = nullptr;
bool ready = false;

// ---- theme: Claude Code's terminal palette -----------------------------------------------
constexpr uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) {
    return (uint16_t)(((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3));
}

struct Theme {
    uint16_t bg, text, dim, faint, claude, permission, success, error, warning, info, selBg;
};
constexpr Theme DARK = {
    rgb(18, 18, 18),    rgb(255, 255, 255), rgb(153, 153, 153), rgb(68, 68, 68),
    rgb(215, 119, 87),  rgb(177, 185, 249), rgb(78, 186, 101),  rgb(255, 107, 128),
    rgb(255, 193, 7),   rgb(122, 180, 232), rgb(40, 40, 40),
};
constexpr Theme LIGHT = {
    rgb(255, 255, 255), rgb(0, 0, 0),       rgb(102, 102, 102), rgb(204, 204, 204),
    rgb(215, 119, 87),  rgb(87, 105, 247),  rgb(44, 122, 57),   rgb(171, 43, 63),
    rgb(150, 108, 30),  rgb(40, 110, 190),  rgb(238, 238, 238),
};
const Theme *T = &DARK;

uint16_t tone(Tone t) {
    switch (t) {
        case Tone::Ok: return T->success;
        case Tone::Warn: return T->permission;   // decisions use the permission-dialog color
        case Tone::Danger: return T->error;
        case Tone::Dim: return T->dim;
        case Tone::Info: return T->info;
        default: return T->claude;
    }
}

// This state taxonomy is duplicated by hand in three other places that must
// stay in sync: host/keypad/core/sessions.py (BUSY_STATES/WAITING_STATES/
// WORKING_STATES), host/keypad/tray.py (state_word/_color_for), and
// stateWord()/waiting() further down this file. A new state added on one
// side needs updating in all four.
bool busy(const char *s) {
    return !strcmp(s, "working") || !strcmp(s, "thinking") || !strcmp(s, "tool") || !strcmp(s, "continuing");
}

uint16_t stateColor(const char *s) {
    if (busy(s)) return T->claude;
    if (!strcmp(s, "permission") || !strcmp(s, "question") || !strcmp(s, "input") || !strcmp(s, "stopped")) return T->permission;
    if (!strcmp(s, "failed")) return T->error;
    if (!strcmp(s, "idle") || !strcmp(s, "done")) return T->success;
    return T->dim;
}

// ---- terminal grid ---------------------------------------------------------------------------
struct Font {
    const uint8_t *data;
    int8_t cw;       // cell width
    int8_t ascent;   // baseline offset within a row
    int8_t line;     // row pitch
};
const Font MONO = {u8g2_font_spleen8x16_mf, 8, 12, 16};
const Font SMALL = {u8g2_font_spleen6x12_mf, 6, 9, 12};
const Font ARABIC = {u8g2_font_unifont_t_arabic, 8, 13, 16};

constexpr int16_t X0 = 4;        // left margin
constexpr int16_t COLS = 39;     // (320 - 2 * 4) / 8

// ---- text ----------------------------------------------------------------------------------
int measureWith(const Font &f, const char *s, size_t len) {
    static char tmp[1600];
    if (len >= sizeof(tmp)) len = sizeof(tmp) - 1;
    memcpy(tmp, s, len);
    tmp[len] = '\0';
    if (textHasArabic(tmp)) return arabicCellCount(tmp) * ARABIC.cw;
    return utf8Length(tmp) * f.cw;   // monospace: cells x cell width
}

int measureCb(const char *s, size_t len, void *ctx) { return measureWith(*(const Font *)ctx, s, len); }

// One line at (x, top y); Arabic is shaped and laid out right-to-left. align 'L' | 'R' | 'C'.
int16_t drawLine(const Font &f, int16_t x, int16_t y, int16_t w, const char *s, size_t len, uint16_t color,
                 char align = 'L', bool bold = false) {
    static char line[512], visual[1024];
    if (len >= sizeof(line)) len = sizeof(line) - 1;
    memcpy(line, s, len);
    line[len] = '\0';
    const char *out = line;
    const Font *use = &f;
    int width;
    if (textHasArabic(line)) {
        char base = textBaseDirection(line);
        width = arabicToVisual(line, base, visual, sizeof(visual)) * ARABIC.cw;
        out = visual;
        use = &ARABIC;
        if (align == 'L' && base == 'R') align = 'R';
    } else {
        width = utf8Length(line) * f.cw;
    }
    int16_t cx = align == 'R' ? x + w - width : align == 'C' ? x + (w - width) / 2 : x;
    cv->setFont(use->data);
    cv->setTextColor(color);
    cv->setCursor(cx, y + use->ascent);
    cv->print(out);
    if (bold) {   // terminal-style bold: overstrike one pixel to the right
        cv->setCursor(cx + 1, y + use->ascent);
        cv->print(out);
    }
    return cx + width;
}

int16_t text(const Font &f, int16_t x, int16_t y, const char *s, uint16_t color, bool bold = false) {
    return drawLine(f, x, y, 320, s, strlen(s), color, 'L', bold);
}

// Single line cut to `cells` with an ellipsis.
int16_t fit(const Font &f, int16_t x, int16_t y, int cells, const char *s, uint16_t color, bool bold = false) {
    static char buf[256];
    size_t len = strlen(s);
    int w = cells * f.cw;
    if (measureWith(f, s, len) <= w) return drawLine(f, x, y, w, s, len, color, 'L', bold);
    while (len > 0 && measureWith(f, s, len) + f.cw > w) {
        len--;
        while (len > 0 && ((unsigned char)s[len] & 0xC0) == 0x80) len--;
    }
    // Spleen has no U+2026: draw the ellipsis as three dots in one cell.
    snprintf(buf, sizeof(buf), "%.*s", (int)len, s);
    int16_t end = drawLine(f, x, y, w, buf, strlen(buf), color, 'L', bold);
    for (int i = 0; i < 3; i++) cv->fillRect(end + 1 + i * 2, y + f.ascent - 1, 1, 1, color);
    return end + f.cw;
}

TextLine lines[128];
// Wrapped paragraph in a box of `cells` x `rows`; returns rows drawn, *total all rows.
// diff: each line colored by its paragraph's first character, "+" added
// (green), "-" removed (red); the first paragraph (the file) dim.
int wrap(const Font &f, int16_t x, int16_t y, int cells, int rows, const char *s, uint16_t color, int skip = 0,
         int *total = nullptr, bool diff = false) {
    uint8_t n = textWrap(s, cells * f.cw, measureCb, (void *)&f, lines, 128);
    if (total) *total = n;
    int drawn = 0;
    uint16_t c = diff ? T->dim : color;
    for (int i = 0; i < n; i++) {
        if (diff) {
            int k = lines[i].start;
            while (k > 0 && s[k - 1] == ' ') k--;
            if (k > 0 && s[k - 1] == '\n') {   // a new paragraph: a diff line
                char m = s[lines[i].start];
                c = m == '+' ? T->success : m == '-' ? T->error : color;
            }
        }
        if (i < skip || drawn >= rows) continue;
        drawLine(f, x, y + drawn * f.line, cells * f.cw, s + lines[i].start, lines[i].len, c);
        drawn++;
    }
    return drawn;
}

// ---- Claude Code glyphs (drawn, so they are crisp at any font) -------------------------------------
// ⏺ record dot, centered in a text cell whose top is y
void gDot(int16_t x, int16_t y, uint16_t c) { cv->fillCircle(x + 3, y + 8, 3, c); }

// ⎿ result elbow
void gElbow(int16_t x, int16_t y, uint16_t c) {
    cv->drawFastVLine(x + 3, y + 1, 8, c);
    cv->drawFastHLine(x + 3, y + 8, 5, c);
}

// ❯ selection chevron
void gChevron(int16_t x, int16_t y, uint16_t c) {
    for (int t = 0; t < 2; t++) {
        cv->drawLine(x + 1 + t, y + 4, x + 5 + t, y + 8, c);
        cv->drawLine(x + 5 + t, y + 8, x + 1 + t, y + 12, c);
    }
}

// ✻ Claude's asterisk; `phase` animates it like the Claude Code spinner (· ✢ ✳ ✶ ✻ ✽).
void gStar(int16_t x, int16_t y, uint16_t c, int phase = 4) {
    static const int8_t R[] = {1, 2, 3, 4, 4, 3, 2, 1};
    int r = R[phase & 7];
    int16_t cx = x + 3, cy = y + 8;
    static const int8_t DX[] = {0, 3, 3, 0, -3, -3}, DY[] = {-4, -2, 2, 4, 2, -2};
    for (int i = 0; i < 6; i++) cv->drawLine(cx, cy, cx + DX[i] * r / 4, cy + DY[i] * r / 4, c);
    cv->fillRect(cx - (r > 2), cy - (r > 2), 1 + 2 * (r > 2), 1 + 2 * (r > 2), c);
}

// ✓ inside a [ ] checkbox cell
void gCheck(int16_t x, int16_t y, uint16_t c) {
    cv->drawLine(x + 1, y + 8, x + 3, y + 10, c);
    cv->drawLine(x + 3, y + 10, x + 7, y + 5, c);
    cv->drawLine(x + 1, y + 9, x + 3, y + 11, c);
    cv->drawLine(x + 3, y + 11, x + 7, y + 6, c);
}

// Rounded dialog box (╭─╮ │ │ ╰─╯)
void box(int16_t x, int16_t y, int16_t w, int16_t h, uint16_t c) { cv->drawRoundRect(x, y, w, h, 6, c); }

void rule(int16_t y, uint16_t c) { cv->drawFastHLine(X0, y, 320 - 2 * X0, c); }

// ---- status bar icons -----------------------------------------------------------------------------
void battery(int16_t x, int16_t y, int pct) {   // 16x8
    cv->drawRect(x, y, 14, 8, T->dim);
    cv->fillRect(x + 14, y + 2, 2, 4, T->dim);
    uint16_t c = pct <= 15 ? T->error : pct <= 30 ? T->warning : T->dim;
    cv->fillRect(x + 2, y + 2, (10 * pct + 50) / 100, 4, c);
}

// Wi-Fi signal bars (11x9). Bright = connected (and the PC talks over it),
// dim = connected but idle / joining, red = can't join.
void wifiBars(int16_t x, int16_t y, const Model &m) {
    int bars = m.wifi != WifiState::Up ? 3 : m.rssi > -60 ? 3 : m.rssi > -72 ? 2 : 1;
    uint16_t on = m.wifi == WifiState::Failed ? T->error
                  : m.wifi == WifiState::Up   ? (m.wifiHost ? T->text : T->dim)
                                              : T->faint;
    for (int i = 0; i < 3; i++) {
        int h = 3 + i * 3;
        cv->fillRect(x + i * 4, y + 9 - h, 3, h, i < bars ? on : T->faint);
    }
    if (m.wifi == WifiState::Failed) {   // small x over the bars
        cv->drawLine(x + 1, y, x + 5, y + 4, T->error);
        cv->drawLine(x + 5, y, x + 1, y + 4, T->error);
    }
}

// Right-aligned indicators: [usb] [wifi bars] [battery]. Returns the x where they start.
int16_t indicators(const Model &m, int16_t y) {
    int16_t x = 320 - X0;
    if (m.battery >= 0) {
        x -= 16;
        battery(x, y + 4, m.battery);
        x -= 6;
    }
    if (m.wifi != WifiState::Off) {
        x -= 11;
        wifiBars(x, y + 3, m);
        x -= 6;
    }
    if (m.usbHost || m.wifi == WifiState::Off) {
        x -= 3 * SMALL.cw;
        text(SMALL, x, y + 2, "usb", m.usbHost ? T->text : T->faint);
        x -= 6;
    }
    return x;
}

void fmtElapsed(char *out, size_t n, uint32_t ms) {
    uint32_t s = ms / 1000;
    if (s < 60) snprintf(out, n, "%us", (unsigned)s);
    else if (s < 3600) snprintf(out, n, "%um %us", (unsigned)(s / 60), (unsigned)(s % 60));
    else snprintf(out, n, "%uh %um", (unsigned)(s / 3600), (unsigned)(s / 60 % 60));
}

// Claude Code shows a playful verb while it works; keep one per session.
const char *gerund(const char *seed) {
    static const char *G[] = {"Thinking", "Pondering", "Brewing", "Musing", "Noodling", "Percolating",
                              "Conjuring", "Crafting", "Cogitating", "Simmering"};
    unsigned h = 0;
    for (const char *p = seed; *p; p++) h = h * 31 + (unsigned char)*p;
    return G[h % (sizeof(G) / sizeof(G[0]))];
}

// Bottom hint line in the dim "? for shortcuts" style, or a toast / sent note.
void hintLine(const Model &m, const char *hint) {
    int16_t y = 170 - SMALL.line - 2;
    uint32_t now = millis();
    if (m.sentUntil && (int32_t)(m.sentUntil - now) > 0) {
        gElbow(X0, y - 3, T->dim);
        fit(SMALL, X0 + 12, y, 50, m.sent, T->success);
        return;
    }
    if (m.toastUntil && (int32_t)(m.toastUntil - now) > 0) {
        gElbow(X0, y - 3, T->dim);
        // tone(Warn) is the permission-dialog color; a warning note is amber
        fit(SMALL, X0 + 12, y, 50, m.toast, m.toastTone == Tone::Warn ? T->warning : tone(m.toastTone));
        return;
    }
    if (hint) fit(SMALL, X0 + 2, y, 51, hint, T->dim);
}

// Small solid triangle (5 px), pointing up or down: "more above / below".
void gTri(int16_t x, int16_t y, bool up, uint16_t c) {
    for (int i = 0; i < 3; i++) cv->drawFastHLine(x + i, up ? y + 2 - i : y + i, 5 - 2 * i, c);   // widest row at the base
}

// ⏵ (one of the "⏵⏵ accept edits on" pair), 4x7 px
void gPlay(int16_t x, int16_t y, uint16_t c) {
    for (int i = 0; i < 4; i++) cv->drawFastVLine(x + i, y + i, 7 - 2 * i, c);
}

uint16_t modeColor(const char *mode) {
    if (!strcmp(mode, "default")) return T->dim;   // manual: every action asks
    if (!strcmp(mode, "plan")) return T->info;
    if (!strcmp(mode, "bypassPermissions")) return T->error;
    return T->permission;   // acceptEdits, auto, dontAsk
}

// What Claude Code's status line says; a mode this firmware doesn't know is shown as sent.
const char *modeLabel(const char *mode) {
    if (!mode[0]) return nullptr;
    if (!strcmp(mode, "default")) return "manual";
    if (!strcmp(mode, "acceptEdits")) return "accept edits on";
    if (!strcmp(mode, "plan")) return "plan mode on";
    if (!strcmp(mode, "auto")) return "auto mode on";
    if (!strcmp(mode, "dontAsk")) return "don't ask on";
    if (!strcmp(mode, "bypassPermissions")) return "bypass permissions on";
    return mode;
}

// A note (the last press, or a toast) replaces the hint line for a moment.
bool noteActive(const Model &m) {
    uint32_t now = millis();
    return (m.sentUntil && (int32_t)(m.sentUntil - now) > 0) || (m.toastUntil && (int32_t)(m.toastUntil - now) > 0);
}

// ---- screens ------------------------------------------------------------------------------------
void drawBoot() {
    box(X0, 36, 320 - 2 * X0, 70, T->claude);
    gStar(X0 + 14, 52, T->claude, (millis() / 120) & 7);
    text(MONO, X0 + 30, 52, "Welcome to", T->text);
    text(MONO, X0 + 30 + 11 * MONO.cw, 52, "Keypad", T->text, true);
    text(MONO, X0 + 30, 72, "for Claude Code", T->dim);
    char v[40];
    snprintf(v, sizeof(v), "v%s", KEYPAD_FW_VERSION);
    drawLine(SMALL, 0, 120, 320, v, strlen(v), T->dim, 'C');
}

void drawWaiting(const Model &m) {
    int16_t w = 320 - 2 * X0;
    box(X0, 4, w, 118, T->claude);
    int16_t x = X0 + 12, y = 14;
    gStar(x, y, T->claude, 4);
    fit(MONO, x + 2 * MONO.cw, y, 30, m.name, T->text, true);
    y += 24;
    gStar(x, y, T->claude, (millis() / 150) & 7);
    text(MONO, x + 2 * MONO.cw, y, "Waiting for your computer", T->claude);
    y += 22;
    char line[96];
    if (!m.paired) {
        wrap(MONO, x + 2 * MONO.cw, y, 34, 3, "Connect with USB, then add it from the Keypad app to set up Wi-Fi.", T->dim);
    } else {
        switch (m.wifi) {
            case WifiState::Up: snprintf(line, sizeof(line), "wifi  %s  %s", m.ssid, m.ip); break;
            case WifiState::Connecting: snprintf(line, sizeof(line), "wifi  joining %s", m.ssid); break;
            case WifiState::Failed: snprintf(line, sizeof(line), "wifi  can't join %s", m.ssid); break;
            default: snprintf(line, sizeof(line), "wifi  not set up"); break;
        }
        gElbow(x, y, T->dim);
        fit(MONO, x + 2 * MONO.cw, y, 34, line, m.wifi == WifiState::Failed ? T->error : T->dim);
        gElbow(x, y + 18, T->dim);
        text(MONO, x + 2 * MONO.cw, y + 18, "is the Keypad app running?", T->dim);
    }
    snprintf(line, sizeof(line), "%s  v%s  hold 1: key test", m.id, KEYPAD_FW_VERSION);
    indicators(m, 128);
    text(SMALL, X0 + 2, 132, line, T->dim);
    hintLine(m, nullptr);
}

// Small-font variants of the transcript glyphs (rows of SMALL.line px).
void gDotS(int16_t x, int16_t y, uint16_t c) { cv->fillCircle(x + 2, y + 6, 2, c); }
void gElbowS(int16_t x, int16_t y, uint16_t c) {
    cv->drawFastVLine(x + 2, y, 6, c);
    cv->drawFastHLine(x + 2, y + 6, 4, c);
}

// Session states in a word, for the picker. See the cross-reference comment
// on busy() above: this and waiting() are the other two copies of the state
// taxonomy that have to stay in sync with it.
const char *stateWord(const char *s) {
    if (busy(s)) return "working";
    if (!strcmp(s, "permission") || !strcmp(s, "question") || !strcmp(s, "input") || !strcmp(s, "stopped")) return "needs you";
    if (!strcmp(s, "failed")) return "failed";
    return "idle";
}

bool waiting(const char *s) {
    return !strcmp(s, "permission") || !strcmp(s, "question") || !strcmp(s, "input");
}

// ---- the transcript, wrapped once per change ----------------------------------------
// Rows of the wrapped transcript; flags: 1 bold and 2 code at the row's
// start (the style markers carry over line breaks), 4 the entry's first row.
struct Row {
    uint8_t e;
    uint8_t flags;
    uint16_t start, len;
};
constexpr int MAX_ROWS = 1024;
constexpr int LOG_CELLS = (320 - 2 * X0) / 6 - 2;   // text after the 2-cell gutter
Row rowsBuf[MAX_ROWS];
int nRows = 0;
uint16_t wrappedVer = 0xFFFF;

// Width of text, not counting the style markers.
int measureRich(const char *s, size_t len, void *ctx) {
    static char tmp[1024];
    size_t n = 0;
    for (size_t i = 0; i < len && n < sizeof(tmp) - 1; i++)
        if (s[i] != MARK_BOLD && s[i] != MARK_CODE) tmp[n++] = s[i];
    return measureWith(*(const Font *)ctx, tmp, n);
}

void rewrap(const StatusModel &st) {
    static TextLine wl[255];
    nRows = 0;
    for (int i = 0; i < st.nLog; i++) {
        const char *t = st.logText + st.log[i].off;
        uint8_t n = textWrap(t, LOG_CELLS * SMALL.cw, measureRich, (void *)&SMALL, wl, 255);
        uint8_t style = 0;
        uint16_t pos = 0;
        for (int l = 0; l < n; l++) {
            for (; pos < wl[l].start; pos++) {
                if (t[pos] == MARK_BOLD) style ^= 1;
                else if (t[pos] == MARK_CODE) style ^= 2;
            }
            if (nRows == MAX_ROWS) {   // keep the newest rows
                memmove(rowsBuf, rowsBuf + 256, (MAX_ROWS - 256) * sizeof(Row));
                nRows -= 256;
            }
            rowsBuf[nRows++] = {(uint8_t)i, (uint8_t)(style | (l == 0 ? 4 : 0)), wl[l].start, wl[l].len};
        }
    }
    wrappedVer = st.logVer;
}

// One row with its styles: bold (headings, **bold**) and code (`code`, code blocks).
void drawRich(const Font &f, int16_t x, int16_t y, int16_t w, const char *s, size_t len, uint16_t base, uint8_t style) {
    size_t i = 0;
    while (i < len) {
        size_t j = i;
        while (j < len && s[j] != MARK_BOLD && s[j] != MARK_CODE) j++;
        if (j > i) x = drawLine(f, x, y, w, s + i, j - i, (style & 2) ? T->permission : base, 'L', style & 1);
        if (j < len) style ^= s[j] == MARK_BOLD ? 1 : 2;
        i = j + 1;
    }
}

int transcriptRows(const StatusModel &st) {
    if (wrappedVer != st.logVer) rewrap(st);
    return nRows;
}

// The selected session's transcript, newest at the bottom, the way the
// terminal shows it:   > prompt   ⏺ Claude's text   ⏺ Tool(args)   ⎿ result
// `skip` rows at the bottom are scrolled away (back in time). Returns the
// total number of rows, so the caller knows how far it can scroll.
int drawTranscript(const StatusModel &st, int16_t top, int16_t bottom, int skip) {
    const Font &f = SMALL;
    int total = transcriptRows(st);
    int rows = (bottom - top) / f.line;
    int last = total - skip;
    int first = last - rows > 0 ? last - rows : 0;
    int16_t y = top + (rows - (last - first)) * f.line;   // short transcripts sit at the bottom
    for (int r = first; r < last; r++, y += f.line) {
        const Row &w = rowsBuf[r];
        const LogEntry &e = st.log[w.e];
        const char *t = st.logText + e.off;
        uint16_t c = T->text, mark = T->text;
        if (e.k == 'u') c = mark = T->dim;
        else if (e.k == 't') mark = T->success;
        else if (e.k == 'r') c = mark = !strncmp(t, "Error", 5) || !strncmp(t, "Denied", 6) ? T->error : T->dim;
        drawRich(f, X0 + 2 * f.cw, y, LOG_CELLS * f.cw, t + w.start, w.len, c, w.flags & 3);
        if (!(w.flags & 4)) continue;
        if (e.k == 'u') text(f, X0, y, ">", T->dim);
        else if (e.k == 'r') gElbowS(X0, y, mark);
        else gDotS(X0, y, mark);
    }
    return total;
}

// Claude Code's welcome box, for a session with nothing in its transcript yet.
void drawWelcome(const SessionInfo &s, int16_t y) {
    box(X0, y, 320 - 2 * X0, 44, T->claude);
    gStar(X0 + 8, y + 5, T->claude, 4);
    text(MONO, X0 + 8 + 2 * MONO.cw, y + 5, "Welcome to Claude Code!", T->text, true);
    char cwd[64];
    snprintf(cwd, sizeof(cwd), "cwd: %s", s.project);
    fit(SMALL, X0 + 8 + 2 * MONO.cw, y + 25, 44, cwd, T->dim);
}

// Header: ✻ session title · project ........ 2/3  usb  battery
const SessionInfo *drawHeader(const Model &m) {
    const StatusModel &st = m.status;
    int16_t right = indicators(m, 3);
    const SessionInfo *s = st.n ? &st.s[m.view < st.n ? m.view : 0] : nullptr;
    gStar(X0, 3, T->claude, 4);
    char sess[16] = "";
    if (st.n > 1) snprintf(sess, sizeof(sess), "%d/%d", m.view + 1, st.n);
    int16_t sessW = strlen(sess) * SMALL.cw;
    if (sess[0]) text(SMALL, right - sessW, 6, sess, T->dim);
    int cells = (right - sessW - 8 - (X0 + 2 * MONO.cw)) / MONO.cw;
    const char *head = s ? (s->name[0] ? s->name : s->project[0] ? s->project : s->id) : (m.host[0] ? m.host : m.name);
    int16_t end = fit(MONO, X0 + 2 * MONO.cw, 3, cells, head, T->text, true);
    if (s && s->name[0] && s->project[0]) {   // the project, dim, after the title
        int rest = (right - sessW - 8 - end) / SMALL.cw - 1;
        if (rest > 4) fit(SMALL, end + SMALL.cw, 6, rest, s->project, T->dim);
    }
    rule(22, T->faint);
    return s;
}

void drawStatus(Model &m) {
    const StatusModel &st = m.status;
    const SessionInfo *s = drawHeader(m);
    int16_t y = 30;
    bool working = s && busy(s->state);
    bool asking = s && waiting(s->state);
    int16_t statusY = 136;   // ✻ Brewing… / waiting on the PC / paused, just above the hints
    bool statusLine = working || asking || st.queue || st.paused;
    m.logMax = 0;
    if (!s) {
        gDot(X0, y, T->dim);
        text(MONO, X0 + 2 * MONO.cw, y, "No Claude Code sessions", T->text);
        gElbow(X0 + 2 * MONO.cw, y + 18, T->dim);
        text(MONO, X0 + 4 * MONO.cw, y + 18, "start claude in a project", T->dim);
    } else if (st.nLog) {
        int16_t bottom = statusLine ? statusY - 2 : 154;
        int rows = (bottom - 26) / SMALL.line;
        // Scrolled back, new lines must not move what you are reading (like a terminal)
        static int lastTotal = -1;
        static char lastSel[sizeof(st.sel)] = "";
        int total = transcriptRows(st);
        if (m.logScroll > 0 && lastTotal >= 0 && total > lastTotal && !strcmp(lastSel, st.sel))
            m.logScroll += total - lastTotal;
        lastTotal = total;
        strlcpy(lastSel, st.sel, sizeof(lastSel));
        // scrolled back, the "newer below" marker takes a row: the limit counts it, so the first line is reachable
        m.logMax = (int16_t)(total > rows ? total - (rows - 1) : 0);
        if (m.logScroll > m.logMax) m.logScroll = m.logMax;
        if (m.logScroll > 0) bottom -= SMALL.line;
        drawTranscript(st, 26, bottom, m.logScroll);
        if (m.logScroll > 0) {   // ▼ 5 newer lines
            char more[48];
            snprintf(more, sizeof(more), "%d newer  (5: jump to latest)", m.logScroll);
            gTri(X0, bottom + 4, false, T->dim);
            text(SMALL, X0 + 2 * SMALL.cw, bottom + 1, more, T->dim);
        }
    } else {
        drawWelcome(*s, 28);
    }
    if (working) {   // ✻ Brewing… (1m 12s)
        char el[16], line[64];
        fmtElapsed(el, sizeof(el), millis() - s->startedAt);
        const char *verb = !strcmp(s->state, "continuing") ? "Continuing" : gerund(s->id);
        snprintf(line, sizeof(line), "%s... (%s)", verb, el);
        gStar(X0, statusY, T->claude, (millis() / 120) & 7);
        text(MONO, X0 + 2 * MONO.cw, statusY, line, T->claude);
    } else if (asking) {   // waiting for an answer on the computer
        gDot(X0, statusY, T->permission);
        fit(MONO, X0 + 2 * MONO.cw, statusY, COLS - 2, s->detail[0] ? s->detail : s->title, T->permission);
    } else if (st.paused) {
        gDot(X0, statusY, T->warning);
        text(MONO, X0 + 2 * MONO.cw, statusY, "Paused: answers stay on the PC", T->warning);
    } else if (s && st.queue) {
        char q[32];
        snprintf(q, sizeof(q), "%u waiting for you", st.queue);
        gDot(X0, statusY, T->permission);
        text(MONO, X0 + 2 * MONO.cw, statusY, q, T->permission);
    }

    // bottom line: key hints on the left, the permission mode on the right ("⏵⏵ accept edits on")
    if (noteActive(m)) {
        hintLine(m, nullptr);
        return;
    }
    const char *mode = s ? modeLabel(s->mode) : nullptr;
    int16_t hy = 170 - SMALL.line - 2;
    int16_t modeX = 320 - X0;
    if (mode) {
        uint16_t c = modeColor(s->mode);
        modeX -= utf8Length(mode) * SMALL.cw;
        text(SMALL, modeX, hy, mode, c);
        modeX -= 13;
        gPlay(modeX, hy + 2, c);
        gPlay(modeX + 5, hy + 2, c);
        modeX -= 6;
    }
    char hint[64];
    snprintf(hint, sizeof(hint), "%s%s%s", st.n > 1 ? "6 sessions  " : "", st.nLog ? "4/8 scroll  " : "",
             st.menu ? "7 send" : "");
    fit(SMALL, X0 + 2, hy, (modeX - X0 - 2) / SMALL.cw, hint, T->dim);
}

// The session picker (6): which sessions the keypad mirrors, which one it
// shows, and whether it follows the latest activity.
void drawSessions(const Model &m) {
    const StatusModel &st = m.status;
    int16_t right = indicators(m, 3);
    gStar(X0, 3, T->claude, 4);
    text(MONO, X0 + 2 * MONO.cw, 3, "Sessions", T->text, true);
    char info[32];
    snprintf(info, sizeof(info), "%u live  %s", st.n, st.pinned ? "pinned" : "following latest");
    int16_t iw = utf8Length(info) * SMALL.cw;
    text(SMALL, right - iw, 6, info, T->dim);
    rule(22, T->faint);

    constexpr int ROWS = 8;
    int16_t y0 = 25;
    int rowsTotal = st.n + 1;
    int top = m.pick - ROWS + 1 > 0 ? m.pick - ROWS + 1 : 0;
    for (int r = top; r < rowsTotal && r < top + ROWS; r++) {
        int16_t y = y0 + (r - top) * 16;
        bool cur = r == m.pick;
        if (cur) gChevron(X0, y, T->claude);
        if (r == 0) {
            fit(MONO, X0 + 5 * MONO.cw, y, 28, "Follow latest activity", cur ? T->claude : T->text);
            if (!st.pinned) gCheck(320 - X0 - 10, y, T->success);
            continue;
        }
        const SessionInfo &x = st.s[r - 1];
        char num[6];
        snprintf(num, sizeof(num), "%d.", r);
        text(MONO, X0 + 2 * MONO.cw, y, num, cur ? T->claude : T->dim);
        uint16_t c = stateColor(x.state);
        cv->fillCircle(X0 + 5 * MONO.cw + 3, y + 8, 3, c);
        const char *name = x.name[0] ? x.name : x.project[0] ? x.project : x.id;
        char meta[48];
        if (busy(x.state)) {
            char el[16];
            fmtElapsed(el, sizeof(el), millis() - x.startedAt);
            snprintf(meta, sizeof(meta), "%s  working %s", x.project, el);
        } else {
            snprintf(meta, sizeof(meta), "%s  %s", x.project, stateWord(x.state));
        }
        int rcells = utf8Length(meta);
        if (rcells > 26) rcells = 26;
        bool shown = st.pinned && !strcmp(x.id, st.sel);
        int16_t rx = 320 - X0 - (shown ? 12 : 0) - rcells * SMALL.cw;
        fit(SMALL, rx, y + 3, rcells, meta, busy(x.state) || waiting(x.state) ? c : T->dim);
        int ncells = (rx - (X0 + 7 * MONO.cw) - 4) / MONO.cw;
        fit(MONO, X0 + 7 * MONO.cw, y, ncells, name, cur ? T->claude : T->text, cur);
        if (shown) gCheck(320 - X0 - 10, y, T->success);
    }
    if (top > 0) gTri(320 - X0 - 6, y0 - 1, true, T->dim);
    if (top + ROWS < rowsTotal) gTri(320 - X0 - 6, y0 + ROWS * 16 - 6, false, T->dim);
    hintLine(m, "4/8 move  7 show  1-3 jump  5 back");
}

// "+2  project 4:59": requests waiting behind this one, the project and the
// countdown, dim and right-aligned so it ends at xRight. Returns its width.
int16_t dialogInfo(const Model &m, int16_t xRight, int16_t y) {
    const ScreenModel &sc = m.screen;
    char info[72];   // never cut the countdown
    int n = 0;
    if (m.status.queue) n = snprintf(info, sizeof(info), "+%u  ", m.status.queue);
    n += snprintf(info + n, sizeof(info) - n, "%s", sc.project);
    if (sc.expiresAt) {
        int32_t left = (int32_t)(sc.expiresAt - millis()) / 1000;
        if (left < 0) left = 0;
        snprintf(info + n, sizeof(info) - n, " %ld:%02ld", (long)(left / 60), (long)(left % 60));
    }
    int cells = utf8Length(info);
    if (cells > 24) cells = 24;
    fit(SMALL, xRight - cells * SMALL.cw, y, cells, info, T->dim);
    return cells * SMALL.cw;
}

// What Esc does, for the hint line.
const char *escHint(const ScreenModel &sc) {
    return !sc.esc[0] ? "" : !strcmp(sc.esc, "pc") ? "  5 answer on PC" : "  5 back";
}

// Keeps the cursor inside a window of `rows` options; returns the first visible.
int windowTop(ScreenModel &sc, int total, int rows) {
    int top = sc.top;
    if (sc.cursor >= 0 && sc.cursor < top) top = sc.cursor;
    if (sc.cursor >= top + rows) top = sc.cursor - rows + 1;
    if (top > total - rows) top = total - rows;
    if (top < 0) top = 0;
    sc.top = (uint8_t)top;
    return top;
}

// Claude Code's dialog: a rounded box, bold title, the command or question,
// and numbered options with the ❯ cursor. Permissions, questions, multi-select.
void drawDialog(Model &m) {
    ScreenModel &sc = m.screen;
    bool multi = sc.tpl == Tpl::Multi;
    uint16_t c = tone(sc.tone);
    int16_t bx = X0, bw = 320 - 2 * X0, bottom = 170 - SMALL.line - 6;
    box(bx, 2, bw, bottom - 2, c);
    int16_t x = bx + 8, cells = (bw - 16) / MONO.cw, y = 7;

    // title  ........  +1  project 4:59
    int16_t iw = dialogInfo(m, x + cells * MONO.cw, y + 3);
    fit(MONO, x, y, cells - iw / MONO.cw - 1, sc.title, c, true);
    y += MONO.line + 3;

    // options take what they need (up to 4 rows), then the question, then the body
    int total = multi ? sc.nItems + 1 : sc.nItems;
    int optRows = total < 4 ? total : 4;
    int16_t optY = bottom - 3 - optRows * MONO.line;
    int qRows = 0;
    if (sc.q[0]) {   // the question gets the rows it needs: up to 2 under a body, 3 without
        int need = textWrap(sc.q, cells * MONO.cw, measureCb, (void *)&MONO, lines, 128);
        int room = (optY - y) / MONO.line;
        qRows = need < (sc.body[0] ? 2 : 3) ? need : (sc.body[0] ? 2 : 3);
        if (qRows > room) qRows = room;
    }
    int16_t qY = optY - qRows * MONO.line - (qRows ? 2 : 0);
    if (!sc.body[0] && qRows) qY = y;   // a question with no body sits under the title

    if (sc.body[0]) {   // the command / plan / text: small font, scrollable
        bool focus = sc.cursor < 0;
        int rows = (qY - 2 - y) / SMALL.line;
        int totalRows = 0;
        int bodyCells = cells * MONO.cw / SMALL.cw - 4;
        uint16_t bc = focus ? T->text : T->dim;
        int shown = rows > 0 ? wrap(SMALL, x + 2 * SMALL.cw, y, bodyCells, rows, sc.body, bc, sc.scroll, &totalRows, sc.diff)
                             : 0;
        sc.maxScroll = (int16_t)(totalRows > rows ? totalRows - rows : 0);
        if (sc.scroll > sc.maxScroll) sc.scroll = sc.maxScroll;
        if (totalRows > shown && rows > 0) {   // a scrollbar, bright while the body has focus
            int track = rows * SMALL.line, thumb = track * shown / totalRows;
            int pos = sc.maxScroll ? (track - thumb) * sc.scroll / sc.maxScroll : 0;
            cv->fillRect(bx + bw - 6, y + pos, 2, thumb < 6 ? 6 : thumb, focus ? c : T->faint);
        }
    } else {
        sc.maxScroll = 0;
    }
    if (qRows) wrap(MONO, x, qY, cells, qRows, sc.q, T->text);

    int top = windowTop(sc, total, optRows);
    for (int r = top; r < total && r < top + optRows; r++) {
        int16_t oy = optY + (r - top) * MONO.line;
        bool cur = r == sc.cursor;
        uint16_t lc = cur ? c : T->text;
        if (cur) gChevron(x, oy, c);
        if (multi && r == sc.nItems) {   // the Submit row
            text(MONO, x + 2 * MONO.cw, oy, "Submit", cur ? c : T->claude, true);
            continue;
        }
        char num[5];
        snprintf(num, sizeof(num), "%d.", r + 1);
        text(MONO, x + 2 * MONO.cw, oy, num, cur ? c : T->dim);
        int16_t lx = x + (r + 1 >= 10 ? 6 : 5) * MONO.cw;
        int lcells = cells - (lx - x) / MONO.cw;
        if (multi) {
            bool on = sc.picked[r];
            text(MONO, lx, oy, "[ ]", on ? c : T->dim);
            if (on) gCheck(lx + MONO.cw, oy, c);
            lx += 4 * MONO.cw;
            lcells -= 4;
        }
        fit(MONO, lx, oy, lcells, sc.items[r], lc);
    }
    if (top > 0) gTri(bx + bw - 10, optY + 2, true, T->dim);
    if (top + optRows < total) gTri(bx + bw - 10, optY + optRows * MONO.line - 7, false, T->dim);

    char hint[64];
    const char *esc = escHint(sc);
    if (sc.cursor < 0) snprintf(hint, sizeof(hint), "4/8 scroll  7 back to the options%s", esc);
    else if (multi) snprintf(hint, sizeof(hint), "1-3 tick  4/8 move  7 tick/submit%s", esc);
    else if (sc.maxScroll > 0 && sc.cursor == 0) snprintf(hint, sizeof(hint), "1-3 pick  7 select  4 read all%s", esc);
    else snprintf(hint, sizeof(hint), "1-3 pick  4/8 move  7 select%s", esc);
    hintLine(m, hint);
}

// Your turn (Claude finished, Send to Claude): the transcript, then the
// choices as numbered options with the ❯ cursor, like every other dialog.
void drawPromptScreen(Model &m) {
    ScreenModel &sc = m.screen;
    // header: ✻ Claude finished  ........  money-mind 4:59
    int16_t right = 320 - X0;
    int16_t iw = dialogInfo(m, right, 6);
    gStar(X0, 3, T->claude, 4);
    fit(MONO, X0 + 2 * MONO.cw, 3, (right - iw - X0) / MONO.cw - 3, sc.title, T->text, true);
    rule(22, T->faint);

    int rows = sc.nItems < 3 ? sc.nItems : 3;
    int16_t listY = 170 - SMALL.line - 6 - rows * MONO.line;
    // Claude's message, from its end; 4 on the first option scrolls back through it
    int16_t tTop = 24, tBottom = listY - 4;
    int vis = (tBottom - tTop) / SMALL.line;
    int total = transcriptRows(m.status);
    sc.maxScroll = (int16_t)(total > vis ? total - vis : 0);
    if (sc.scroll > sc.maxScroll) sc.scroll = sc.maxScroll;
    if (sc.scroll < 0) sc.scroll = 0;
    bool focus = sc.cursor < 0;
    drawTranscript(m.status, tTop, tBottom, focus ? sc.maxScroll - sc.scroll : 0);
    if (focus && sc.maxScroll > 0) {   // a scrollbar while reading
        int track = vis * SMALL.line, thumb = track * vis / total;
        int pos = (track - thumb) * sc.scroll / sc.maxScroll;
        cv->fillRect(320 - X0 - 2, tTop + pos, 2, thumb < 6 ? 6 : thumb, T->claude);
    }
    rule(listY - 3, T->faint);

    int top = windowTop(sc, sc.nItems, rows);
    for (int r = top; r < sc.nItems && r < top + rows; r++) {
        int16_t y = listY + (r - top) * MONO.line;
        bool on = r == sc.cursor;
        if (on) gChevron(X0, y, T->claude);
        char num[5];
        snprintf(num, sizeof(num), "%d.", r + 1);
        text(MONO, X0 + 2 * MONO.cw, y, num, on ? T->claude : T->dim);
        int16_t end = fit(MONO, X0 + 5 * MONO.cw, y, 18, sc.items[r], on ? T->claude : T->text);
        int16_t nx = end + 2 * SMALL.cw;
        if (sc.notes[r][0] && nx < 320 - X0 - 4 * SMALL.cw)
            fit(SMALL, nx, y + 3, (320 - X0 - nx) / SMALL.cw, sc.notes[r], T->dim);
    }
    if (top > 0) gTri(320 - X0 - 6, listY + 2, true, T->dim);
    if (top + rows < sc.nItems) gTri(320 - X0 - 6, listY + rows * MONO.line - 7, false, T->dim);
    const char *esc = escHint(sc);
    char hint[64];
    if (focus) snprintf(hint, sizeof(hint), "4/8 scroll  7 back to the options%s", esc);
    else if (sc.maxScroll > 0 && sc.cursor == 0) snprintf(hint, sizeof(hint), "1-3 pick  7 select  4 read back%s", esc);
    else snprintf(hint, sizeof(hint), "1-3 pick  4/8 move  7 select%s", esc);
    hintLine(m, hint);
}

void drawTest(const Model &m) {
    gStar(X0, 3, T->claude, 4);
    text(MONO, X0 + 2 * MONO.cw, 3, "Key test", T->text, true);
    text(SMALL, 320 - X0 - 9 * SMALL.cw, 6, m.id, T->dim);
    rule(22, T->faint);
    for (uint8_t k = 1; k <= 8; k++) {
        bool on = m.testKeys & (1u << k);
        int16_t col = (k - 1) % 4, row = (k - 1) / 4;
        int16_t x = X0 + col * 78, y = 30 + row * 40;
        if (on) cv->fillRoundRect(x, y, 74, 34, 6, T->claude);
        else box(x, y, 74, 34, T->faint);
        char n[2] = {(char)('0' + k), 0};
        drawLine(MONO, x, y + 9, 74, n, 1, on ? T->bg : T->text, 'C', true);
    }
    char line[64];
    snprintf(line, sizeof(line), "encoder %ld%s", (long)m.testEncoder, (m.testKeys & 1) ? "  (pressed)" : "");
    gElbow(X0, 116, T->dim);
    text(MONO, X0 + 2 * MONO.cw, 116, line, (m.testKeys & 1) ? T->claude : T->dim);
    hintLine(m, "hold 8 to leave");
}

void drawOta(const Model &m) {
    int16_t y = 50;
    gStar(X0 + 10, y, T->claude, (millis() / 120) & 7);
    char line[48];
    snprintf(line, sizeof(line), "Updating firmware... %d%%", m.otaPct);
    text(MONO, X0 + 10 + 2 * MONO.cw, y, line, T->claude);
    int16_t w = 320 - 2 * X0 - 20;
    cv->fillRect(X0 + 10, y + 28, w, 6, T->selBg);
    cv->fillRect(X0 + 10, y + 28, w * m.otaPct / 100, 6, T->claude);
    gElbow(X0 + 10, y + 44, T->dim);
    text(MONO, X0 + 10 + 2 * MONO.cw, y + 44, "keep it connected", T->dim);
}

}  // namespace

void uiBegin() {
    pinMode(PIN_LCD_POWER, OUTPUT);
    digitalWrite(PIN_LCD_POWER, HIGH);
    ledcAttach(PIN_LCD_BL, 2000, 8);
    ledcWrite(PIN_LCD_BL, 0);
    cv = new Arduino_Canvas(SCREEN_W, SCREEN_H, panel);
    ready = cv->begin();
    panel->setRotation(1);   // landscape; the canvas is already 320x170
    if (!ready) return;
    cv->setUTF8Print(true);
    cv->setTextWrap(false);
    cv->fillScreen(DARK.bg);
    cv->flush();
}

void uiBrightness(uint8_t pct) {
    if (pct > 100) pct = 100;
    ledcWrite(PIN_LCD_BL, (uint32_t)pct * 255 / 100);
}

bool uiAnimating(const Model &m) {
    if (m.mode == Mode::Boot || m.mode == Mode::Waiting || m.mode == Mode::Ota) return true;
    if (m.mode == Mode::Sessions) {   // the elapsed timers
        for (uint8_t i = 0; i < m.status.n; i++) if (busy(m.status.s[i].state)) return true;
        return false;
    }
    if (m.mode != Mode::Status || m.status.n == 0) return false;
    const SessionInfo &s = m.status.s[m.view < m.status.n ? m.view : 0];
    return busy(s.state);
}

void uiRender(Model &m) {
    if (!ready) return;
    T = m.dark ? &DARK : &LIGHT;
    cv->fillScreen(T->bg);
    switch (m.mode) {
        case Mode::Boot: drawBoot(); break;
        case Mode::Waiting: drawWaiting(m); break;
        case Mode::Status: drawStatus(m); break;
        case Mode::Sessions: drawSessions(m); break;
        case Mode::Screen:
            if (m.screen.tpl == Tpl::Prompt) drawPromptScreen(m);
            else drawDialog(m);
            break;
        case Mode::Test: drawTest(m); break;
        case Mode::Ota: drawOta(m); break;
    }
    cv->flush();
}
