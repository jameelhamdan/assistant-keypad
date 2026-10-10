import hashlib
import io
import json
import logging

import pytest

from keypad import updater
from keypad.updater import Release, UpdateError

ASSETS = [
    {"name": "Keypad-1.4.0.dmg", "browser_download_url": "https://github.com/x/y/releases/download/v1.4.0/Keypad-1.4.0.dmg",
     "size": 5, "digest": "sha256:" + hashlib.sha256(b"mac!!").hexdigest()},
    {"name": "Keypad-1.4.0-setup.exe", "browser_download_url": "https://github.com/x/y/releases/download/v1.4.0/Keypad-1.4.0-setup.exe",
     "size": 5, "digest": "sha256:" + hashlib.sha256(b"win!!").hexdigest()},
]


class Resp(io.BytesIO):
    def __init__(self, body: bytes, url: str = "https://github.com/x"):
        super().__init__(body)
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def opener_for(body: bytes, url: str = "https://github.com/x"):
    return lambda req, timeout=0: Resp(body, url)


def test_versions_compare_numerically():
    assert updater.is_newer("1.10.0", "1.9.3") and updater.is_newer("v2.0.0", "1.99.99")
    assert not updater.is_newer("1.2.3", "1.2.3") and not updater.is_newer("1.2.2", "1.2.3")
    assert updater.is_newer("1.2.4", "1.2.3-5-gabc123")
    assert not updater.is_newer("1.2.3", "dev"), "a development build is never 'updated'"
    assert not updater.is_newer("garbage", "1.0.0")


def test_the_installer_for_this_os_is_picked():
    assert updater.pick_asset(ASSETS, "win32")["name"].endswith("-setup.exe")
    assert updater.pick_asset(ASSETS, "darwin")["name"].endswith(".dmg")
    assert updater.pick_asset(ASSETS, "linux") is None
    assert updater.pick_asset([], "win32") is None


def test_the_latest_release_is_read_from_the_github_api():
    body = json.dumps({"tag_name": "v1.4.0", "assets": ASSETS}).encode()
    rel = updater.fetch_latest(opener_for(body), platform="win32")
    assert rel.version == "1.4.0" and rel.name == "Keypad-1.4.0-setup.exe" and rel.size == 5
    assert rel.sha256 == hashlib.sha256(b"win!!").hexdigest()
    assert updater.fetch_latest(opener_for(json.dumps({"tag_name": "v1.4.0", "assets": []}).encode()), platform="win32") is None


def test_a_network_failure_is_an_update_error_not_a_crash():
    def broken(req, timeout=0):
        raise OSError("no route")

    with pytest.raises(UpdateError, match="could not reach GitHub"):
        updater.fetch_latest(broken)


def release(body=b"win!!", **kw):
    return Release("1.4.0", "Keypad-1.4.0-setup.exe", "https://github.com/x/y/Keypad-1.4.0-setup.exe", len(body),
                   kw.pop("sha256", hashlib.sha256(body).hexdigest()), **kw)


def test_a_download_is_kept_only_if_its_checksum_matches(tmp_path):
    p = updater.download(release(), tmp_path, opener_for(b"win!!"))
    assert p.read_bytes() == b"win!!" and not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("body,why", [(b"evil", "checksum"), (b"win!!!!!", "larger")])
def test_a_wrong_download_is_deleted_and_refused(tmp_path, body, why):
    with pytest.raises(UpdateError, match=why):
        updater.download(release(), tmp_path, opener_for(body))
    assert list(tmp_path.iterdir()) == [], "nothing is left to be run by mistake"


def test_no_checksum_no_install(tmp_path):
    with pytest.raises(UpdateError, match="no checksum"):
        updater.download(release(sha256=""), tmp_path, opener_for(b"win!!"))


def test_a_redirect_away_from_https_is_refused(tmp_path):
    with pytest.raises(UpdateError, match="HTTPS"):
        updater.download(release(), tmp_path, opener_for(b"win!!", "http://evil.example/x"))
    with pytest.raises(UpdateError, match="HTTPS"):
        updater.download(Release("1", "a-setup.exe", "http://github.com/a", 5, "0" * 64), tmp_path, opener_for(b"win!!"))


