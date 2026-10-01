// Persistent settings in NVS: Wi-Fi, pairing, name and display preferences.
#pragma once

#include <stdint.h>

struct Stored {
    char ssid[33];
    char pass[65];
    char host[24];       // paired host id
    uint8_t key[32];     // pairing key
    bool paired;
    char name[25];
    bool dark;
    uint8_t brightness;  // 5..100
};

void storeLoad(Stored &s);
void storeSave(const Stored &s);
