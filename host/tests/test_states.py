"""The session states are spelled out in three places (the agent, the tray and the
firmware's ui.cpp). This keeps them from drifting apart."""

import re
from pathlib import Path

from keypad.core import sessions as S
from keypad.tray import state_word

UI = (Path(__file__).resolve().parents[2] / "firmware" / "src" / "ui.cpp").read_text(encoding="utf-8")


def firmware_fn(name: str) -> set[str]:
    """The state names compared with strcmp in one of ui.cpp's state functions."""
    body = re.search(rf"\n[\w \*]+ {name}\(const char \*s\) \{{(.*?)\n\}}\n", UI, re.S)
    assert body, f"{name}() not found in ui.cpp"
    return set(re.findall(r'strcmp\(s, "(\w+)"\)', body.group(1)))


def test_firmware_working_states_match_the_agent():
    assert firmware_fn("busy") == S.WORKING_STATES


def test_firmware_waiting_states_match_the_agent():
    assert firmware_fn("waiting") == {S.PERMISSION, S.QUESTION, S.INPUT}  # "stopped" waits too, but is only coloured
    word = re.search(r"const char \*stateWord\(const char \*s\) \{(.*?)\n\}", UI, re.S).group(1)
    needs_you = set(re.findall(r'strcmp\(s, "(\w+)"\)', re.search(r"if \(!strcmp\(s, \"permission\"\).*?return \"needs you\"", word, re.S).group(0)))
    assert needs_you == S.WAITING_STATES


def test_tray_words_follow_the_same_sets():
    assert {state_word(s) for s in S.WORKING_STATES} == {"working"}
    assert {state_word(s) for s in S.WAITING_STATES} == {"needs you"}
    assert state_word(S.FAILED) == "failed" and state_word(S.IDLE) == "idle"
