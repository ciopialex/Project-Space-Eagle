from core.capability.identity import (CLI, LOCAL, SDK, backend_for_case,
                                      label_for, label_from_routing,
                                      prettify_model)
from core.capability.router import decide
from core.capability.agents import Agent, AgentRegistry, PRESENT


# ---- the three renderings ----------------------------------------------

def test_cli_backend_shows_the_real_product_name():
    assert label_for("claude_code", CLI) == "Claude Code"
    assert label_for("antigravity_cli", CLI) == "Antigravity CLI"
    assert label_for("antigravity_ide", CLI) == "Antigravity IDE"
    assert label_for("opencode", CLI) == "OpenCode"


def test_sdk_backend_never_claims_a_cli_that_is_not_running():
    """There is no subprocess, no terminal. Saying "CLI" would be a lie."""
    assert label_for("claude_code", SDK) == "Claude Agent"
    assert label_for("antigravity_cli", SDK) == "Antigravity Agent"
    assert "CLI" not in label_for("antigravity_ide", SDK)


def test_local_backend_names_the_model_doing_the_work():
    assert label_for("opencode", LOCAL, model="gemma4") == "Gemma4 Agent"
    assert label_for("opencode", LOCAL, model="qwen2.5:14b") == "Qwen2.5 Agent"


def test_local_without_a_model_falls_back_to_the_agent_identity():
    assert label_for("claude_code", LOCAL) == "Claude Agent"


# ---- robustness ----------------------------------------------------------

def test_unknown_agents_are_humanised_not_dropped():
    assert label_for("some_new_tool", CLI) == "Some New Tool"
    assert label_for("some_new_tool", SDK) == "Some New Tool Agent"


def test_empty_and_none_inputs_never_produce_an_empty_lane():
    assert label_for("", CLI) == "Agent"
    assert label_for(None, SDK) == "Agent"
    assert label_for("claude_code", None) == "Claude Code"


def test_unrecognised_backend_is_treated_as_sdk_not_cli():
    """Defaulting to a CLI name on an unknown backend risks the exact lie
    this module exists to prevent."""
    assert label_for("claude_code", "wormhole") == "Claude Agent"


def test_prettify_model_handles_the_common_shapes():
    assert prettify_model("gemma4:latest") == "Gemma4"
    assert prettify_model("library/llama3.1:8b") == "Llama3.1"
    assert prettify_model("") == ""
    assert prettify_model(None) == ""


# ---- wiring to the router ------------------------------------------------

def test_each_router_case_maps_to_the_right_backend():
    assert backend_for_case("subscription") == CLI
    assert backend_for_case("metered") == SDK
    assert backend_for_case("local") == LOCAL
    assert backend_for_case("bare") == CLI
    assert backend_for_case("nonsense") == CLI


def _registry(*keys):
    return AgentRegistry.from_discovery(
        Agent(key=k, binary=k, status=PRESENT, path=f"/usr/bin/{k}")
        for k in keys)


def test_subscription_routing_labels_the_cli():
    r = decide(agents=_registry("claude_code"), keys={"gemini": "env"})
    assert label_from_routing("claude_code", r) == "Claude Code"


def test_metered_routing_labels_the_sdk_agent():
    r = decide(agents=_registry(), keys={"openrouter": "env"})
    assert label_from_routing("claude_code", r) == "Claude Agent"


def test_local_routing_labels_the_resident_model():
    r = decide(agents=_registry(), keys={}, local_server=True,
               local_model="gemma4")
    assert label_from_routing("opencode", r) == "Gemma4 Agent"


def test_label_from_routing_survives_a_missing_routing():
    assert label_from_routing("claude_code", None) == "Claude Code"


# ---- the call site -------------------------------------------------------

def test_web_ui_no_longer_hardcodes_the_cli_label_map():
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "aethelark_web.py"
    text = src.read_text(encoding="utf-8")
    assert '"claude_code": "Claude Code"' not in text
    assert "label_from_routing" in text or "label_for" in text
