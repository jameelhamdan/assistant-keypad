"""User settings (config.json, edited from the tray or by hand) and machine
state (state.json: host id, known keypads and their pairing keys)."""

from __future__ import annotations

import copy
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from .dirs import data_dir as _data_dir

MAX_SHORTCUTS = 16  # what the tray lists and the keypad pages through


def data_dir() -> Path:
    """The per-user data directory (config, state, logs, agent.json)."""
    return Path(_data_dir())


def log_dir() -> Path:
    return data_dir() / "logs"


def config_path() -> Path:
    return data_dir() / "config.json"


def state_path() -> Path:
    return data_dir() / "state.json"


# ---- settings -----------------------------------------------------------------


@dataclass
class Shortcut:
    label: str = ""
    prompt: str = ""


@dataclass
class Behavior:
    ask_when_finished: int = 60  # s away from the PC before Claude finishing is asked on the keypad (0 = always, -1 = never)
    max_continues: int = 20
    intercept_ask_user_question: bool = True
    shortcut_ttl: int = 900
    timeout: int = 300  # s the keypad waits for an answer before the PC takes over


# Earlier versions wrote these eight shortcuts into every new config file.
# A list that is exactly them was never edited, so it is dropped on load.
OLD_DEFAULT_LABELS = ["Run tests", "Write tests", "Fix bugs", "Refactor", "Review", "Improve design", "Explain", "Commit"]


