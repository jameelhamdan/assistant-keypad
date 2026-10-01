#include "crypto.h"

#include <string.h>

#include "mbedtls/md.h"

namespace {

bool hmac(const uint8_t *key, size_t keyLen, const uint8_t *a, size_t aLen, const uint8_t *b, size_t bLen,
          uint8_t out[32]) {
    const mbedtls_md_info_t *md = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    mbedtls_md_context_t c;
    mbedtls_md_init(&c);
    bool ok = mbedtls_md_setup(&c, md, 1) == 0 && mbedtls_md_hmac_starts(&c, key, keyLen) == 0 &&
              mbedtls_md_hmac_update(&c, a, aLen) == 0 && (bLen == 0 || mbedtls_md_hmac_update(&c, b, bLen) == 0) &&
              mbedtls_md_hmac_finish(&c, out) == 0;
    mbedtls_md_free(&c);
    return ok;
}

}  // namespace

bool deriveKeys(const uint8_t psk[32], const uint8_t nh[16], const uint8_t nd[16], uint8_t h2d[32], uint8_t d2h[32]) {
    uint8_t salt[32], prk[32], t1[32], t2[32];
    memcpy(salt, nh, 16);
    memcpy(salt + 16, nd, 16);
    static const char INFO[] = "keypad v2";
    const size_t infoLen = sizeof(INFO) - 1;
    uint8_t buf[32 + sizeof(INFO)];
    // extract
    if (!hmac(salt, 32, psk, 32, nullptr, 0, prk)) return false;
    // expand: T1 = HMAC(prk, info || 1), T2 = HMAC(prk, T1 || info || 2)
    memcpy(buf, INFO, infoLen);
    buf[infoLen] = 1;
    if (!hmac(prk, 32, buf, infoLen + 1, nullptr, 0, t1)) return false;
    memcpy(buf, INFO, infoLen);
    buf[infoLen] = 2;
    if (!hmac(prk, 32, t1, 32, buf, infoLen + 1, t2)) return false;
    memcpy(h2d, t1, 32);
    memcpy(d2h, t2, 32);
    memset(prk, 0, sizeof(prk));
    return true;
}

Sealer::Sealer() { mbedtls_gcm_init(&ctx_); }
Sealer::~Sealer() { mbedtls_gcm_free(&ctx_); }

bool Sealer::init(const uint8_t key[32]) {
    ctr_ = 0;
    mbedtls_gcm_free(&ctx_);
    mbedtls_gcm_init(&ctx_);
    return mbedtls_gcm_setkey(&ctx_, MBEDTLS_CIPHER_ID_AES, key, 256) == 0;
}

void Sealer::nonce(uint8_t iv[12]) {
    memset(iv, 0, 4);
    for (int i = 0; i < 8; i++) iv[4 + i] = (uint8_t)(ctr_ >> (56 - 8 * i));
    ctr_++;
}

bool Sealer::seal(const uint8_t *in, size_t len, uint8_t *out) {
    uint8_t iv[12];
    nonce(iv);
    return mbedtls_gcm_crypt_and_tag(&ctx_, MBEDTLS_GCM_ENCRYPT, len, iv, 12, nullptr, 0, in, out, GCM_TAG,
                                     out + len) == 0;
}

bool Sealer::open(const uint8_t *in, size_t len, uint8_t *out) {
    if (len < GCM_TAG) return false;
    uint8_t iv[12];
    nonce(iv);
    size_t n = len - GCM_TAG;
    return mbedtls_gcm_auth_decrypt(&ctx_, n, iv, 12, nullptr, 0, in + n, GCM_TAG, in, out) == 0;
}

namespace {
int nibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
}  // namespace

// Strict: sscanf("%2x") would also accept " f", "+f" and "-1".
bool hexDecode(const char *hex, uint8_t *out, size_t n) {
    if (!hex || strlen(hex) != n * 2) return false;
    for (size_t i = 0; i < n; i++) {
        int hi = nibble(hex[2 * i]), lo = nibble(hex[2 * i + 1]);
        if (hi < 0 || lo < 0) return false;
        out[i] = (uint8_t)(hi << 4 | lo);
    }
    return true;
}

bool cryptoSelfTest() {
    uint8_t psk[32], nh[16], nd[16], h2d[32], d2h[32];
    for (int i = 0; i < 32; i++) psk[i] = i;
    for (int i = 0; i < 16; i++) { nh[i] = 0xA0 + i; nd[i] = 0xB0 + i; }
    static const uint8_t WANT_H2D[4] = {0xe2, 0x5d, 0xa1, 0x96};
    static const uint8_t WANT_D2H[4] = {0x5d, 0xca, 0xb9, 0x87};
    static const uint8_t WANT_CT[28] = {0xaf, 0xab, 0x16, 0x71, 0x07, 0xd1, 0x96, 0xed, 0x52, 0x4c,
                                        0x87, 0x9b, 0x0e, 0x88, 0x12, 0x46, 0x5b, 0x0b, 0x6b, 0xde,
                                        0x0e, 0x2d, 0xf6, 0xc0, 0xbe, 0x57, 0xca, 0x88};
    if (!deriveKeys(psk, nh, nd, h2d, d2h)) return false;
    if (memcmp(h2d, WANT_H2D, 4) != 0 || memcmp(d2h, WANT_D2H, 4) != 0) return false;
    Sealer s;
    uint8_t ct[28];
    const char *msg = "{\"t\":\"ping\"}";
    if (!s.init(h2d) || !s.seal((const uint8_t *)msg, 12, ct)) return false;
    if (memcmp(ct, WANT_CT, 28) != 0) return false;
    Sealer r;
    uint8_t back[12];
    return r.init(h2d) && r.open(ct, 28, back) && memcmp(back, msg, 12) == 0;
}
