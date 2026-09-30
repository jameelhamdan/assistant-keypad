// Keypad application: protocol handling, key logic, link supervision.
#pragma once

#include <stdint.h>

#include "model.h"

void appBegin();
bool appLoop();   // true when the model changed and the screen should be redrawn
void appToast(const char *text, Tone tone, uint32_t ms);
