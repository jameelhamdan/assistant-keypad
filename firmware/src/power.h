// Power source and battery level.
#pragma once

#include <stdint.h>

// Battery percent, -1 when unknown (no battery, or a reading outside the battery's range).
int8_t batteryPercent();

// True when the keypad runs from a wire: a USB host or charger is connected, there is no
// battery, or the battery is being charged (it then sits at about 4.2 V). The screen stays
// at full brightness then; on battery it dims after a while.
bool usbPowered();
