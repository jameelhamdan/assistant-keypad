// Screen rendering (320x170 landscape, dark theme).
#pragma once

#include <stdint.h>

#include "model.h"

void uiBegin();
void uiRender(Model &m);   // draws the full frame; records scroll limits back into m
void uiBrightness(uint8_t pct);
bool uiAnimating(const Model &m);   // spinner on screen: redraw often
