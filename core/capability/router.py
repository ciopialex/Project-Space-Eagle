"""Which of the three cases is this machine in, and what follows from it.

The precedence is the product decision, not an implementation detail:

    1. SUBSCRIPTION  a coding CLI is installed  -> delegate, HARDCORE swarm
    2. METERED       a per-token key is present -> the eagle works via SDKs
    3. LOCAL         a local model server is up -> local, with VRAM headroom

Flat-rate labour always beats metered labour. A user with Claude Code *and* an
OpenRouter key routes to Claude Code, because their subscription is already
paid for and their OpenRouter credit is not.

One rule holds across all three: the eagle's own hands — mouse, keyboard,
screen, messaging — are ALWAYS on. Claude Code cannot click a button in
Photoshop. The case only decides who does heavy repo-scoped code work.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SUBSCRIPTION = "subscription"
METERED = "metered"
LOCAL = "local"
BARE = "bare"

#: Human-facing names. The user hears these; the code branches on the constants.
CASE_LABELS = {
    SUBSCRIPTION: "Delegated",
    METERED:      "Metered",
    LOCAL:        "Local",
    BARE:         "Hands only",
}

#: The one number each case is asking the user to trust it with.
CASE_METRIC = {
    SUBSCRIPTION: "cost",      # and it is always $0.00
    METERED:      "spend",
    LOCAL:        "vram",
    BARE:         "quota",
}


@dataclass(frozen=True)
class Routing:
    case: str
    label: str
    metric: str
    labour: str                      # agent key, "native", or "none"
    brain: str                       # provider name driving text/tool work
    reason: str
    hands_enabled: bool = True       # always. never conditional.
    detail: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        return f"{self.label}: {self.reason}"


def decide(*, agents=None, keys=None, local_server: bool = False,
           local_model: str = "") -> Routing:
    """Resolve a capability profile into one routing decision.

    `agents` is an AgentRegistry (or None), `keys` a provider->origin mapping.
    """
    from core.capability.keys import metered_providers

    keys = keys or {}
    best = agents.best() if agents is not None else None
    metered = metered_providers(keys)
    has_gemini = "gemini" in keys

    # 1. Flat-rate labour wins whenever it exists.
    if best is not None:
        brain = "gemini" if has_gemini else (metered[0] if metered else "local")
        return Routing(
            case=SUBSCRIPTION,
            label=CASE_LABELS[SUBSCRIPTION],
            metric=CASE_METRIC[SUBSCRIPTION],
            labour=best.key,
            brain=brain,
            reason=(f"{best.key} is installed, so heavy work goes there "
                    f"instead of burning tokens"),
            detail={"agent_path": best.path,
                    "metered_available": metered},
        )

    # 2. A metered key means the eagle can do the work itself.
    if metered:
        provider = metered[0]
        return Routing(
            case=METERED,
            label=CASE_LABELS[METERED],
            metric=CASE_METRIC[METERED],
            labour="native",
            brain=provider,
            reason=(f"no coding CLI installed, so the eagle works directly "
                    f"through {provider}"),
            detail={"providers": metered, "origin": keys.get(provider, "")},
        )

    # 3. Local model, with headroom reserved for the user's own applications.
    if local_server:
        return Routing(
            case=LOCAL,
            label=CASE_LABELS[LOCAL],
            metric=CASE_METRIC[LOCAL],
            labour="native",
            brain="local",
            reason=("running entirely on this machine; nothing leaves it"),
            detail={"model": local_model},
        )

    # 4. Nothing to delegate to and nothing to pay with. The hands still work,
    #    which is the whole point of them being unconditional.
    return Routing(
        case=BARE,
        label=CASE_LABELS[BARE],
        metric=CASE_METRIC[BARE],
        labour="none",
        brain="gemini" if has_gemini else "none",
        reason=("no coding agent, no metered key and no local model — the "
                "eagle can still see, talk and drive this machine"),
        detail={},
    )
