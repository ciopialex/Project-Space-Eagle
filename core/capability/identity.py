"""What to call an agent, given how it is actually running.

The same logical agent renders differently depending on its execution backend.
Labelling an SDK-backed worker "Claude Code CLI" is a lie about what is on the
machine — there is no CLI, no subprocess, no terminal. The user reading a
HARDCORE lane should be able to trust that the name means what it says.

    backend   lane shows            because
    cli       Claude Code           a real `claude` subprocess is running
    sdk       Claude Agent          the eagle is calling the API itself
    local     Gemma Agent           a local model is doing the work

The mode badge and the spend counter carry whether money is moving, so the
lane label doesn't have to.
"""
from __future__ import annotations

CLI = "cli"
SDK = "sdk"
LOCAL = "local"

#: Exact product names, used only when the real CLI is running.
_CLI_NAMES: dict[str, str] = {
    "claude_code":     "Claude Code",
    "antigravity_cli": "Antigravity CLI",
    "antigravity_ide": "Antigravity IDE",
    "opencode":        "OpenCode",
    "codex":           "Codex CLI",
    "copilot":         "Copilot CLI",
    "aider":           "Aider",
    "kimi":            "Kimi",
}

#: The underlying identity, used when no CLI is involved.
_BASE_NAMES: dict[str, str] = {
    "claude_code":     "Claude",
    "antigravity_cli": "Antigravity",
    "antigravity_ide": "Antigravity",
    "opencode":        "OpenCode",
    "codex":           "Codex",
    "copilot":         "Copilot",
    "aider":           "Aider",
    "kimi":            "Kimi",
}

#: Router case -> execution backend.
_CASE_BACKEND = {
    "subscription": CLI,
    "metered":      SDK,
    "local":        LOCAL,
    "bare":         CLI,
}


def _humanise(key: str) -> str:
    return str(key or "").replace("_", " ").replace("-", " ").strip().title()


def prettify_model(model: str) -> str:
    """"gemma4:latest" -> "Gemma4". "qwen2.5:14b" -> "Qwen2.5"."""
    raw = str(model or "").strip()
    if not raw:
        return ""
    head = raw.split(":", 1)[0].split("/")[-1]
    return head[:1].upper() + head[1:] if head else ""


def backend_for_case(case: str) -> str:
    """Which execution backend a router case implies."""
    return _CASE_BACKEND.get(str(case or "").lower(), CLI)


def label_for(agent_key: str, backend: str = CLI, model: str = "") -> str:
    """Display name for an agent, honest about how it is running."""
    key = str(agent_key or "").strip().lower()
    mode = str(backend or CLI).strip().lower()

    if mode == CLI:
        return _CLI_NAMES.get(key) or _humanise(key) or "Agent"

    if mode == LOCAL:
        pretty = prettify_model(model)
        if pretty:
            return f"{pretty} Agent"
        # No model name to show — fall back to the agent's own identity rather
        # than inventing one.
        base = _BASE_NAMES.get(key) or _humanise(key)
        return f"{base} Agent" if base else "Agent"

    # sdk, and anything unrecognised: never claim a CLI that isn't there.
    base = _BASE_NAMES.get(key) or _humanise(key)
    return f"{base} Agent" if base else "Agent"


def label_from_routing(agent_key: str, routing) -> str:
    """Convenience: label an agent using a Routing from core.capability.router."""
    case = getattr(routing, "case", "") if routing is not None else ""
    model = ""
    detail = getattr(routing, "detail", None)
    if isinstance(detail, dict):
        model = detail.get("model", "") or ""
    return label_for(agent_key, backend_for_case(case), model)
