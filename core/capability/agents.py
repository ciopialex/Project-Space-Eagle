"""What can already do work on this machine?

Two tiers, because presence is not availability. `shutil.which("claude")`
tells you a binary exists; it does not tell you the user is logged in or has
quota left. `agent_delegation.py:179` already carries the scar — "exited
immediately after launch".

So an agent is PRESENT when the binary resolves, and LIVE only once it has
actually done work. The router starts optimistic and demotes on failure.
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

PRESENT = "present"      # binary on PATH; may be logged out
LIVE = "live"            # has completed work this session
DEAD = "dead"            # tried and failed; do not route here again

#: CLI agents the eagle can delegate heavy work to, in preference order.
#: Order encodes the routing rule: flat-rate subscriptions before metered keys.
CLI_AGENTS: tuple[tuple[str, str], ...] = (
    ("claude_code",     "claude"),
    ("antigravity_cli", "agy"),
    ("opencode",        "opencode"),
    ("codex",           "codex"),
    ("copilot",         "copilot"),
    ("aider",           "aider"),
    ("kimi",            "kimi"),
)

#: Desktop applications worth knowing about, by platform.
_GUI_APPS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "linux": (
        ("cursor",         ("cursor",)),
        ("windsurf",       ("windsurf",)),
        ("lm_studio",      ("lm-studio", "lmstudio")),
        ("jan",            ("jan",)),
        ("claude_desktop", ("claude-desktop",)),
        ("antigravity",    ("antigravity",)),
    ),
    "darwin": (
        ("cursor",         ("Cursor.app",)),
        ("windsurf",       ("Windsurf.app",)),
        ("lm_studio",      ("LM Studio.app",)),
        ("jan",            ("Jan.app",)),
        ("claude_desktop", ("Claude.app",)),
        ("antigravity",    ("Antigravity.app",)),
    ),
    "win32": (
        ("cursor",         ("Cursor",)),
        ("windsurf",       ("Windsurf",)),
        ("lm_studio",      ("LM Studio",)),
        ("jan",            ("Jan",)),
        ("claude_desktop", ("Claude",)),
        ("antigravity",    ("Antigravity",)),
    ),
}


@dataclass(frozen=True)
class Agent:
    key: str
    binary: str
    status: str = PRESENT
    path: str = ""

    @property
    def usable(self) -> bool:
        return self.status in (PRESENT, LIVE)


def discover_clis(which: Callable[[str], str | None] = shutil.which,
                  agents: Iterable[tuple[str, str]] = CLI_AGENTS) -> list[Agent]:
    """CLI agents whose binary resolves on PATH, in preference order."""
    found: list[Agent] = []
    for key, binary in agents:
        try:
            resolved = which(binary)
        except Exception:
            resolved = None
        if resolved:
            found.append(Agent(key=key, binary=binary, status=PRESENT,
                               path=str(resolved)))
    return found


def _app_roots(platform: str) -> list[Path]:
    home = Path.home()
    if platform == "darwin":
        return [Path("/Applications"), home / "Applications"]
    if platform == "win32":
        import os
        roots = []
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            value = os.environ.get(var)
            if value:
                roots.append(Path(value))
        return roots
    return [
        Path("/usr/share/applications"),
        Path("/var/lib/flatpak/exports/share/applications"),
        home / ".local" / "share" / "applications",
        Path("/opt"),
    ]


def discover_gui_apps(platform: str | None = None,
                      roots: Iterable[Path] | None = None,
                      lister: Callable[[Path], Iterable[str]] | None = None,
                      ) -> list[str]:
    """Desktop AI applications installed on this machine.

    Name-matched against directory listings rather than launched, so this stays
    cheap and never starts anything the user didn't ask for.
    """
    plat = platform or sys.platform
    table = _GUI_APPS.get(plat if plat in _GUI_APPS else "linux", ())
    search_roots = list(roots) if roots is not None else _app_roots(plat)

    def _list(path: Path) -> Iterable[str]:
        try:
            return [entry.name for entry in Path(path).iterdir()]
        except Exception:
            return []

    ls = lister or _list

    entries: list[str] = []
    for root in search_roots:
        # An unreadable directory is normal — /opt and Program Files are often
        # permission-denied. One of them must never take the whole audit down.
        try:
            entries.extend(name.lower() for name in ls(root))
        except Exception:
            continue

    found: list[str] = []
    for key, needles in table:
        for needle in needles:
            if any(needle.lower() in entry for entry in entries):
                found.append(key)
                break
    return found


@dataclass
class AgentRegistry:
    """Live view of what can do work, mutable as things fail at runtime."""
    agents: dict[str, Agent] = field(default_factory=dict)

    @classmethod
    def from_discovery(cls, found: Iterable[Agent]) -> "AgentRegistry":
        return cls(agents={a.key: a for a in found})

    def usable(self) -> list[Agent]:
        """Agents worth routing to, in CLI_AGENTS preference order."""
        order = [key for key, _ in CLI_AGENTS]
        return [self.agents[k] for k in order
                if k in self.agents and self.agents[k].usable]

    def best(self) -> Agent | None:
        candidates = self.usable()
        return candidates[0] if candidates else None

    def mark(self, key: str, status: str) -> None:
        """Promote on success, demote on failure. This is tier 3 of the audit:
        a binary that was present at boot can be logged out by lunchtime."""
        current = self.agents.get(key)
        if current is None:
            return
        self.agents[key] = Agent(current.key, current.binary, status,
                                 current.path)
