// Replaces Arduino_GFX_Library.h: the real drawing code (Arduino_GFX, Arduino_Canvas) with a panel
// that keeps the frame in memory instead of an ST7789 on an 8-bit bus.
#pragma once
#include <Arduino.h>
#include "Arduino_DataBus.h"
#include "Arduino_GFX.h"
#include "canvas/Arduino_Canvas.h"

extern uint16_t sim_frame[320 * 170];

class SimBus : public Arduino_DataBus {
public:
    template <class... A> SimBus(A...) {}
    bool begin(int32_t = 0, int8_t = 0) override { return true; }
    void beginWrite() override {}
    void endWrite() override {}
    void writeCommand(uint8_t) override {}
    void writeCommand16(uint16_t) override {}
    void writeCommandBytes(uint8_t *, uint32_t) override {}
    void write(uint8_t) override {}
    void write16(uint16_t) override {}
    void writeRepeat(uint16_t, uint32_t) override {}
    void writePixels(uint16_t *, uint32_t) override {}
    void writeBytes(uint8_t *, uint32_t) override {}
    void writePattern(uint8_t *, uint8_t, uint32_t) override {}
};

class SimPanel : public Arduino_GFX {
public:
    template <class... A> SimPanel(A...) : Arduino_GFX(320, 170) {}
    bool begin(int32_t = 0) override { return true; }
    void writePixelPreclipped(int16_t x, int16_t y, uint16_t c) override { sim_frame[y * 320 + x] = c; }
    void draw16bitRGBBitmap(int16_t x, int16_t y, uint16_t *b, int16_t w, int16_t h) override {
        for (int j = 0; j < h; j++)
            for (int i = 0; i < w; i++)
                if (x + i >= 0 && x + i < 320 && y + j >= 0 && y + j < 170) sim_frame[(y + j) * 320 + x + i] = b[j * w + i];
    }
};

#define Arduino_ESP32LCD8 SimBus
#define Arduino_ST7789 SimPanel
inline void ledcAttach(int, int, int) {}
inline void ledcWrite(int, int) {}
