// Transports to the host: USB CDC (JSON lines) and Wi-Fi (TCP server,
// mDNS, paired handshake, AES-GCM frames). Both deliver whole JSON messages.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "store.h"

enum class Src : uint8_t { Usb = 0, Net = 1 };

typedef void (*MessageHandler)(char *json, size_t len, Src src);

void linkBegin(MessageHandler h, const char *deviceId);
void linkPoll();
bool linkSend(Src src, const char *json, size_t len);

// (Re)apply Wi-Fi credentials and pairing after they changed.
void linkConfigure(const Stored &st);
bool linkNetAuthed();        // an authenticated host session exists
void linkNetDrop();          // close Wi-Fi sessions (unpair)
