"""Đọc / ghi config/settings.json (gộp với settings.example.json làm giá trị mặc định)."""
from __future__ import annotations

import copy
import json
from typing import Any

from .paths import SETTINGS_EXAMPLE, SETTINGS_FILE


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict[str, Any]:
    defaults = json.loads(SETTINGS_EXAMPLE.read_text(encoding="utf-8"))
    if SETTINGS_FILE.exists():
        try:
            user = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            user = {}
        return _deep_merge(defaults, user)
    return defaults


def save(data: dict[str, Any]) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


def get(path: str, default: Any = None) -> Any:
    cur: Any = load()
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


SECRET_KEYS = {"api_key"}


def redacted(data: dict[str, Any]) -> dict[str, Any]:
    """Bản sao dùng để ghi log: ẩn API key."""
    def walk(x):
        if isinstance(x, dict):
            return {k: ("***" if k in SECRET_KEYS and v else walk(v)) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(i) for i in x]
        return x
    return walk(data)
