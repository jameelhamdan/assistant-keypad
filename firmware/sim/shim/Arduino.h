// Just enough Arduino for the simulator: the firmware's own sources compile unchanged on top of this.
#pragma once
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "Print.h"
#include "pgmspace.h"

#define IRAM_ATTR
#define HIGH 1
#define LOW 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define CHANGE 3

extern uint32_t sim_now;   // simulated time, ms
inline uint32_t millis() { return sim_now; }
inline void delay(uint32_t ms) { sim_now += ms; }
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline int digitalRead(int) { return 1; }
inline void delayMicroseconds(uint32_t) {}
inline int analogRead(int) { return 2300; }
inline int analogReadMilliVolts(int) { return 2000; }   // battery 4.0 V on the 1:2 divider
inline long map(long x, long a, long b, long c, long d) { return (x - a) * (d - c) / (b - a) + c; }
template <class T> T min(T a, T b) { return a < b ? a : b; }
template <class T> T max(T a, T b) { return a > b ? a : b; }
inline size_t strlcpy(char *d, const char *s, size_t n) {
    size_t l = strlen(s);
    if (n) { size_t c = l >= n ? n - 1 : l; memcpy(d, s, c); d[c] = 0; }
    return l;
}

struct EspClass {
    uint64_t getEfuseMac() { return 0xa1b2c3d4e5f6ULL; }
    void restart() { fprintf(stderr, "[sim] ESP.restart()\n"); }
};
extern EspClass ESP;

struct SerialClass {
    void flush() {}
};
extern SerialClass Serial;
