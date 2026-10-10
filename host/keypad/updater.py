"""Keeps the installed app up to date from the project's GitHub releases.

The tray checks api.github.com (HTTPS, certificate verified) for the latest release, downloads the
installer asset for this OS, checks its SHA-256 against the digest GitHub publishes for it, and runs it:
the Windows setup silently (it stops Keypad, replaces it and starts it again), or on macOS a small
script that waits for Keypad to quit, copies the new Keypad.app over the old one and opens it.
Only installed builds update: a development checkout has nothing to replace. No server of our own is
involved, only requests to GitHub."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dirs import data_dir, in_app_bundle, self_path
from .version import version

REPO = "jameelhamdan/assistant-keypad"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
MAX_SIZE = 600 << 20  # an installer larger than this is not ours
FIRST_CHECK = 90  # s after the tray starts
CHECK_EVERY = 6 * 3600
RETRY_WHILE_BUSY = 300  # s: a request is on the keypad; the update waits for it

MAC_SCRIPT = """#!/bin/sh
# Waits for Keypad to quit, replaces the app with the one in the downloaded disk image, opens it.
pid="$1"; dmg="$2"; app="$3"
while kill -0 "$pid" 2>/dev/null; do sleep 0.5; done
mnt="$(mktemp -d)"
hdiutil attach -nobrowse -readonly -quiet -mountpoint "$mnt" "$dmg" || exit 1
if [ -d "$mnt/Keypad.app" ]; then
  rm -rf "$app.new"
  ditto "$mnt/Keypad.app" "$app.new" && rm -rf "$app" && mv "$app.new" "$app"
fi
hdiutil detach -quiet "$mnt"
open "$app"
"""


class UpdateError(Exception):
    """An update could not be checked, downloaded or started; the message says why."""


@dataclass(frozen=True)
class Release:
    version: str
    name: str
    url: str
    size: int
    sha256: str


def parse_version(v: str) -> tuple[int, ...]:
    """"v1.2.3" -> (1, 2, 3); anything after a "-" (a pre-release or git describe suffix) is ignored."""
    return tuple(int(n) for n in re.findall(r"\d+", v.lstrip("v").split("-")[0]))


def is_newer(candidate: str, current: str) -> bool:
    c, n = parse_version(candidate), parse_version(current)
    return bool(c) and bool(n) and c > n


def supported() -> bool:
    """Whether this process is an installed build that can replace itself."""
    if not getattr(sys, "frozen", False) or version() == "dev":
        return False
    return sys.platform == "win32" or (sys.platform == "darwin" and in_app_bundle())


def pick_asset(assets: list[dict[str, Any]], platform: str) -> dict[str, Any] | None:
    suffix = {"win32": "-setup.exe", "darwin": ".dmg"}.get(platform)
    return next((a for a in assets if suffix and str(a.get("name", "")).endswith(suffix)), None)


def _get(url: str, opener: Callable[..., Any], accept: str = "application/vnd.github+json"):
    if not url.startswith("https://"):
        raise UpdateError(f"refusing a non-HTTPS address: {url}")
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"keypad/{version()}"})
    return opener(req, timeout=30)


def fetch_latest(opener: Callable[..., Any] = urllib.request.urlopen, platform: str = sys.platform) -> Release | None:
    """The latest release's installer for this OS, or None when it has none."""
    try:
        with _get(API, opener) as r:
            data = json.loads(r.read(1 << 20))
    except (OSError, ValueError) as e:
        raise UpdateError(f"could not reach GitHub: {e}") from e
    asset = pick_asset(data.get("assets") or [], platform)
    tag = str(data.get("tag_name", ""))
    if not asset or not parse_version(tag):
        return None
    digest = str(asset.get("digest", ""))
    return Release(version=tag.lstrip("v"), name=str(asset["name"]), url=str(asset.get("browser_download_url", "")),
                   size=int(asset.get("size") or 0), sha256=digest.removeprefix("sha256:") if digest.startswith("sha256:") else "")


