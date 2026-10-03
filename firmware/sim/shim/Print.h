#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

class __FlashStringHelper;
class String {
public:
    const char *c_str() const { return ""; }
    unsigned length() const { return 0; }
};

class Print {
public:
    virtual ~Print() {}
    virtual size_t write(uint8_t) = 0;
    virtual size_t write(const uint8_t *b, size_t n) { size_t k = 0; while (n--) k += write(*b++); return k; }
    size_t write(const char *s) { return write((const uint8_t *)s, strlen(s)); }
    size_t print(const char *s) { return write(s); }
    size_t print(const String &) { return 0; }
    size_t print(const __FlashStringHelper *) { return 0; }
    size_t print(char c) { return write((uint8_t)c); }
    size_t print(int v) { char b[16]; snprintf(b, sizeof b, "%d", v); return write(b); }
    size_t print(unsigned v) { char b[16]; snprintf(b, sizeof b, "%u", v); return write(b); }
    size_t println() { return write((uint8_t)'\n'); }
    size_t println(const char *s) { return write(s) + println(); }
    int printf(const char *f, ...) { char b[512]; va_list a; va_start(a, f); int n = vsnprintf(b, sizeof b, f, a); va_end(a); write(b); return n; }
};
