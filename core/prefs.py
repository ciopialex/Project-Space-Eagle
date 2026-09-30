"""What the eagle does without being asked, decided in one place.

Every behaviour that speaks, pops up or listens on its own starts OFF. The
product is a voice that answers when spoken to; anything louder than that is a
choice the user makes in Settings, not one made for them.

Values live in the same config file as the API key (core.user_paths), so the
Settings panel, the onboarding flow and the runtime read one source.
"""
from __future__ import annotations

import json

from core import user_paths

#: name -> default. A key missing from the user's config gets this value.
DEFAULTS: dict[str, object] = {
    # Speaking first.
    "morning_brief_enabled": False,     # greeting + headlines at launch
    "proactive_enabled": False,         # check in after a long silence
    "system_alerts_enabled": False,     # "your CPU is at 95%"
    # Watching without being asked.
    "clipboard_assist_enabled": False,  # panel on every copy
    "island_live_activities": False,    # a running print stays on the island at rest
    "remote_dashboard_enabled": False,  # LAN web remote + phone mic
    # Tools a small voice model should not have to route around by default:
    # coding swarms, messaging, desktop automation, account integrations.
    "labs_tools_enabled": False,
    "coding_mode": False,
    # "" means: reply in whatever language the user speaks.
    "reply_language": "",
    # On, unlike everything above: it never acts on its own, only when the
    # user talks over the eagle. Off is for a room full of other voices.
    "barge_in_enabled": True,
    # Zero-latency acoustic keystroke shield. Blocks mechanical keyboard clicks
    # and typing sounds from reaching Gemini while the user is not speaking,
    # bursting pre-roll on speech onset for unclipped consonants.
    "mic_shield_enabled": True,
}


def _config() -> dict:
    try:
        return json.loads(user_paths.api_keys_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def get(name: str):
    """The user's value for `name`, or its default."""
    cfg = _config()
    if name in cfg and cfg[name] is not None:
        return cfg[name]
    return DEFAULTS.get(name)


def enabled(name: str) -> bool:
    return bool(get(name))


def snapshot() -> dict:
    """Every preference with its effective value, for the Settings panel."""
    cfg = _config()
    return {k: (cfg[k] if k in cfg and cfg[k] is not None else v)
            for k, v in DEFAULTS.items()}


def save(patch: dict) -> dict:
    """Merge known preferences into the config file; ignore anything else."""
    cfg = _config()
    for k, v in (patch or {}).items():
        if k in DEFAULTS:
            cfg[k] = v
    path = user_paths.api_keys_path()
    user_paths.write_private(path, json.dumps(cfg, indent=4))
    return snapshot()
