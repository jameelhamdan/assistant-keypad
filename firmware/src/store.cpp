#include "store.h"

#include <Preferences.h>
#include <stdio.h>
#include <string.h>

namespace {
Preferences prefs;

// Slot 0 keeps the names older firmware used ("host", "key"), so a downgrade still finds its computer.
void names(int i, char *host, char *key, size_t n) {
    if (i == 0) {
        snprintf(host, n, "host");
        snprintf(key, n, "key");
    } else {
        snprintf(host, n, "host%d", i);
        snprintf(key, n, "key%d", i);
    }
}
}  // namespace

void storeLoad(Stored &s) {
    memset(&s, 0, sizeof(s));
    s.brightness = 80;
    prefs.begin("keypad", true);
    prefs.getString("ssid", s.ssid, sizeof(s.ssid));
    prefs.getString("pass", s.pass, sizeof(s.pass));
    for (int i = 0; i < MAX_HOSTS; i++) {
        char hn[8], kn[8];
        names(i, hn, kn, sizeof(hn));
        HostKey &h = s.hosts[s.nHosts];
        if (prefs.getString(hn, h.id, sizeof(h.id)) && h.id[0] && prefs.getBytes(kn, h.key, sizeof(h.key)) == sizeof(h.key)) s.nHosts++;
        else memset(&h, 0, sizeof(h));
    }
    s.paired = s.nHosts > 0;
    prefs.getString("name", s.name, sizeof(s.name));
    s.brightness = prefs.getUChar("bright", 80);
    prefs.end();
}

void storeSave(const Stored &s) {
    prefs.begin("keypad", false);
    prefs.putString("ssid", s.ssid);
    prefs.putString("pass", s.pass);
    for (int i = 0; i < MAX_HOSTS; i++) {
        char hn[8], kn[8];
        names(i, hn, kn, sizeof(hn));
        if (i < s.nHosts) {
            prefs.putString(hn, s.hosts[i].id);
            prefs.putBytes(kn, s.hosts[i].key, sizeof(s.hosts[i].key));
        } else {
            if (prefs.isKey(hn)) prefs.remove(hn);
            if (prefs.isKey(kn)) prefs.remove(kn);
        }
    }
    prefs.putString("name", s.name);
    prefs.putUChar("bright", s.brightness);
    prefs.end();
}
