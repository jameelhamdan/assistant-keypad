#include "power.h"

#include <Arduino.h>
#include <HWCDC.h>

#include "config.h"

namespace {
int batteryMv() { return (int)analogReadMilliVolts(PIN_BATTERY) * 2; }   // 1:2 divider
bool noBattery(int mv) { return mv > 4300 || mv < 2800; }
}  // namespace

int8_t batteryPercent() {
    int mv = batteryMv();
    if (noBattery(mv)) return -1;
    int pct = (mv - 3300) * 100 / (4150 - 3300);
    return (int8_t)(pct < 0 ? 0 : pct > 100 ? 100 : pct);
}

bool usbPowered() {
    int mv = batteryMv();
    return HWCDC::isPlugged() || noBattery(mv) || mv >= 4180;
}
