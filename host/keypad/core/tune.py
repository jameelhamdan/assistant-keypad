"""The keypad's model and effort sliders: what a project's next Claude Code
session starts with, kept in the project's .claude/settings.local.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import claudecfg
from ..config import write_atomic

MODELS = ["default", "fable", "opus", "sonnet", "haiku"]  # model aliases; "default" removes the setting
EFFORTS = ["default", "low", "medium", "high", "xhigh"]  # the levels settings.json accepts
KEYS = ("model", "effortLevel")
MAX_NAME = 11  # the keypad's slider labels


def local_path(cwd: str) -> Path:
    return Path(cwd) / ".claude" / "settings.local.json"


def _read(p: Path) -> dict[str, Any]:
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return m if isinstance(m, dict) else {}


def current(cwd: str) -> tuple[str, str]:
    """(model, effort) a new session in cwd starts with, as Claude Code resolves the files: local over
    project over user. "default" when none sets it."""
    files = [local_path(cwd), Path(cwd) / ".claude" / "settings.json", claudecfg.settings_path()]
    found = []
    for key in KEYS:
        v = next((m[key] for m in map(_read, files) if isinstance(m.get(key), str) and m[key]), "default")
        found.append(v)
    return found[0], found[1]


def view(cwd: str) -> dict[str, Any]:
    """The tune object of a status message; empty when the session has no project folder."""
    if not cwd or not Path(cwd).is_dir():
        return {}
    model, effort = current(cwd)
    models = MODELS + ([model[:MAX_NAME]] if model not in MODELS else [])  # a full model id set by hand stays listed
    efforts = EFFORTS + ([effort[:MAX_NAME]] if effort not in EFFORTS else [])
    return {"model": models, "effort": efforts,
            "m": models.index(model if model in MODELS else model[:MAX_NAME]),
            "e": efforts.index(effort if effort in EFFORTS else effort[:MAX_NAME])}


def choose(cwd: str, m: int, e: int) -> tuple[str, str]:
    """The model and effort the keypad's slider positions stand for. The extra slot of a value set by
    hand stands for that value, unchanged."""
    cur_m, cur_e = current(cwd)
    n_m = len(MODELS) + (cur_m not in MODELS)
    n_e = len(EFFORTS) + (cur_e not in EFFORTS)
    if not (0 <= m < n_m and 0 <= e < n_e):
        raise ValueError("slider position out of range")
    return (MODELS[m] if m < len(MODELS) else cur_m), (EFFORTS[e] if e < len(EFFORTS) else cur_e)


def save(cwd: str, model: str, effort: str) -> None:
    """Sets (or, for "default", removes) the two keys in the project's local settings. Raises
    ValueError when the existing file cannot be read, so it is never overwritten."""
    p = local_path(cwd)
    m: dict[str, Any] = {}
    if p.exists():
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"{p} is not valid JSON") from e
        if not isinstance(m, dict):
            raise ValueError(f"{p} is not a JSON object")
    for key, v in zip(KEYS, (model, effort), strict=True):
        if v == "default":
            m.pop(key, None)
        else:
            m[key] = v
    write_atomic(p, json.dumps(m, indent=2, ensure_ascii=False) + "\n", 0o644)
