// Pure text helpers (no Arduino): glyph sanitising, UTF-8 length and word wrapping.
#pragma once

#include <stddef.h>
#include <stdint.h>

// Replace characters our fonts cannot draw, in place: typographic
// punctuation becomes ASCII, Latin-1 is kept, anything else becomes '?'. Control characters other than '\n' and the style markers
// \x01 (bold), \x02 (code) and \x03 (dim) become spaces.
void textSanitize(char *s);

// Number of code points in a UTF-8 string (cells, on a monospace font).
uint16_t utf8Length(const char *s);

struct TextLine {
    uint16_t start;  // byte offset into the source string
    uint16_t len;    // bytes
};

// Pixel width of s[0..len).
typedef int (*TextMeasure)(const char *s, size_t len, void *ctx);

// Word-wrap `s` into lines no wider than maxW. Paragraphs break on '\n';
// words wider than a line are split between characters. Returns the number
// of lines (at most maxLines; the rest is dropped).
uint8_t textWrap(const char *s, int maxW, TextMeasure measure, void *ctx, TextLine *out, uint8_t maxLines);
