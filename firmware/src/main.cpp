// Keypad firmware: a hardware keypad for Claude Code.
// LILYGO T-Display S3 + 2x4 keys (1-4 top, 5-8 bottom) + rotary encoder.
#include <Arduino.h>
#include <esp_task_wdt.h>

#include "app.h"
#include "config.h"
#include "crypto.h"
#include "input.h"
#include "model.h"
#include "ui.h"

namespace {

bool watchdog = false;
uint32_t lastFrame = 0;
uint8_t appliedBrightness = 255;

void startWatchdog() {
    esp_task_wdt_config_t cfg = {};
    cfg.timeout_ms = 8000;
    cfg.trigger_panic = true;
    esp_err_t err = esp_task_wdt_init(&cfg);
    if (err == ESP_ERR_INVALID_STATE) err = esp_task_wdt_reconfigure(&cfg);
    watchdog = err == ESP_OK && esp_task_wdt_add(nullptr) == ESP_OK;
}

}  // namespace

void setup() {
    // The host writes whole JSON lines in well under a millisecond; the default
    // 256-byte RX ring would truncate them before loop() runs.
    Serial.setRxBufferSize(8192);
    Serial.setTxBufferSize(2048);
    Serial.begin(115200);
    Serial.setTxTimeoutMs(30);   // never block when no host is reading

    uiBegin();
    inputBegin();
    appBegin();
    if (!cryptoSelfTest()) appToast("Crypto self-test failed", Tone::Danger, 10000);
    startWatchdog();
}

void loop() {
    bool changed = appLoop();
    uint32_t now = millis();
    // Redraw on change; animate the spinner at ~8 fps, otherwise tick once a
    // second for timers and overlays.
    if (changed || now - lastFrame > (uiAnimating(model) ? 120u : 1000u)) {
        uiRender(model);
        lastFrame = now;
    }
    if (model.brightness != appliedBrightness) {
        appliedBrightness = model.brightness;
        uiBrightness(appliedBrightness);
    }
    if (watchdog) esp_task_wdt_reset();
    delay(1);
}
