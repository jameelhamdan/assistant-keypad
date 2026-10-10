// Persistent settings in NVS: Wi-Fi, pairing, name and display preferences.
#pragma once

#include <Arduino.h>   // strlcpy
#include <stdint.h>
#include <string.h>

constexpr uint8_t MAX_HOSTS = 3;   // computers one keypad can be paired with (one is served at a time)

struct HostKey {
    char id[24];         // the computer's host id
    uint8_t key[32];     // its pairing key
};

struct Stored {
    char ssid[33];
    char pass[65];
    HostKey hosts[MAX_HOSTS];   // the first nHosts are in use
    uint8_t nHosts;
    bool paired;         // nHosts > 0
    char name[25];
    uint8_t brightness;  // 5..100
};

void storeLoad(Stored &s);
void storeSave(const Stored &s);

// The slot of a paired computer, or -1.
inline int storeFindHost(const Stored &s, const char *id) {
    for (int i = 0; i < s.nHosts; i++) if (!strcmp(s.hosts[i].id, id)) return i;
    return -1;
}

// Adds a computer or replaces its key; false when all slots hold other computers.
inline bool storeSetHost(Stored &s, const char *id, const uint8_t *key) {
    int i = storeFindHost(s, id);
    if (i < 0) {
        if (s.nHosts >= MAX_HOSTS) return false;
        i = s.nHosts++;
    }
    strlcpy(s.hosts[i].id, id, sizeof(s.hosts[i].id));
    memcpy(s.hosts[i].key, key, sizeof(s.hosts[i].key));
    s.paired = true;
    return true;
}

// Removes a computer (all of them when id is null or empty).
inline void storeRemoveHost(Stored &s, const char *id) {
    if (!id || !id[0]) {
        memset(s.hosts, 0, sizeof(s.hosts));
        s.nHosts = 0;
    } else if (int i = storeFindHost(s, id); i >= 0) {
        for (int j = i; j + 1 < s.nHosts; j++) s.hosts[j] = s.hosts[j + 1];
        memset(&s.hosts[--s.nHosts], 0, sizeof(HostKey));
    }
    s.paired = s.nHosts > 0;
}
