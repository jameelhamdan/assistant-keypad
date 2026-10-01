// Firmware update streamed over the protocol (USB or Wi-Fi) into the idle OTA slot.
#pragma once

#include <stddef.h>
#include <stdint.h>

bool otaBegin(size_t size, const char *md5, const char **err);
bool otaWrite(size_t offset, const char *base64, const char **err);
bool otaFinish(const char **err);   // on success the caller restarts
bool otaRunning();
int otaPercent();
void otaAbort();
