#include "store.h"

#include <Preferences.h>
#include <string.h>

namespace {
Preferences prefs;
}

void storeLoad(Stored &s) {
    memset(&s, 0, sizeof(s));
    s.brightness = 80;
    prefs.begin("keypad", true);
    prefs.getString("ssid", s.ssid, sizeof(s.ssid));
    prefs.getString("pass", s.pass, sizeof(s.pass));
    prefs.getString("host", s.host, sizeof(s.host));
    s.paired = prefs.getBytes("key", s.key, sizeof(s.key)) == sizeof(s.key) && s.host[0];
    prefs.getString("name", s.name, sizeof(s.name));
    s.brightness = prefs.getUChar("bright", 80);
    prefs.end();
}

void storeSave(const Stored &s) {
    prefs.begin("keypad", false);
    prefs.putString("ssid", s.ssid);
    prefs.putString("pass", s.pass);
    prefs.putString("host", s.host);
    if (s.paired) prefs.putBytes("key", s.key, sizeof(s.key));
    else if (prefs.isKey("key")) prefs.remove("key");
    prefs.putString("name", s.name);
    prefs.putUChar("bright", s.brightness);
    prefs.end();
}
