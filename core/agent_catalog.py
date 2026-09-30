"""Coding-agent CLIs the eagle can launch and drive.

`launch` is the interactive argv; `None` marks where the first prompt goes.
An entry without a `None` is started empty and the prompt is typed into its
chat box once the screen settles. `stream` names a verified persistent JSON
protocol (core.agent_session), used for swarm workers.

Users add their own in <config dir>/agents.json:

    [{"key": "mycli", "label": "My CLI", "command": "mycli --flag",
      "aliases": ["my cli"]}]
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
from dataclasses import dataclass

from core import user_paths


@dataclass(frozen=True)
class AgentCLI:
    key: str
    label: str
    binary: str
    launch: tuple[str | None, ...]
    aliases: tuple[str, ...] = ()
    stream: str = ""

    @property
    def types_prompt(self) -> bool:
        return None not in self.launch

    def command(self, prompt: str = "") -> str:
        argv = [prompt if p is None else p for p in self.launch]
        return shlex.join(argv)

    def installed(self) -> bool:
        return shutil.which(self.binary) is not None


KNOWN: tuple[AgentCLI, ...] = (
    AgentCLI("claude_code", "Claude Code", "claude", ("claude", None),
             ("claude", "cloud", "clawed", "claude code"), stream="claude"),
    AgentCLI("antigravity_cli", "Antigravity", "agy", ("agy", "-i", None),
             ("antigravity", "anti-gravity", "agy", "anti gravity"), stream="agy"),
    AgentCLI("gemini_cli", "Gemini CLI", "gemini",
             ("env", "GEMINI_CLI_TRUST_WORKSPACE=true", "gemini", "-i", None),
             ("gemini", "gemini cli")),
    AgentCLI("codex", "Codex", "codex", ("codex", None), ("codex", "openai codex")),
    AgentCLI("opencode", "OpenCode", "opencode", ("opencode",),
             ("opencode", "open code", "open_code")),
    AgentCLI("hermes", "Hermes", "hermes", ("hermes",), ("hermes", "hermes agent")),
    AgentCLI("cursor_agent", "Cursor Agent", "cursor-agent", ("cursor-agent", None),
             ("cursor", "cursor agent")),
    AgentCLI("qwen_code", "Qwen Code", "qwen",
             ("env", "GEMINI_CLI_TRUST_WORKSPACE=true", "QWEN_CODE_TRUST_WORKSPACE=true", "qwen"),
             ("qwen", "qwen code")),
    AgentCLI("copilot", "Copilot CLI", "copilot", ("copilot",),
             ("copilot", "github copilot")),
    AgentCLI("aider", "Aider", "aider", ("aider",), ("aider",)),
    AgentCLI("goose", "Goose", "goose", ("goose", "session"), ("goose",)),
    AgentCLI("crush", "Crush", "crush", ("crush",), ("crush",)),
    AgentCLI("amp", "Amp", "amp", ("amp",), ("amp",)),
    AgentCLI("kimi", "Kimi", "kimi", ("kimi",), ("kimi",)),
)

PREFERENCE = ("claude_code", "antigravity_cli", "codex", "gemini_cli",
              "opencode", "cursor_agent", "hermes")


def _custom() -> list[AgentCLI]:
    try:
        raw = json.loads((user_paths.config_dir() / "agents.json").read_text("utf-8"))
    except Exception:
        return []
    out = []
    for item in raw if isinstance(raw, list) else []:
        try:
            argv = shlex.split(str(item["command"]))
            key = re.sub(r"\W+", "_", str(item.get("key") or argv[0])).strip("_").lower()
        except Exception:
            continue
        if not argv or not key:
            continue
        label = str(item.get("label") or argv[0])
        aliases = tuple(str(a).lower() for a in item.get("aliases") or ()) + (label.lower(),)
        out.append(AgentCLI(key, label, argv[0], tuple(argv), aliases))
    return out


def catalog() -> dict[str, AgentCLI]:
    table = {a.key: a for a in KNOWN}
    for a in _custom():
        table[a.key] = a
    return table


def installed() -> list[AgentCLI]:
    table = catalog()
    order = [k for k in PREFERENCE if k in table] + [k for k in table if k not in PREFERENCE]
    return [table[k] for k in order if table[k].installed()]


def resolve(name: str) -> str | None:
    """A registry key for whatever the user or the model called the agent."""
    wanted = re.sub(r"[\s\-]+", " ", (name or "").strip().lower())
    if not wanted:
        return None
    table = catalog()
    if wanted.replace(" ", "_") in table:
        return wanted.replace(" ", "_")
    for a in table.values():
        if wanted in a.aliases or wanted == a.label.lower() or wanted == a.binary:
            return a.key
    for a in table.values():
        if any(wanted.startswith(x) or x.startswith(wanted) for x in a.aliases if len(x) > 3):
            return a.key
    return None


def spoken_list() -> str:
    names = [a.label for a in installed()]
    return ", ".join(names) if names else "none"
