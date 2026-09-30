"""The fused picture of this machine, and where it is remembered.

Three freshness tiers, because a single onboarding snapshot goes stale the
moment the user installs something:

    onboarding  full scan, once, persisted           seconds
    launch      load the cache, refresh cheap parts  ~20ms
    runtime     demote a tool that just failed       free

Today `detect_machine()` is pushed straight into the onboarding HTML and
thrown away (aethelark_web.py:950), so the eagle renders a specs screen and
then knows nothing about the machine when you ask it for something hard. This
is where that result gets kept.
"""
from __future__ import annotations

import time
import json
import os
from pathlib import Path
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 1


@dataclass
class CapabilityProfile:
    """What the eagle knows about the machine it is running on."""
    schema: int = SCHEMA_VERSION
    scanned_at: float = 0.0
    hardware: dict[str, Any] = field(default_factory=dict)
    cli_agents: list[dict[str, Any]] = field(default_factory=list)
    gui_apps: list[str] = field(default_factory=list)
    providers: dict[str, str] = field(default_factory=dict)
    local_server: bool = False
    local_model: str = ""

    # ---- persistence ----------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, blob: Any) -> "CapabilityProfile":
        """Tolerant of anything. A corrupt cache must never block startup."""
        if not isinstance(blob, dict) or blob.get("schema") != SCHEMA_VERSION:
            return cls()
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in blob.items() if k in allowed})

    def is_stale(self, max_age: float = 86400.0,
                 now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return (current - self.scanned_at) > max_age

    # ---- convenience -----------------------------------------------------
    def registry(self):
        from core.capability.agents import Agent, AgentRegistry
        return AgentRegistry.from_discovery(
            Agent(**a) for a in self.cli_agents)

    def route(self):
        from core.capability.router import decide
        return decide(agents=self.registry(), keys=self.providers,
                      local_server=self.local_server,
                      local_model=self.local_model)


def profile_path():
    """Where the profile lives. Uses user_paths so a frozen build writes
    somewhere the user can actually write to."""
    from core import user_paths
    return user_paths.config_dir() / "capability.json"


def load(path=None) -> CapabilityProfile:
    """Last known profile, or an empty one. Never raises."""
    try:
        target = Path(path) if path is not None else profile_path()
        return CapabilityProfile.from_dict(json.loads(target.read_text("utf-8")))
    except Exception:
        return CapabilityProfile()


def save(profile: CapabilityProfile, path=None) -> bool:
    """Persist the profile. Returns whether it stuck. Never raises."""
    try:
        target = Path(path) if path is not None else profile_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(profile.to_dict(), indent=2), "utf-8")
        os.replace(tmp, target)
        return True
    except Exception:
        return False


def scan(*, full: bool = True, previous: CapabilityProfile | None = None,
         now: float | None = None) -> CapabilityProfile:
    """Build a profile of this machine.

    full=False skips the expensive hardware probe (nvidia-smi and lspci cost
    ~400ms together) and reuses whatever the previous scan found. That is the
    every-launch path; the full scan belongs to onboarding.
    """
    from core.capability import agents as agent_mod
    from core.capability import keys as key_mod

    prior = previous or CapabilityProfile()

    hardware = prior.hardware
    if full or not hardware:
        try:
            from actions.machine_profile import detect_machine
            hardware = detect_machine()
        except Exception:
            hardware = prior.hardware

    try:
        clis = [a.__dict__ for a in agent_mod.discover_clis()]
    except Exception:
        clis = prior.cli_agents

    try:
        gui = agent_mod.discover_gui_apps()
    except Exception:
        gui = prior.gui_apps

    try:
        providers = key_mod.discover()
    except Exception:
        providers = prior.providers

    local_up, local_model = _probe_local_server()

    return CapabilityProfile(
        scanned_at=time.time() if now is None else now,
        hardware=hardware,
        cli_agents=clis,
        gui_apps=gui,
        providers=providers,
        local_server=local_up,
        local_model=local_model,
    )


def _probe_local_server() -> tuple[bool, str]:
    """Is Ollama or an OpenAI-compatible server already running?

    Deliberately does NOT start one — `llm_client.ensure_ollama_running` will
    launch `ollama serve`, and an audit must never start a process the user
    didn't ask for.
    """
    try:
        import requests
        from core.llm_client import get_llm_settings
        try:
            url, model = get_llm_settings()
        except Exception:
            url, model = "http://localhost:11434", ""
        for suffix in ("/api/tags", "/v1/models"):
            try:
                r = requests.get(f"{url}{suffix}", timeout=1.5)
                if r.status_code == 200:
                    return True, model
            except Exception:
                continue
    except Exception:
        pass
    return False, ""
