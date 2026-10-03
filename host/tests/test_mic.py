import pytest

from keypad import proto


def test_mic_messages_decode():
    for act in ("start", "stop"):
        assert proto.decode(b'{"t":"mic","act":"%s"}' % act.encode())["act"] == act


def test_mic_rejects_unknown_act():
    with pytest.raises(proto.InvalidMessage):
        proto.decode(b'{"t":"mic","act":"record"}')
