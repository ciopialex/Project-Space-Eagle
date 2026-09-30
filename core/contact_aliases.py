from __future__ import annotations

import json
import os
import time

from core import user_paths
from core.contact_match import normalise


def _path():
    return user_paths.user_data_dir() / "contact_aliases.json"


def _all() -> dict:
    try:
        data = json.loads(_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load(platform: str) -> dict[str, str]:
    if not isinstance(platform, str):
        return {}
    table = _all().get(platform.lower())
    if not isinstance(table, dict):
        return {}
    return {k: v for k, v in table.items() if isinstance(k, str) and isinstance(v, str)}


def _quarantine(path) -> None:
    target = path.with_name(path.name + ".bad")
    if target.exists():
        target = path.with_name(f"{path.name}.bad.{int(time.time())}")
    try:
        path.rename(target)
    except OSError:
        pass


def learn(platform: str, spoken: str, title: str) -> bool:
    if not isinstance(platform, str) or not platform:
        return False
    if not isinstance(spoken, str) or not spoken:
        return False
    if not isinstance(title, str) or not title:
        return False
    key = normalise(spoken)
    if not key or normalise(title) == key:
        return True
    path = _path()
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError:
        data = {}
    except PermissionError:
        return False
    except Exception:
        _quarantine(path)
        data = {}
    else:
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("contact_aliases.json is not an object")
        except Exception:
            _quarantine(path)
            data = {}
    try:
        table = data.get(platform.lower())
        if not isinstance(table, dict):
            table = {}
        table[key] = title
        data[platform.lower()] = table
        tmp = path.with_name(path.name + ".tmp")
        user_paths.write_private(tmp, json.dumps(data, ensure_ascii=False, indent=1))
        os.replace(tmp, path)
        return True
    except Exception:
        return False
