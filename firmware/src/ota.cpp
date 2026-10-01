#include "ota.h"

#include <Update.h>
#include <string.h>

#include "mbedtls/base64.h"

namespace {
bool running = false;
size_t total = 0, written = 0;
uint8_t chunk[3072];
}  // namespace

bool otaBegin(size_t size, const char *md5, const char **err) {
    otaAbort();
    if (size == 0 || !Update.begin(size, U_FLASH)) {
        *err = "not enough space";
        return false;
    }
    if (md5 && strlen(md5) == 32) Update.setMD5(md5);
    running = true;
    total = size;
    written = 0;
    return true;
}

bool otaWrite(size_t offset, const char *b64, const char **err) {
    if (!running) { *err = "no update in progress"; return false; }
    if (offset != written) { *err = "unexpected offset"; return false; }
    size_t n = 0;
    if (mbedtls_base64_decode(chunk, sizeof(chunk), &n, (const unsigned char *)b64, strlen(b64)) != 0 || n == 0) {
        *err = "bad chunk";
        return false;
    }
    if (Update.write(chunk, n) != n) {
        *err = Update.errorString();
        otaAbort();
        return false;
    }
    written += n;
    return true;
}

bool otaFinish(const char **err) {
    if (!running || written != total) { *err = "incomplete image"; otaAbort(); return false; }
    running = false;
    if (!Update.end(true)) { *err = Update.errorString(); return false; }
    return true;
}

bool otaRunning() { return running; }
int otaPercent() { return total ? (int)(written * 100 / total) : 0; }

void otaAbort() {
    if (running) Update.abort();
    running = false;
}
