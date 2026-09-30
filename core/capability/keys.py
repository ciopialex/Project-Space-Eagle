"""Which model providers can this machine actually reach?

A human assistant doesn't ask you to write down credentials you've already
entered somewhere on the same laptop. Neither should the eagle: OpenCode keeps
its keys in `~/.local/share/opencode/auth.json`, the shells export the usual
environment variables, and the eagle's own config already holds a Gemini key.

Read-only. This module never writes, never transmits, and never logs a key —
only which providers are reachable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable, Iterable

#: provider -> environment variables that carry its key, most specific first.
ENV_VARS: dict[str, tuple[str, ...]] = {
    "gemini":     ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "anthropic":  ("ANTHROPIC_API_KEY",),
    "openai":     ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "perplexity": ("PERPLEXITY_API_KEY", "PPLX_API_KEY"),
    "groq":       ("GROQ_API_KEY",),
    "mistral":    ("MISTRAL_API_KEY",),
}

#: Providers billed per token. The router prefers flat-rate labour over these.
METERED = ("openrouter", "anthropic", "openai", "perplexity", "groq", "mistral")

#: Keys in the eagle's own config file, mapped to provider names.
_CONFIG_KEYS = {
    "gemini_api_key":     "gemini",
    "anthropic_api_key":  "anthropic",
    "openai_api_key":     "openai",
    "openrouter_api_key": "openrouter",
    "perplexity_api_key": "perplexity",
}


def _default_config_files() -> list[Path]:
    home = Path.home()
    return [
        home / ".local" / "share" / "opencode" / "auth.json",
        home / ".config" / "opencode" / "auth.json",
        home / ".aethelark" / "api_keys.json",
    ]


def _nonempty(value) -> bool:
    return isinstance(value, str) and len(value.strip()) > 6


def from_env(environ: dict | None = None) -> dict[str, str]:
    """Providers reachable via environment variables -> the var that supplied it."""
    env = os.environ if environ is None else environ
    found: dict[str, str] = {}
    for provider, names in ENV_VARS.items():
        for name in names:
            if _nonempty(env.get(name)):
                found[provider] = name
                break
    return found


def _scan_json(blob, found: dict[str, str], origin: str) -> None:
    """Pull provider names out of an arbitrary credentials blob.

    OpenCode stores {"anthropic": {"type": "api", "key": "..."}}; the eagle's
    own config uses flat "<provider>_api_key" names. Handle both without
    caring which is which.
    """
    if not isinstance(blob, dict):
        return
    for raw_key, value in blob.items():
        key = str(raw_key).strip().lower()
        provider = _CONFIG_KEYS.get(key)
        if provider is None and key in ENV_VARS:
            provider = key
        if provider is None:
            continue
        if _nonempty(value):
            found.setdefault(provider, origin)
        elif isinstance(value, dict) and any(_nonempty(v) for v in value.values()):
            found.setdefault(provider, origin)


def from_files(paths: Iterable[Path] | None = None,
               reader: Callable[[Path], str] | None = None) -> dict[str, str]:
    """Providers reachable via credential files already on this machine."""
    candidates = list(paths) if paths is not None else _default_config_files()

    def _read(p: Path) -> str:
        return Path(p).read_text(encoding="utf-8")

    read = reader or _read
    found: dict[str, str] = {}
    for path in candidates:
        try:
            _scan_json(json.loads(read(path)), found, str(path))
        except Exception:
            continue
    return found


def discover(environ: dict | None = None,
             paths: Iterable[Path] | None = None,
             reader: Callable[[Path], str] | None = None) -> dict[str, str]:
    """Every provider this machine can reach -> where the credential came from.

    Environment wins over files: an explicitly exported variable is a more
    deliberate statement of intent than a file another tool left behind.
    """
    found = dict(from_files(paths=paths, reader=reader))
    found.update(from_env(environ))
    return found


def metered_providers(found: dict[str, str]) -> list[str]:
    """Reachable providers that bill per token, in router preference order."""
    return [p for p in METERED if p in found]
