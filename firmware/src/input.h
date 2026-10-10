// Key matrix (keys 1-8), encoder switch (key 0) and encoder rotation.
// One PRESS and one RELEASE per physical press.
// `clean` = no other key was down when the press began (the matrix has no
// diodes, so decision screens only accept clean presses).
#pragma once

#include <stdint.h>

enum class KeyAction : uint8_t { Press, Release };

struct KeyEvent {
    uint8_t key;        // 1..8, 0 for the encoder switch
    KeyAction action;
    uint32_t at;        // millis() of the event
    uint32_t pressedAt; // millis() when the press began
    bool clean;
};

void inputBegin();
void inputPoll();                       // every loop iteration
bool inputNext(KeyEvent &ev);           // false when the queue is empty
int32_t inputTakeSteps();               // encoder detents since last call (+ clockwise)
