// Host-side tests: pio test -e native
#include <string.h>
#include <unity.h>

#include "text/arabic.h"
#include "text/text.h"

void setUp(void) {}
void tearDown(void) {}

static int monoMeasure(const char *s, size_t len, void *) {
    int n = 0;
    for (size_t i = 0; i < len; i++) if (((unsigned char)s[i] & 0xC0) != 0x80) n++;
    return n * 6;   // 6 px per character
}

static void lineEq(const char *src, TextLine l, const char *want) {
    char buf[128];
    memcpy(buf, src + l.start, l.len);
    buf[l.len] = 0;
    TEST_ASSERT_EQUAL_STRING(want, buf);
}

void test_wrap_words(void) {
    const char *s = "Run the test suite now please";
    TextLine lines[8];
    uint8_t n = textWrap(s, 6 * 12, monoMeasure, nullptr, lines, 8);   // 12 chars wide
    TEST_ASSERT_EQUAL(3, n);
    lineEq(s, lines[0], "Run the test");
    lineEq(s, lines[1], "suite now");
    lineEq(s, lines[2], "please");
}

void test_wrap_paragraphs_and_long_words(void) {
    const char *s = "go test ./...\nsupercalifragilistic";
    TextLine lines[8];
    uint8_t n = textWrap(s, 6 * 8, monoMeasure, nullptr, lines, 8);
    TEST_ASSERT_EQUAL(5, n);
    lineEq(s, lines[0], "go test");
    lineEq(s, lines[1], "./...");
    lineEq(s, lines[2], "supercal");
    lineEq(s, lines[3], "ifragili");
    lineEq(s, lines[4], "stic");
}

void test_wrap_max_lines(void) {
    TextLine lines[2];
    TEST_ASSERT_EQUAL(2, textWrap("a b c d e f", 6, monoMeasure, nullptr, lines, 2));
}

void test_sanitize(void) {
    char s[64];
    strcpy(s, "It\xE2\x80\x99s \xE2\x80\x9Cok\xE2\x80\x9D \xE2\x80\x94 wait\xE2\x80\xA6 \xF0\x9F\x98\x80");
    textSanitize(s);
    TEST_ASSERT_EQUAL_STRING("It's \"ok\" - wait... ?", s);
    strcpy(s, "caf\xC3\xA9 \xD9\x86\xD8\xB9\xD9\x85\ttab");
    textSanitize(s);
    TEST_ASSERT_EQUAL_STRING("caf\xC3\xA9 \xD9\x86\xD8\xB9\xD9\x85 tab", s);
}

// Cases from the original project's Arabic tests.
void test_sanitize_keeps_style_markers(void) {
    char s[] = "\x01Title\x01 and \x02code\x02\tend";
    textSanitize(s);
    TEST_ASSERT_EQUAL_STRING("\x01Title\x01 and \x02code\x02 end", s);
}

void test_arabic_shaping(void) {
    char out[128];
    // "نعم" (yes): isolated -> initial noon, medial ain, final meem, reversed for display
    arabicToVisual("\xD9\x86\xD8\xB9\xD9\x85", 'R', out, sizeof(out));
    TEST_ASSERT_EQUAL_STRING("\xEF\xBB\xA2\xEF\xBB\x8C\xEF\xBB\xA7", out);  // FEE2 FECC FEE7
    // Lam-Alef ligature: "لا" -> FEFB
    arabicToVisual("\xD9\x84\xD8\xA7", 'R', out, sizeof(out));
    TEST_ASSERT_EQUAL_STRING("\xEF\xBB\xBB", out);
    TEST_ASSERT_EQUAL(1, arabicCellCount("\xD9\x84\xD8\xA7"));
}

void test_bidi_keeps_latin_runs(void) {
    char out[128];
    // "نعم API" in an RTL paragraph: the Latin word stays left-to-right, placed on the left
    arabicToVisual("\xD9\x86\xD8\xB9\xD9\x85 API", 'R', out, sizeof(out));
    TEST_ASSERT_EQUAL_STRING("API \xEF\xBB\xA2\xEF\xBB\x8C\xEF\xBB\xA7", out);
    TEST_ASSERT_EQUAL('R', textBaseDirection("\xD9\x86\xD8\xB9\xD9\x85 API"));
    TEST_ASSERT_EQUAL('L', textBaseDirection("API \xD9\x86\xD8\xB9\xD9\x85"));
}

int main(int, char **) {
    UNITY_BEGIN();
    RUN_TEST(test_wrap_words);
    RUN_TEST(test_wrap_paragraphs_and_long_words);
    RUN_TEST(test_wrap_max_lines);
    RUN_TEST(test_sanitize);
    RUN_TEST(test_sanitize_keeps_style_markers);
    RUN_TEST(test_arabic_shaping);
    RUN_TEST(test_bidi_keeps_latin_runs);
    return UNITY_END();
}
