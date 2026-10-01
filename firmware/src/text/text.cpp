#include "text.h"

#include <string.h>

#include "arabic.h"

namespace {

// Decode one code point at p; returns bytes consumed (>= 1).
uint8_t decode(const unsigned char *p, uint32_t &cp) {
    if (p[0] < 0x80) { cp = p[0]; return 1; }
    uint8_t n = (p[0] & 0xE0) == 0xC0 ? 2 : (p[0] & 0xF0) == 0xE0 ? 3 : (p[0] & 0xF8) == 0xF0 ? 4 : 0;
    if (n == 0) { cp = '?'; return 1; }
    cp = p[0] & (0x7F >> n);
    for (uint8_t i = 1; i < n; i++) {
        if ((p[i] & 0xC0) != 0x80) { cp = '?'; return i; }
        cp = (cp << 6) | (p[i] & 0x3F);
    }
    return n;
}

const char *replacement(uint32_t cp) {
    switch (cp) {
        case 0x2018: case 0x2019: case 0x201A: case 0x2032: return "'";
        case 0x201C: case 0x201D: case 0x201E: case 0x2033: return "\"";
        case 0x2010: case 0x2011: case 0x2012: case 0x2013: case 0x2014: case 0x2212: return "-";
        case 0x2026: return "...";
        case 0x2022: case 0x00B7: return "\xC2\xB7";   // middle dot (Latin-1)
        case 0x2192: return ">";
        case 0x2190: return "<";
        case 0x00A0: case 0x2009: case 0x200A: case 0x202F: return " ";
        case 0x2713: case 0x2714: return "v";
        case 0x2715: case 0x2716: case 0x2717: return "x";
        default: return nullptr;
    }
}

bool keep(uint32_t cp) {
    return (cp >= 0x20 && cp < 0x7F) || (cp >= 0xA0 && cp <= 0xFF) || cp == '\n' || cp == 0x01 || cp == 0x02 ||
           (cp >= 0x0600 && cp <= 0x06FF) || (cp >= 0xFB50 && cp <= 0xFDFF) || (cp >= 0xFE70 && cp <= 0xFEFF) ||
           cp == 0x200C || cp == 0x200D;
}

}  // namespace

void textSanitize(char *s) {
    // Output never grows beyond input except "..." (3 bytes) replacing a 3-byte
    // ellipsis, so an in-place rewrite is safe.
    unsigned char *r = (unsigned char *)s;
    char *w = s;
    while (*r) {
        uint32_t cp;
        uint8_t n = decode(r, cp);
        // \x01 and \x02 are the transcript's style markers (bold, code): kept
        const char *rep = (cp < 0x20 && cp != '\n' && cp != 0x01 && cp != 0x02) ? " " : replacement(cp);
        if (rep) {
            size_t k = strlen(rep);
            memmove(w, rep, k);
            w += k;
        } else if (keep(cp)) {
            memmove(w, r, n);
            w += n;
        } else {
            *w++ = '?';
        }
        r += n;
    }
    *w = '\0';
}

uint8_t textWrap(const char *s, int maxW, TextMeasure measure, void *ctx, TextLine *out, uint8_t maxLines) {
    uint8_t count = 0;
    size_t total = strlen(s);
    size_t para = 0;
    auto emit = [&](size_t start, size_t len) {
        while (len > 0 && s[start + len - 1] == ' ') len--;   // trailing spaces
        if (count < maxLines) out[count++] = {(uint16_t)start, (uint16_t)len};
    };
    while (para <= total && count < maxLines) {
        size_t end = para;
        while (end < total && s[end] != '\n') end++;
        // wrap s[para..end)
        size_t lineStart = para;
        while (lineStart < end && s[lineStart] == ' ') lineStart++;
        if (lineStart >= end) {
            emit(para, 0);                                      // empty line
        }
        while (lineStart < end && count < maxLines) {
            size_t lastFit = lineStart;   // end of the longest prefix that fits, at a word boundary
            size_t i = lineStart;
            while (i < end) {
                size_t wordEnd = i;
                while (wordEnd < end && s[wordEnd] == ' ') wordEnd++;
                while (wordEnd < end && s[wordEnd] != ' ') wordEnd++;
                if (measure(s + lineStart, wordEnd - lineStart, ctx) <= maxW) {
                    lastFit = wordEnd;
                    i = wordEnd;
                } else {
                    break;
                }
            }
            if (lastFit == lineStart) {
                // a single word wider than the line: split between characters
                size_t j = lineStart;
                while (j < end) {
                    size_t next = j + 1;
                    while (next < end && ((unsigned char)s[next] & 0xC0) == 0x80) next++;
                    if (j > lineStart && measure(s + lineStart, next - lineStart, ctx) > maxW) break;
                    j = next;
                }
                lastFit = j;
            }
            emit(lineStart, lastFit - lineStart);
            lineStart = lastFit;
            while (lineStart < end && s[lineStart] == ' ') lineStart++;
        }
        para = end + 1;
    }
    return count;
}
