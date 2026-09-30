// Renderer in Claude Code's visual language: a monospace terminal grid, the
// Claude Code theme colors, and its glyphs (✻ ⏺ ⎿ ❯, rounded dialog boxes,
// numbered option lists, the "> " prompt box). The whole frame is drawn into
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
int16_t fit(const Font &f, int16_t x, int16_t y, int cells, const char *s, uint16_t color, bool bold = false,
            char align = 'L') {
    static char buf[256];
    size_t len = strlen(s);
    int w = cells * f.cw;
    if (measureWith(f, s, len) <= w) return drawLine(f, x, y, w, s, len, color, align, bold);
    while (len > 0 && measureWith(f, s, len) + f.cw > w) {
        len--;
        while (len > 0 && ((unsigned char)s[len] & 0xC0) == 0x80) len--;
    }
    // Spleen has no U+2026: draw the ellipsis as three dots in one cell.
    snprintf(buf, sizeof(buf), "%.*s", (int)len, s);
    int16_t end = drawLine(f, x, y, w, buf, strlen(buf), color, align, bold);
    for (int i = 0; i < 3; i++) cv->fillRect(end + 1 + i * 2, y + f.ascent - 1, 1, 1, color);
    return end + f.cw;
}

TextLine lines[48];
// Wrapped paragraph in a box of `cells` x `rows`; returns rows drawn, *total all rows.
int wrap(const Font &f, int16_t x, int16_t y, int cells, int rows, const char *s, uint16_t color, int skip = 0,
         int *total = nullptr) {
    uint8_t n = textWrap(s, cells * f.cw, measureCb, (void *)&f, lines, 48);
    if (total) *total = n;
    int drawn = 0;
    for (int i = skip; i < n && drawn < rows; i++, drawn++) {
        drawLine(f, x, y + drawn * f.line, cells * f.cw, s + lines[i].start, lines[i].len, color);
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
        char s[48];
        snprintf(s, sizeof(s), "%s", m.sent);
        text(SMALL, X0 + 12, y, s, T->success);
        return;
    }
    if (m.toastUntil && (int32_t)(m.toastUntil - now) > 0) {
        gElbow(X0, y - 3, T->dim);
        fit(SMALL, X0 + 12, y, 50, m.toast, tone(m.toastTone));
        return;
    }
    if (hint) fit(SMALL, X0 + 2, y, 51, hint, T->dim);
}

// Numbered option list in the Claude Code dialog style:  ❯ 1. Yes / 2. No
void optionRow(int16_t x, int16_t y, int cells, uint8_t key, const char *label, uint16_t color, bool first) {
    if (first) gChevron(x, y, color);
    char num[4];
    snprintf(num, sizeof(num), "%u.", key);
    text(MONO, x + 2 * MONO.cw, y, num, first ? color : T->dim);
    fit(MONO, x + 5 * MONO.cw, y, cells - 5, label, first ? color : T->text);
}

