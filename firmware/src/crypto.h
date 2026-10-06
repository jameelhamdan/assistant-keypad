// Session crypto for the Wi-Fi link (see proto/PROTOCOL.md): HKDF-SHA256
// key derivation and AES-256-GCM with implicit 64-bit counters.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "mbedtls/gcm.h"

constexpr size_t GCM_TAG = 16;

// okm = HKDF-SHA256(psk, salt = nonceHost || nonceDevice, "keypad v3", 64)
bool deriveKeys(const uint8_t psk[32], const uint8_t nonceHost[16], const uint8_t nonceDevice[16],
                uint8_t h2d[32], uint8_t d2h[32]);

class Sealer {
public:
    Sealer();
    ~Sealer();
    bool init(const uint8_t key[32]);
    // out must hold len + GCM_TAG bytes (ciphertext || tag).
    bool seal(const uint8_t *in, size_t len, uint8_t *out);
    // in = ciphertext || tag; out receives len - GCM_TAG bytes.
    bool open(const uint8_t *in, size_t len, uint8_t *out);

private:
    void nonce(uint8_t iv[12]);
    mbedtls_gcm_context ctx_;
    uint64_t ctr_ = 0;
};

// Decodes exactly n bytes from 2n hex characters.
bool hexDecode(const char *hex, uint8_t *out, size_t n);

// Checks the implementation against the vector in host/tests/test_secure.py.
bool cryptoSelfTest();
