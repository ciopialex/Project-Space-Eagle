"""Where a local model would be reached, for an audit that starts nothing.

This module once held a second inference path — an Ollama and
OpenAI-compatible client with streaming, warm-up and availability probes.
The product moved to Gemini Live and did not move back, and almost all of it
had no caller anywhere in the repository. One function did: an audit wants to
know where a local runtime *would* listen, without starting one, and that is
all that remains.

The old path is in `git log -- core/llm_client.py` if it is ever wanted. It
should be read as a reference and not restored: it was written against a
runtime that has moved on twice since.
"""
from __future__ import annotations

from core import user_paths

CONFIG_PATH = user_paths.api_keys_path()

#: Ollama's default. An OpenAI-compatible server generally accepts the same
#: shape on the same port, so one pair of defaults covers both.
_DEFAULT_URL = "http://localhost:11434"
_DEFAULT_MODEL = "llama3.2"


def get_llm_settings() -> tuple[str, str]:
    """`(base_url, model_name)` for a local LLM, from config or the defaults.

    The trailing slash is stripped here rather than at the join site, because
    there are several join sites and only one of this.
    """
    from config import get_config

    cfg = get_config()
    url = str(cfg.get("llm_url") or _DEFAULT_URL).rstrip("/")
    model = str(cfg.get("llm_model") or _DEFAULT_MODEL)
    return url, model