// ---- screens ------------------------------------------------------------------------------------
void drawBoot(const Model &m) {
    box(X0, 36, 320 - 2 * X0, 70, T->claude);
    gStar(X0 + 14, 52, T->claude, (millis() / 120) & 7);
    text(MONO, X0 + 30, 52, "Welcome to", T->text);
    text(MONO, X0 + 30 + 11 * MONO.cw, 52, "Keypad", T->text, true);
    text(MONO, X0 + 30, 72, "for Claude Code", T->dim);
    char v[40];
    snprintf(v, sizeof(v), "v%s", KEYPAD_FW_VERSION);
    drawLine(SMALL, 0, 120, 320, v, strlen(v), T->dim, 'C');
    (void)m;
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

void drawStatus(const Model &m) {
    const StatusModel &st = m.status;
    // top line: ✻ project ........ 2/3  usb
    int16_t right = indicators(m, 3);
    const SessionInfo *s = st.n ? &st.s[m.view < st.n ? m.view : 0] : nullptr;
    gStar(X0, 3, T->claude, 4);
    char sess[16] = "";
    if (st.n > 1) snprintf(sess, sizeof(sess), "%d/%d%s", m.view + 1, st.n, st.pinned ? "*" : "");
    int16_t sessW = strlen(sess) * SMALL.cw;
    if (sess[0]) text(SMALL, right - sessW, 6, sess, T->dim);
    int cells = (right - sessW - 8 - (X0 + 2 * MONO.cw)) / MONO.cw;
    fit(MONO, X0 + 2 * MONO.cw, 3, cells, s ? (s->project[0] ? s->project : s->id) : (m.host[0] ? m.host : m.name),
        T->text, true);
    rule(22, T->faint);

    int16_t y = 30;
    if (!s) {
        gDot(X0, y, T->dim);
        text(MONO, X0 + 2 * MONO.cw, y, "No Claude Code sessions", T->text);
        gElbow(X0 + 2 * MONO.cw, y + 18, T->dim);
        text(MONO, X0 + 4 * MONO.cw, y + 18, "start claude in a project", T->dim);
    } else {
        uint16_t c = stateColor(s->state);
        gDot(X0, y, c);
        fit(MONO, X0 + 2 * MONO.cw, y, COLS - 2, s->title[0] ? s->title : s->state, T->text, true);
        y += MONO.line + 2;
        if (s->detail[0]) {
            gElbow(X0 + 2 * MONO.cw, y, T->dim);
            y += MONO.line * wrap(MONO, X0 + 5 * MONO.cw, y, COLS - 5, 3, s->detail, T->dim);
        }
        if (busy(s->state)) {   // ✻ Brewing… (1m 12s)
            char el[16], line[64];
            fmtElapsed(el, sizeof(el), millis() - s->startedAt);
            const char *verb = !strcmp(s->state, "continuing") ? "Continuing" : gerund(s->id);
            snprintf(line, sizeof(line), "%s... (%s)", verb, el);
            int16_t sy = 96;
            gStar(X0, sy, T->claude, (millis() / 120) & 7);
            text(MONO, X0 + 2 * MONO.cw, sy, line, T->claude);
        } else if (st.queue) {
            char q[32];
            snprintf(q, sizeof(q), "%u waiting for you", st.queue);
            gDot(X0, 96, T->permission);
            text(MONO, X0 + 2 * MONO.cw, 96, q, T->permission);
        }
    }

    // the prompt box:  > _
    int16_t by = 118;
    box(X0, by, 320 - 2 * X0, 30, st.paused ? T->warning : T->faint);
    text(MONO, X0 + 8, by + 7, ">", T->dim);
    const char *ph = st.paused ? "paused - answer on the PC" : nullptr;
    char placeholder[64] = "";
    if (!ph) {
        for (uint8_t k = 1; k <= 8; k++) {
            if (st.keys[k].set) {
                snprintf(placeholder, sizeof(placeholder), "press %u for %s", k, st.keys[k].label);
                break;
            }
        }
        ph = placeholder;
    }
    fit(MONO, X0 + 8 + 2 * MONO.cw, by + 7, COLS - 4, ph, st.paused ? T->warning : T->faint);
    hintLine(m, st.n > 1 ? "turn: switch session   click: follow latest" : nullptr);
}

// Top line of a dialog: bold title, dim project on the right, countdown.
void dialogTitle(const ScreenModel &sc, int16_t x, int16_t y, int cells, uint16_t c) {
    char right[48] = "";
    if (sc.expiresAt) {
        int32_t left = (int32_t)(sc.expiresAt - millis()) / 1000;
        if (left < 0) left = 0;
        snprintf(right, sizeof(right), "%s %ld:%02ld", sc.project, (long)(left / 60), (long)(left % 60));
    } else {
        snprintf(right, sizeof(right), "%s", sc.project);
    }
    int rc = utf8Length(right);
    if (rc > 18) rc = 18;
    fit(SMALL, x + (cells - 0) * MONO.cw - rc * SMALL.cw, y + 3, rc, right, T->dim);
    fit(MONO, x, y, cells - (rc * SMALL.cw) / MONO.cw - 1, sc.title, c, true);
}

void drawPrompt(Model &m) {
    ScreenModel &sc = m.screen;
    uint16_t c = tone(sc.tone);
    int16_t bx = X0, bw = 320 - 2 * X0, bh = 170 - SMALL.line - 8;
    box(bx, 2, bw, bh, c);
    int16_t x = bx + 10, cells = (bw - 20) / MONO.cw, y = 8;
    dialogTitle(sc, x, y, cells, c);
    y += MONO.line + 4;

    uint8_t nOpt = 0;
    for (uint8_t k = 1; k <= 8; k++) nOpt += sc.keys[k].set;
    int optRows = nOpt > 4 ? (nOpt + 1) / 2 : nOpt;
    int16_t optY = 2 + bh - 6 - optRows * MONO.line;
    int bodyRows = (optY - 4 - y) / MONO.line;
    int total = 0;
    int shown = wrap(MONO, x + 2 * MONO.cw, y, cells - 3, bodyRows, sc.body, T->text, sc.scroll, &total);
    sc.maxScroll = (int16_t)(total > shown + sc.scroll ? total - shown : sc.scroll);
    if (total > shown) {   // scroll position, like a terminal scrollbar
        int track = bodyRows * MONO.line, thumb = track * shown / total;
        int pos = (track - thumb) * sc.scroll / (total - shown);
        cv->fillRect(bx + bw - 6, y + pos, 2, thumb < 6 ? 6 : thumb, T->dim);
    }

    // ❯ 1. Allow / 4. Deny / 8. PC  (two columns when there are many)
    uint8_t i = 0;
    int colCells = nOpt > 4 ? cells / 2 : cells;
    for (uint8_t k = 1; k <= 8; k++) {
        if (!sc.keys[k].set) continue;
        int col = nOpt > 4 ? i / optRows : 0, row = nOpt > 4 ? i % optRows : i;
        optionRow(x + col * colCells * MONO.cw, optY + row * MONO.line, colCells, k, sc.keys[k].label,
                  i == 0 ? tone(sc.keys[k].tone == Tone::Dim ? Tone::Accent : sc.keys[k].tone) : T->text, i == 0);
        i++;
    }
    hintLine(m, sc.click[0] ? (!strcmp(sc.click, "pc") ? "click: answer on the PC   turn: scroll" : "click: back")
                            : nullptr);
}

// AskUserQuestion-style list: header chip, question, numbered options.
void drawList(Model &m) {
    ScreenModel &sc = m.screen;
    bool multi = sc.tpl == Tpl::Multi;
    uint16_t c = T->permission;
    int16_t x = X0 + 2, y = 3;
    int pages = (sc.nItems + ITEMS_PER_PAGE - 1) / ITEMS_PER_PAGE;

    // chip:  ☐ Database                         money-mind 4:59
    int16_t cw = (utf8Length(sc.title) + 2) * MONO.cw + 8;
    if (cw > 200) cw = 200;
    cv->fillRoundRect(x, y, cw, MONO.line + 2, 3, T->selBg);
    fit(MONO, x + 4, y + 1, (cw - 8) / MONO.cw, sc.title, c, true);
    char right[48];
    int32_t left = sc.expiresAt ? (int32_t)(sc.expiresAt - millis()) / 1000 : -1;
    if (pages > 1) snprintf(right, sizeof(right), "(%d/%d)", sc.page + 1, pages);
    else if (left >= 0) snprintf(right, sizeof(right), "%s %ld:%02ld", sc.project, (long)(left / 60), (long)(left % 60));
    else snprintf(right, sizeof(right), "%s", sc.project);
    int rc = utf8Length(right);
    if (rc > 16) rc = 16;
    fit(SMALL, 320 - X0 - rc * SMALL.cw, y + 4, rc, right, T->dim);
    y += MONO.line + 6;

    // question (up to 2 rows)
    if (sc.body[0] && strcmp(sc.body, sc.title) != 0) y += MONO.line * wrap(MONO, x, y, COLS - 1, 2, sc.body, T->text);
    y += 3;

    // options: one column up to 4, two columns (1-4 | 5-8) beyond
    uint8_t first = sc.page * ITEMS_PER_PAGE;
    uint8_t onPage = sc.nItems - first < ITEMS_PER_PAGE ? sc.nItems - first : ITEMS_PER_PAGE;
    uint8_t slots = multi ? onPage + 1 : onPage;   // + "8. Submit"
    bool twoCols = slots > 4;
    int colCells = twoCols ? COLS / 2 : COLS;
    int rows = (170 - SMALL.line - 4 - y) / MONO.line;
    int16_t rowH = rows >= 4 ? MONO.line : (170 - SMALL.line - 4 - y) / 4;
    for (uint8_t k = 1; k <= 8; k++) {
        int idx = first + k - 1;
        bool submit = multi && k == 8;
        if (!submit && (k > onPage)) continue;
        int col = twoCols ? (k - 1) / 4 : 0, row = twoCols ? (k - 1) % 4 : (submit ? onPage : k - 1);
        int16_t ox = x + col * colCells * MONO.cw, oy = y + row * rowH;
        char num[4];
        snprintf(num, sizeof(num), "%u.", k);
        if (submit) {
            gChevron(ox, oy, T->claude);
            text(MONO, ox + 2 * MONO.cw, oy, num, T->claude);
            text(MONO, ox + 5 * MONO.cw, oy, "Submit", T->claude, true);
            continue;
        }
        text(MONO, ox + 2 * MONO.cw, oy, num, T->dim);
        int16_t lx = ox + 5 * MONO.cw;
        if (multi) {
            bool on = sc.picked[idx];
            text(MONO, lx, oy, "[ ]", on ? c : T->dim);
            if (on) gCheck(lx + MONO.cw, oy, c);
            lx += 4 * MONO.cw;
            fit(MONO, lx, oy, colCells - 9, sc.items[idx], on ? c : T->text);
        } else {
            fit(MONO, lx, oy, colCells - 5, sc.items[idx], T->text);
        }
    }
    hintLine(m, pages > 1 ? "turn: more options   click: answer on the PC" : "click: answer on the PC");
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
    if (m.mode != Mode::Status || m.status.n == 0) return false;
    const SessionInfo &s = m.status.s[m.view < m.status.n ? m.view : 0];
    return busy(s.state);
}

void uiRender(Model &m) {
    if (!ready) return;
    T = m.dark ? &DARK : &LIGHT;
    cv->fillScreen(T->bg);
    switch (m.mode) {
        case Mode::Boot: drawBoot(m); break;
        case Mode::Waiting: drawWaiting(m); break;
        case Mode::Status: drawStatus(m); break;
        case Mode::Screen:
            if (m.screen.tpl == Tpl::Prompt) drawPrompt(m);
            else drawList(m);
            break;
        case Mode::Test: drawTest(m); break;
        case Mode::Ota: drawOta(m); break;
    }
    cv->flush();
}
