import pytest

from keypad import proto


def test_mic_messages_decode():
    for act in ("start", "stop"):
        assert proto.decode(b'{"t":"mic","act":"%s"}' % act.encode())["act"] == act


def test_mic_rejects_unknown_act():
    with pytest.raises(proto.InvalidMessage):
        proto.decode(b'{"t":"mic","act":"record"}')


def test_usb_scan_follows_wifi_only(home):
    from keypad import config
    from keypad.device.hub import Hub

    store = config.Store("h-1", [])
    hub = Hub(store, ev=None, wifi_only=lambda: True)
    assert hub.usb_wanted()  # nothing paired yet: first setup needs the cable
    store.update("kp-1", lambda d: setattr(d, "key", "k" * 64))
    assert not hub.usb_wanted()  # paired: the cable is only power
    hub.open_pairing()
    assert hub.usb_wanted()  # Add keypad… opens a window
    assert Hub(store, ev=None).usb_wanted()  # Wi-Fi only off: as before
