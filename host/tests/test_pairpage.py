import json
import urllib.error
import urllib.request

import pytest

from keypad.pairpage import PairPage


class FakeAgent:
    def snapshot(self):
        return {"keypads": [{"id": "kp-1", "name": "kp-1", "link": "usb", "addr": "COM5", "battery": -1}], "problems": {}}


class FakeHub:
    def __init__(self):
        self.calls = []
        self.opened = 0

    def open_pairing(self):
        self.opened += 1

    def provision(self, dev_id, ssid, password, name):
        if ssid == "bad":
            raise RuntimeError("keypad rejected the settings")
        self.calls.append((dev_id, ssid, password, name))


@pytest.fixture
def page():
    hub = FakeHub()
    p = PairPage(FakeAgent(), hub)
    p.hub_calls, p.fake_hub = hub.calls, hub
    yield p
    p.close()


def fetch(url, data=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read()


def test_the_page_lists_the_usb_keypad_and_pairs_it(page):
    url = page.url()
    status, body = fetch(url)
    assert status == 200 and b"Set up a keypad" in body and page.token.encode() in body
    _, state = fetch(url.replace("/?", "/state?"))
    assert json.loads(state)["keypads"][0]["link"] == "usb"
    assert page.fake_hub.opened == 1, "an open page keeps USB scanned"
    _, res = fetch(url.replace("/?", "/pair?"), {"id": "kp-1", "name": "Desk", "ssid": "Home", "pass": "secret"},
                   {"Content-Type": "application/json"})
    assert json.loads(res) == {"ok": True} and page.hub_calls == [("kp-1", "Home", "secret", "Desk")]


def test_errors_are_reported_on_the_page(page):
    url = page.url().replace("/?", "/pair?")
    _, res = fetch(url, {"id": "kp-1", "ssid": "bad"}, {"Content-Type": "application/json"})
    assert json.loads(res)["ok"] is False and "rejected" in json.loads(res)["error"]
    _, res = fetch(url, {"id": "kp-1", "ssid": ""}, {"Content-Type": "application/json"})
    assert "network name" in json.loads(res)["error"]


def test_nothing_works_without_the_token_or_with_a_foreign_host_header(page):
    url = page.url()
    with pytest.raises(urllib.error.HTTPError) as e:
        fetch(url.split("?")[0])
    assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        fetch(url.split("?")[0] + "state?t=wrong")
    assert e.value.code == 403
    import http.client

    port = int(url.split(":")[2].split("/")[0])
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)  # a web page elsewhere pointing a name at 127.0.0.1
    c.request("GET", "/?t=" + page.token, headers={"Host": "evil.example"})
    assert c.getresponse().status == 403
    assert page.hub_calls == []