def test_windows_runs_the_setup_silently(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    calls = []
    updater.apply(tmp_path / "Keypad-1.4.0-setup.exe", spawn=lambda *a: calls.append(a))
    assert calls == [(str(tmp_path / "Keypad-1.4.0-setup.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS")]


def test_macos_hands_over_to_a_script_that_waits_for_the_app_to_quit(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.sys, "platform", "darwin")
    monkeypatch.setattr(updater, "app_bundle", lambda: "/Applications/Keypad.app")
    calls = []
    dmg = tmp_path / "Keypad-1.4.0.dmg"
    updater.apply(dmg, spawn=lambda *a: calls.append(a))
    prog, script, pid, image, app = calls[0]
    assert prog == "/bin/sh" and image == str(dmg) and app == "/Applications/Keypad.app" and pid.isdigit()
    text = (tmp_path / "Keypad-1.4.0.sh").read_text()
    assert "kill -0" in text and "hdiutil attach" in text and 'open "$app"' in text


class Rig:
    def __init__(self, rel, auto=True, busy=False, can=True):
        self.said, self.quit, self.installs = [], [], []
        self.busy_now = busy
        self.u = updater.Updater(lambda: auto, lambda: self.busy_now, lambda t, m: self.said.append((t, m)),
                                 lambda: self.quit.append(1), logging.getLogger("test"), fetch=lambda: rel, can_update=lambda: can)


def test_a_newer_release_becomes_available_an_older_one_does_not(monkeypatch):
    monkeypatch.setattr(updater, "version", lambda: "1.3.0")
    assert Rig(release()).u.check().version == "1.4.0"
    monkeypatch.setattr(updater, "version", lambda: "1.4.0")
    assert Rig(release()).u.check() is None


def test_installing_downloads_verifies_starts_the_installer_and_quits(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "version", lambda: "1.3.0")
    monkeypatch.setenv("KEYPAD_HOME", str(tmp_path))
    started = []
    monkeypatch.setattr(updater, "download", lambda rel, folder: (folder.mkdir(parents=True, exist_ok=True), folder / "setup.exe")[1])
    monkeypatch.setattr(updater, "apply", lambda path: started.append(path.name))
    r = Rig(release())
    r.u.check()
    r.u.install()
    assert started == ["setup.exe"] and r.quit == [1]
    assert any("Installing 1.4.0" in m for _, m in r.said)


def test_a_development_build_never_installs(monkeypatch):
    monkeypatch.setattr(updater, "version", lambda: "1.3.0")
    r = Rig(release(), can=False)
    r.u.check()
    with pytest.raises(UpdateError, match="cannot update itself"):
        r.u.install()
    assert r.quit == []


def test_nothing_to_install_is_an_error(monkeypatch):
    with pytest.raises(UpdateError, match="no update"):
        Rig(None).u.install()


def test_dev_checkouts_are_not_updatable():
    assert updater.supported() is False


def run_loop(monkeypatch, rig, seconds=0.5):
    import threading
    import time

    monkeypatch.setattr(updater, "FIRST_CHECK", 0.01)
    monkeypatch.setattr(updater, "CHECK_EVERY", 0.05)
    monkeypatch.setattr(updater, "RETRY_WHILE_BUSY", 0.05)
    monkeypatch.setattr(updater, "version", lambda: "1.3.0")
    t = threading.Thread(target=rig.u._loop, daemon=True)
    t.start()
    time.sleep(seconds)
    rig.u.stop()


def test_it_installs_by_itself_only_when_allowed_and_idle(monkeypatch):
    for kw, installs in (({"auto": False}, 0), ({"busy": True}, 0), ({}, 1)):
        r = Rig(release(), **kw)
        n = []
        r.u.install = lambda n=n: n.append(1)
        run_loop(monkeypatch, r, 0.3)
        assert (len(n) > 0) == bool(installs), kw
    r = Rig(release(), busy=True)
    n = []
    r.u.install = lambda: n.append(1)
    import threading
    import time

    monkeypatch.setattr(updater, "FIRST_CHECK", 0.01)
    monkeypatch.setattr(updater, "RETRY_WHILE_BUSY", 0.05)
    monkeypatch.setattr(updater, "version", lambda: "1.3.0")
    threading.Thread(target=r.u._loop, daemon=True).start()
    time.sleep(0.2)
    assert not n
    r.busy_now = False  # the request was answered: now it goes
    time.sleep(0.3)
    r.u.stop()
    assert n


def test_a_failed_automatic_update_is_not_retried_in_a_loop(monkeypatch):
    r = Rig(release())
    tries = []

    def boom():
        tries.append(1)
        raise UpdateError("disk full")

    r.u.install = boom
    run_loop(monkeypatch, r, 0.4)
    assert len(tries) == 1, "one try per version until Keypad restarts"
    assert any("failed" in t for t, _ in r.said) and r.u.error == "disk full"


def test_a_finished_updates_installer_is_removed_at_the_next_start(tmp_path, monkeypatch):
    monkeypatch.setenv("KEYPAD_HOME", str(tmp_path))
    folder = tmp_path / "updates"
    folder.mkdir()
    (folder / "Keypad-3.1.0-setup.exe").write_bytes(b"old")
    r = Rig(None)
    r.u._loop = lambda: None  # no checking in this test
    r.u.start()
    assert not list(folder.iterdir())
