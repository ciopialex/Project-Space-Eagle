"""Where a local LLM would live, if one were still being called.

This module used to carry a complete second inference path — `call_llm`,
`call_llm_text`, `call_llm_stream`, `_stream_openai`, `warmup_model`,
`check_model_available`, `ensure_ollama_running` — for talking to Ollama or an
OpenAI-compatible local server. The product moved to Gemini Live and never came
back, and 520 of this file's 587 lines had no caller anywhere in the repo:
not in code, not in tests, not in the dashboard.

`ensure_ollama_running` looked live to an automated scan because another module
names it in a docstring. A mention is not a call.

What remains is the one thing something actually asks for: where a local model
would be reached, so `core.capability.profile` can look for one during an audit
without starting anything.

Restoring the removed path means writing it against whatever local runtime is
current, not resurrecting a version that predates the move — it is in git
history (`git log -- core/llm_client.py`) if it is ever wanted as a reference.
"""
from __future__ import annotations

import json

from core import user_paths

CONFIG_PATH = user_paths.api_keys_path()

#: Where a local runtime listens if the user has not said otherwise. Ollama's
#: default port; an OpenAI-compatible server usually accepts the same shape.
_DEFAULTS = {
    "llm_url":   "http://localhost:11434",
    "llm_model": "llama3.2",
}


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_llm_settings() -> tuple[str, str]:
    """(base_url, model_name) for a local LLM, from config or the defaults."""
    cfg = _load_config()
    url = str(cfg.get("llm_url") or _DEFAULTS["llm_url"]).rstrip("/")
    model = str(cfg.get("llm_model") or _DEFAULTS["llm_model"])
    return url, model
