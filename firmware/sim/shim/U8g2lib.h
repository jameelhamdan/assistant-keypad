// The simulator only needs U8g2's font data (plain C arrays), not its display drivers.
#pragma once
#include <stdint.h>
#define U8G2_FONT_SECTION(name)
#define U8G2_WITH_UNICODE   // as the real u8g2.h: lets Arduino_GFX look up glyphs above U+00FF
extern "C" {
extern const uint8_t u8g2_font_spleen8x16_mf[];
extern const uint8_t u8g2_font_spleen6x12_mf[];
extern const uint8_t u8g2_font_unifont_t_arabic[];
}
