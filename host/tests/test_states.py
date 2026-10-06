"""The keypad's session states are the words in core.sessions.PHASES; ui.cpp may only compare against those."""

import re
from pathlib import Path

from keypad.core import sessions as S
from keypad.tray import state_word

UI = (Path(__file__).resolve().parents[2] / "firmware" / "src" / "ui.cpp").read_text(encoding="utf-8")
WORDS = set(S.PHASES.values()) | {"idle"}


def test_firmware_only_knows_the_words_the_host_sends():
    used = set(re.findall(r'strcmp\((?:s|[\w.>-]*state), "(\w+)"\)', UI))
    assert used and used <= WORDS, used - WORDS


def test_every_session_state_has_a_phase():
    for state in (S.IDLE, S.THINKING, S.WORKING, S.TOOL, S.PERMISSION, S.QUESTION, S.DONE, S.STOPPED, S.CONTINUING):
        assert S.phase(state) in WORDS


def test_tray_words_follow_the_phases():
    assert {state_word(S.phase(s)) for s in (S.THINKING, S.WORKING, S.TOOL, S.CONTINUING)} == {"working"}
    assert {state_word(S.phase(s)) for s in (S.PERMISSION, S.QUESTION, S.STOPPED)} == {"needs you"}
    assert state_word(S.phase(S.IDLE)) == "idle"
