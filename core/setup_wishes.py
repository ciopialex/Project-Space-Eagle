"""What the user asked for during setup, to be done once the app is running.

The setup window closes before the app exists, so an answer such as "yes, I
have a 3D printer" is written down and picked up, once, by the app that opens
next.
"""
from __future__ import annotations

KEY = "wishes"


def add_wish(cfg: dict, wish: str) -> dict:
    cfg[KEY] = sorted(set(cfg.get(KEY) or []) | {wish})
    return cfg


def take_wish(cfg: dict, wish: str) -> bool:
    """True the first time `wish` is asked for, and never again."""
    wishes = set(cfg.get(KEY) or [])
    if wish not in wishes:
        return False
    wishes.discard(wish)
    cfg[KEY] = sorted(wishes)
    return True


INSTALL = "install:"


def take_installs(cfg: dict) -> list[str]:
    """The modules the user ticked in setup, each returned once and then forgotten."""
    wishes = list(cfg.get(KEY) or [])
    names = [w[len(INSTALL):] for w in wishes if w.startswith(INSTALL)]
    if names:
        cfg[KEY] = sorted(w for w in wishes if not w.startswith(INSTALL))
    return names