def download(rel: Release, dest_dir: Path, opener: Callable[..., Any] = urllib.request.urlopen) -> Path:
    """Downloads the installer, refusing it unless its SHA-256 matches the digest GitHub published."""
    if not rel.sha256:
        raise UpdateError("this release has no checksum to verify the download against")
    if not 0 < rel.size <= MAX_SIZE:
        raise UpdateError("the installer has an unexpected size")
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / re.sub(r"[^A-Za-z0-9._-]", "_", rel.name)
    part = final.with_name(final.name + ".part")
    h, n = hashlib.sha256(), 0
    try:
        with _get(rel.url, opener, "application/octet-stream") as r, open(part, "wb") as f:
            if not r.geturl().startswith("https://"):
                raise UpdateError("the download was redirected away from HTTPS")
            while chunk := r.read(1 << 20):
                n += len(chunk)
                if n > rel.size:
                    raise UpdateError("the download is larger than announced")
                h.update(chunk)
                f.write(chunk)
    except OSError as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"download failed: {e}") from e
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    if n != rel.size or h.hexdigest() != rel.sha256.lower():
        part.unlink(missing_ok=True)
        raise UpdateError("the download does not match its checksum")
    os.replace(part, final)
    return final


def app_bundle() -> str:
    """The Keypad.app this process runs from (macOS)."""
    p = Path(self_path()).resolve()
    for parent in p.parents:
        if parent.suffix == ".app":
            return str(parent)
    raise UpdateError("not running from an app bundle")


def apply(path: Path, spawn: Callable[..., None] | None = None) -> None:
    """Starts the installer and returns at once: the caller then quits, so the installer can replace the files."""
    if spawn is None:
        from .service import spawn as spawn_detached

        spawn = spawn_detached
    if sys.platform == "win32":
        spawn(str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS")
    elif sys.platform == "darwin":
        script = path.with_suffix(".sh")
        script.write_text(MAC_SCRIPT)
        script.chmod(0o700)
        spawn("/bin/sh", str(script), str(os.getpid()), str(path), app_bundle())
    else:
        raise UpdateError("updates are supported on macOS and Windows")


def clean(dest_dir: Path, keep: Path | None = None) -> None:
    """Removes installers downloaded earlier."""
    if dest_dir.is_dir():
        for p in dest_dir.iterdir():
            if p != keep:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)


class Updater:
    """Checks now and then; installs by itself when allowed and nothing is waiting on the keypad."""

    def __init__(self, auto: Callable[[], bool], busy: Callable[[], bool], say: Callable[[str, str], None],
                 quit_for_update: Callable[[], None], log: logging.Logger | None = None,
                 fetch: Callable[[], Release | None] = fetch_latest, can_update: Callable[[], bool] = supported):
        self.auto, self.busy, self.say, self.quit_for_update = auto, busy, say, quit_for_update
        self.log = log or logging.getLogger("keypad")
        self._fetch, self._can = fetch, can_update
        self.available: Release | None = None
        self.error = ""
        self._failed = ""  # a version whose automatic install failed: not retried until a restart
        self._lock = threading.Lock()  # one download or install at a time
        self._stop = threading.Event()

    # ---- checking ----

    def check(self) -> Release | None:
        """The newer release, if there is one (also remembered in .available)."""
        try:
            rel = self._fetch()
            if not self._failed:  # a failed install stays reported until Keypad restarts
                self.error = ""
        except UpdateError as e:
            self.error = str(e)
            self.log.info("update check failed: %s", e)
            return None
        self.available = rel if rel and is_newer(rel.version, version()) else None
        if self.available:
            self.log.info("update available: %s (running %s)", self.available.version, version())
        return self.available

    def start(self) -> None:
        if self._can():
            clean(data_dir() / "updates")  # the installer of an update that has now finished
            threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        wait = FIRST_CHECK
        while not self._stop.wait(wait):
            wait = CHECK_EVERY
            rel = self.check()
            if rel and self.auto() and rel.version != self._failed:
                if self.busy():  # a request is on the keypad: look again soon
                    wait = RETRY_WHILE_BUSY
                    continue
                try:
                    self.install()
                except UpdateError as e:
                    self._failed = rel.version
                    self.error = str(e)
                    self.log.warning("automatic update to %s failed: %s", rel.version, e)
                    self.say("Keypad update failed", f"{e}. Use Advanced -> Check for updates to try again.")

    # ---- installing ----

    def install(self) -> None:
        """Downloads and starts the installer for .available, then quits Keypad so it can be replaced."""
        rel = self.available
        if rel is None:
            raise UpdateError("no update is available")
        if not self._can():
            raise UpdateError("this build cannot update itself: install the new version from the releases page")
        if not self._lock.acquire(blocking=False):
            raise UpdateError("an update is already in progress")
        try:
            folder = data_dir() / "updates"
            clean(folder)
            self.say("Updating Keypad", f"Downloading {rel.version}…")
            path = download(rel, folder)
            self.log.info("installing %s from %s", rel.version, path)
            self.say("Updating Keypad", f"Installing {rel.version}. Keypad restarts in a moment.")
            apply(path)
        finally:
            self._lock.release()
        self.quit_for_update()
