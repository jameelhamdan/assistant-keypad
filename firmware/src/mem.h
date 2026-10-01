// Large buffers live in the 8 MB PSRAM; internal RAM stays free for Wi-Fi and the display.
#pragma once

#include <esp_heap_caps.h>
#include <stdlib.h>
#include <string.h>

inline void *bigAlloc(size_t n) {
    void *p = heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!p) p = malloc(n);
    if (p) memset(p, 0, n);
    return p;
}