@dataclass
class Config:
    behavior: Behavior = field(default_factory=Behavior)
    shortcuts: list[Shortcut] = field(default_factory=list)  # saved prompts, sent from the keypad (none by default)
    log_level: str = "info"

    def validate(self) -> None:
        """Clamps values into safe ranges."""
        b = self.behavior

        def clamp(v: Any, lo: int, hi: int) -> int:
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = lo
            return max(lo, min(hi, v))

        b.max_continues = clamp(b.max_continues, 1, 200)
        b.shortcut_ttl = clamp(b.shortcut_ttl, 30, 86400)
        b.timeout = clamp(b.timeout, 10, 3600)
        b.ask_when_finished = clamp(b.ask_when_finished, -1, 3600)
        self.shortcuts = [s for s in self.shortcuts if s.label.strip() and s.prompt.strip()][:MAX_SHORTCUTS]

    # ---- (de)serialisation: the same names in YAML and over IPC ----
    def to_dict(self) -> dict[str, Any]:
        return _to_plain(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Config:
        return _from_plain(cls, d or {})

    def copy(self) -> Config:
        return copy.deepcopy(self)


def _to_plain(v: Any) -> Any:
    if is_dataclass(v):
        return {f.name: _to_plain(getattr(v, f.name)) for f in fields(v)}
    if isinstance(v, list):
        return [_to_plain(x) for x in v]
    return v


def _from_plain(cls: type, d: Any) -> Any:
    """Builds cls from d, starting from cls's defaults; unknown keys are ignored."""
    obj = cls()
    if not isinstance(d, dict):
        return obj
    for f in fields(cls):
        if f.name not in d:
            continue
        cur, v = getattr(obj, f.name), d[f.name]
        if is_dataclass(cur):
            setattr(obj, f.name, _from_plain(type(cur), v))
        elif f.name == "shortcuts" and isinstance(v, list):
            setattr(obj, f.name, [_from_plain(Shortcut, x) for x in v if isinstance(x, dict)])
        elif isinstance(cur, bool):
            # "no", "off", "false" and "0" in a hand-edited file mean off
            setattr(obj, f.name, v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on"))
        elif isinstance(cur, int):
            setattr(obj, f.name, v)  # validate() clamps and converts
        elif isinstance(cur, str):
            setattr(obj, f.name, str(v) if v is not None else "")
    return obj


def load() -> tuple[Config, Exception | None]:
    """Reads config.json, creating it on first run. The result is always usable:
    an unreadable file gives the defaults."""
    p = config_path()
    if not p.exists():
        c = Config()
        legacy = p.with_suffix(".yaml")
        if legacy.exists():  # Keypad 2.x kept its settings in YAML: carry them over once
            try:
                c = Config.from_dict(_migrate_legacy(_read_legacy_yaml(legacy.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                c = Config()
        try:
            save(c)
        except OSError as e:
            return c, e
        return c, None
    try:
        c = Config.from_dict(json.loads(p.read_text(encoding="utf-8")) or {})
    except (OSError, ValueError) as e:
        return Config(), e
    if [s.label for s in c.shortcuts] == OLD_DEFAULT_LABELS:
        c.shortcuts = []
    c.validate()
    return c, None


def _scalar(s: str) -> Any:
    s = s.strip()
    if s in ("true", "false"):
        return s == "true"
    if s in ("null", "~", ""):
        return None
    if s == "[]":
        return []
    if s == "{}":
        return {}
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1].replace("''", "'") if s[0] == "'" else s[1:-1].encode().decode("unicode_escape")
    try:
        return int(s)
    except ValueError:
        return s


def _read_legacy_yaml(text: str) -> dict[str, Any]:
    """The small YAML subset Keypad 2.x wrote (nested mappings, scalars, a list of mappings),
    read without a YAML library."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any, str]] = [(-1, root, "")]  # indent, container, the key it was filed under
    for raw in text.splitlines():
        if raw.lstrip().startswith("#") or not raw.strip():
            continue
        line = raw.rstrip()
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        while len(stack) > 1 and indent <= stack[-1][0] and not (body.startswith("- ") and indent == stack[-1][0] and (isinstance(stack[-1][1], list) or not stack[-1][1])):
            stack.pop()
        _, parent, _ = stack[-1]
        if body.startswith("- "):
            if isinstance(parent, dict) and not parent and len(stack) > 1:  # "key:" was a list, not a mapping
                grand, key = stack[-2][1], stack[-1][2]
                parent = grand[key] = []
                stack[-1] = (stack[-1][0], parent, key)
            if not isinstance(parent, list):
                continue
            item_body = body[2:]
            k, sep, v = item_body.partition(":")
            if sep and not item_body.startswith(("'", '"')):
                item: dict[str, Any] = {k.strip(): _scalar(v)}
                parent.append(item)
                stack.append((indent + 1, item, ""))
            else:
                parent.append(_scalar(item_body))
            continue
        k, sep, v = body.partition(":")
        if not sep or not isinstance(parent, dict):
            continue
        if v.strip() == "":
            child: dict[str, Any] = {}
            parent[k.strip()] = child
            stack.append((indent, child, k.strip()))
        else:
            parent[k.strip()] = _scalar(v)
    return root


def _migrate_legacy(old: dict[str, Any]) -> dict[str, Any]:
    """Keypad 2.x settings as 3.x names."""
    b = dict(old.get("behavior") or {})
    if b.pop("ask_on_stop", True) is False:
        b["ask_when_finished"] = -1
    elif "stop_when_away" in b:
        b["ask_when_finished"] = b["stop_when_away"]
    b.pop("stop_when_away", None)
    b.pop("pc_handback", None)
    return {**old, "behavior": b}


def save(c: Config) -> None:
    """Validates c in place (clamping its values) and writes it."""
    c.validate()
    write_atomic(config_path(), json.dumps(c.to_dict(), indent=2, ensure_ascii=False) + "\n", 0o644)


def write_atomic(path: Path, text: str | bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    data = text.encode() if isinstance(text, str) else text
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


# ---- machine state ---------------------------------------------------------------


@dataclass
class Device:
    id: str
    name: str = ""
    key: str = ""  # pairing key (hex); empty = USB only
    brightness: int = 80
    last_ip: str = ""
    ssid: str = ""

    def view(self) -> dict[str, Any]:
        """For the tray and CLI: everything but the key."""
        d = asdict(self)
        d["key"] = ""
        d["paired"] = bool(self.key)
        return d


class Store:
    """Guards state.json."""

    def __init__(self, host_id: str, devices: list[Device]):
        self._lock = threading.Lock()
        self._host_id = host_id
        self._devices = devices

    @classmethod
    def open(cls) -> tuple[Store, Exception | None]:
        """Loads state.json. A damaged file is moved aside (so its pairing keys
        can be recovered by hand) and a fresh state started; the store is then
        returned with an error describing what happened."""
        p = state_path()
        warn: Exception | None = None
        host_id, devices = "", []
        if p.exists():
            try:
                st = json.loads(p.read_text(encoding="utf-8"))
                host_id = st.get("host_id", "")
                for d in st.get("devices") or []:
                    devices.append(Device(**{f.name: d[f.name] for f in fields(Device) if f.name in d}))
            except (ValueError, TypeError, KeyError) as e:
                aside = p.with_name(p.name + ".damaged-" + time.strftime("%Y%m%d-%H%M%S"))
                os.replace(p, aside)
                warn = RuntimeError(f"{p} was damaged ({e}); moved it to {aside} and started without paired keypads")
                host_id, devices = "", []
        s = cls(host_id, devices)
        if not host_id:
            s._host_id = "h-" + secrets.token_hex(4)
            s._save()
        return s, warn

    def host_id(self) -> str:
        with self._lock:
            return self._host_id

    def devices(self) -> list[Device]:
        with self._lock:
            return [copy.deepcopy(d) for d in self._devices]

    def device(self, dev_id: str) -> Device | None:
        with self._lock:
            for d in self._devices:
                if d.id == dev_id:
                    return copy.deepcopy(d)
        return None

    def update(self, dev_id: str, fn: Callable[[Device], None]) -> Device:
        """Creates or modifies a keypad record and persists it."""
        with self._lock:
            d = next((x for x in self._devices if x.id == dev_id), None)
            if d is None:
                d = Device(id=dev_id, name=dev_id)
                self._devices.append(d)
            fn(d)
            self._save()
            return copy.deepcopy(d)

    def remove(self, dev_id: str) -> None:
        with self._lock:
            self._devices = [d for d in self._devices if d.id != dev_id]
            self._save()

    def _save(self) -> None:
        st = {"host_id": self._host_id, "devices": [asdict(d) for d in self._devices]}
        write_atomic(state_path(), json.dumps(st, indent=2), 0o600)  # holds pairing keys
