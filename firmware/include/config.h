// Keypad firmware configuration: LILYGO T-Display S3 + 2x4 key matrix + rotary encoder.
// The pin map is the one confirmed on the real board by the original project.
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifndef KEYPAD_FW_VERSION
#define KEYPAD_FW_VERSION "dev"
#endif

constexpr int PROTOCOL_VERSION = 2;
constexpr uint16_t TCP_PORT = 7470;

#if !defined(NATIVE_TEST) && (!defined(ARDUINO_USB_CDC_ON_BOOT) || ARDUINO_USB_CDC_ON_BOOT == 0)
#error "USB CDC on boot must be enabled: GPIO43/44 are key-matrix columns, not UART0."
#endif

// ---- Keys --------------------------------------------------------------------
// Labelled like the keypad:   top row    1 2 3 4
//                             bottom row 5 6 7 8
constexpr int8_t ROW_PINS[] = {17, 16};          // outputs, driven LOW one at a time
constexpr int8_t COL_PINS[] = {43, 44, 18, 21};  // INPUT_PULLUP
constexpr uint8_t NUM_ROWS = 2, NUM_COLS = 4;
constexpr uint8_t KEY_MAP[NUM_ROWS][NUM_COLS] = {{1, 2, 3, 4}, {5, 6, 7, 8}};
constexpr uint8_t NUM_KEYS = 8;
constexpr uint8_t KEY_ENC = 0;                   // encoder push switch

constexpr int ENC_SW_PIN = 1;
constexpr int ENC_DT_PIN = 2;
constexpr int ENC_CLK_PIN = 3;

// ---- Display (fixed by the board) -----------------------------------------------
constexpr int PIN_LCD_POWER = 15, PIN_LCD_BL = 38;
constexpr int PIN_LCD_RST = 5, PIN_LCD_CS = 6, PIN_LCD_DC = 7, PIN_LCD_WR = 8, PIN_LCD_RD = 9;
constexpr int PIN_LCD_D[8] = {39, 40, 41, 42, 45, 46, 47, 48};
constexpr int PIN_BATTERY = 4;                   // ADC, 1:2 divider
constexpr int16_t SCREEN_W = 320, SCREEN_H = 170;

// ---- Timing (ms) ------------------------------------------------------------------
constexpr uint32_t DEBOUNCE_MS = 30;
constexpr uint32_t LONG_PRESS_MS = 700;
constexpr uint32_t STALE_PRESS_MS = 150;   // a press must start this long after its screen appeared
constexpr uint32_t TEST_HOLD_MS = 1500;    // hold 1 while waiting for the PC: key test
constexpr uint32_t TEST_EXIT_MS = 2000;    // hold 8 in key test: leave
constexpr uint32_t HELLO_EVERY_MS = 3000;
constexpr uint32_t HOST_TIMEOUT_MS = 6000;
constexpr uint32_t HANDSHAKE_MS = 5000;
constexpr uint32_t SENT_MS = 900;

// ---- Limits -----------------------------------------------------------------------
constexpr size_t MSG_MAX = 4096;          // one JSON message from the host
constexpr uint8_t MAX_SESSIONS = 8;
constexpr uint8_t MAX_ITEMS = 32;
constexpr uint8_t ITEMS_PER_PAGE = 8;
