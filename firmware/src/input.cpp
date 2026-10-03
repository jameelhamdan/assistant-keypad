#include "input.h"

#include <Arduino.h>

#include "config.h"

namespace {

constexpr uint8_t SLOTS = NUM_KEYS + 2;   // index 0..7 = keys 1..8, 8 = encoder switch, 9 = mic button
constexpr uint8_t ENC_SLOT = NUM_KEYS;
constexpr uint8_t MIC_SLOT = NUM_KEYS + 1;

struct Key {
    bool stable, raw;
    uint32_t changed, pressStart;
    bool clean;
};
Key keys[SLOTS];

constexpr uint8_t QN = 16;
KeyEvent queue[QN];
uint8_t qHead = 0, qCount = 0;

uint8_t keyOf(uint8_t slot) { return slot == ENC_SLOT ? KEY_ENC : slot == MIC_SLOT ? KEY_MIC : slot + 1; }
uint8_t slotOf(uint8_t key) { return key == KEY_ENC ? ENC_SLOT : key == KEY_MIC ? MIC_SLOT : key - 1; }

void push(const KeyEvent &ev) {
    if (qCount == QN) { qHead = (qHead + 1) % QN; qCount--; }   // drop the oldest
    queue[(qHead + qCount) % QN] = ev;
    qCount++;
}

bool othersDown(uint8_t slot) {
    for (uint8_t i = 0; i < SLOTS; i++) if (i != slot && i != MIC_SLOT && keys[i].stable) return true;   // the mic has its own pin: no ghosting
    return false;
}

void debounce(uint8_t slot, bool raw, uint32_t now) {
    Key &k = keys[slot];
    if (raw != k.raw) { k.raw = raw; k.changed = now; }
    if (now - k.changed >= DEBOUNCE_MS && raw != k.stable) {
        k.stable = raw;
        if (raw) {
            k.pressStart = now;
            k.clean = !othersDown(slot);
        }
        push({keyOf(slot), raw ? KeyAction::Press : KeyAction::Release, now, k.pressStart, k.clean});
    }
}

// Quadrature decoder (Ben Buxton's table): one step per detent, bounce-immune.
constexpr uint8_t R_START = 0, R_CW_FINAL = 1, R_CW_BEGIN = 2, R_CW_NEXT = 3, R_CCW_BEGIN = 4, R_CCW_FINAL = 5,
                  R_CCW_NEXT = 6, DIR_CW = 0x10, DIR_CCW = 0x20;
const uint8_t TABLE[7][4] = {
    {R_START, R_CW_BEGIN, R_CCW_BEGIN, R_START},
    {R_CW_NEXT, R_START, R_CW_FINAL, R_START | DIR_CW},
    {R_CW_NEXT, R_CW_BEGIN, R_START, R_START},
    {R_CW_NEXT, R_CW_BEGIN, R_CW_FINAL, R_START},
    {R_CCW_NEXT, R_START, R_CCW_BEGIN, R_START},
    {R_CCW_NEXT, R_CCW_FINAL, R_START, R_START | DIR_CCW},
    {R_CCW_NEXT, R_CCW_FINAL, R_CCW_BEGIN, R_START},
};
volatile uint8_t encState = R_START;
volatile int32_t encSteps = 0;
int32_t encTaken = 0;

void IRAM_ATTR onEncoder() {
    uint8_t pins = (digitalRead(ENC_CLK_PIN) << 1) | digitalRead(ENC_DT_PIN);
    encState = TABLE[encState & 0x0F][pins];
    if ((encState & 0x30) == DIR_CW) encSteps = encSteps + 1;
    else if ((encState & 0x30) == DIR_CCW) encSteps = encSteps - 1;
}

}  // namespace

void inputBegin() {
    for (int8_t p : ROW_PINS) { pinMode(p, OUTPUT); digitalWrite(p, HIGH); }
    for (int8_t p : COL_PINS) pinMode(p, INPUT_PULLUP);
    pinMode(ENC_SW_PIN, INPUT_PULLUP);
    if (MIC_PIN >= 0) pinMode(MIC_PIN, INPUT_PULLUP);
    pinMode(ENC_CLK_PIN, INPUT_PULLUP);
    pinMode(ENC_DT_PIN, INPUT_PULLUP);
    attachInterrupt(digitalPinToInterrupt(ENC_CLK_PIN), onEncoder, CHANGE);
    attachInterrupt(digitalPinToInterrupt(ENC_DT_PIN), onEncoder, CHANGE);
}

void inputPoll() {
    uint32_t now = millis();
    for (uint8_t r = 0; r < NUM_ROWS; r++) {
        digitalWrite(ROW_PINS[r], LOW);
        delayMicroseconds(50);
        for (uint8_t c = 0; c < NUM_COLS; c++) {
            debounce(slotOf(KEY_MAP[r][c]), digitalRead(COL_PINS[c]) == LOW, now);
        }
        digitalWrite(ROW_PINS[r], HIGH);
    }
    debounce(ENC_SLOT, digitalRead(ENC_SW_PIN) == LOW, now);
    if (MIC_PIN >= 0) debounce(MIC_SLOT, digitalRead(MIC_PIN) == LOW, now);
}

bool inputNext(KeyEvent &ev) {
    if (qCount == 0) return false;
    ev = queue[qHead];
    qHead = (qHead + 1) % QN;
    qCount--;
    return true;
}

int32_t inputTakeSteps() {
    noInterrupts();
    int32_t s = encSteps;
    interrupts();
    int32_t d = s - encTaken;
    encTaken = s;
    return d;
}

bool inputHeld(uint8_t key, uint32_t forMs) {
    const Key &k = keys[slotOf(key)];
    return k.stable && millis() - k.pressStart >= forMs;
}
