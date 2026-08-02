from core.capability.agents import Agent, AgentRegistry, DEAD, LIVE, PRESENT
from core.capability.router import (BARE, LOCAL, METERED, SUBSCRIPTION, decide)


def _registry(*keys):
    return AgentRegistry.from_discovery(
        Agent(key=k, binary=k, status=PRESENT, path=f"/usr/bin/{k}")
        for k in keys)


# ---- the three cases ---------------------------------------------------

def test_installed_cli_routes_to_subscription():
    r = decide(agents=_registry("claude_code"), keys={"gemini": "env"})
    assert r.case == SUBSCRIPTION
    assert r.labour == "claude_code"
    assert r.brain == "gemini"


def test_metered_key_without_a_cli_routes_to_native_sdk_work():
    r = decide(agents=_registry(), keys={"openrouter": "env"})
    assert r.case == METERED
    assert r.labour == "native"
    assert r.brain == "openrouter"


def test_local_server_without_keys_or_clis_routes_local():
    r = decide(agents=_registry(), keys={}, local_server=True,
               local_model="gemma4")
    assert r.case == LOCAL
    assert r.brain == "local"
    assert r.detail["model"] == "gemma4"


# ---- the precedence rule the user specified ----------------------------

def test_flat_rate_labour_beats_a_metered_key():
    """Claude Code + OpenRouter routes to Claude Code. The subscription is
    already paid for; the OpenRouter credit is not."""
    r = decide(agents=_registry("claude_code"), keys={"openrouter": "env"})
    assert r.case == SUBSCRIPTION
    assert r.labour == "claude_code"
    assert "openrouter" in r.detail["metered_available"]


def test_a_cli_beats_a_local_server_too():
    r = decide(agents=_registry("opencode"), keys={"gemini": "env"},
               local_server=True, local_model="gemma4")
    assert r.case == SUBSCRIPTION


def test_metered_beats_local_when_no_cli_is_present():
    r = decide(agents=_registry(), keys={"anthropic": "env"},
               local_server=True)
    assert r.case == METERED


def test_cli_preference_order_is_honoured():
    r = decide(agents=_registry("opencode", "claude_code"), keys={})
    assert r.labour == "claude_code"


# ---- the case the original three-case model had no home for ------------

def test_free_gemini_key_and_nothing_else_is_still_a_valid_state():
    r = decide(agents=_registry(), keys={"gemini": "env"})
    assert r.case == BARE
    assert r.brain == "gemini"
    assert r.labour == "none"


def test_completely_empty_machine_still_routes():
    r = decide(agents=_registry(), keys={})
    assert r.case == BARE
    assert r.brain == "none"


# ---- the invariant ------------------------------------------------------

def test_hands_are_enabled_in_every_single_case():
    """Claude Code cannot click a button in Photoshop. The case decides who
    does heavy code work, never whether the eagle has hands."""
    cases = [
        decide(agents=_registry("claude_code"), keys={}),
        decide(agents=_registry(), keys={"openrouter": "env"}),
        decide(agents=_registry(), keys={}, local_server=True),
        decide(agents=_registry(), keys={}),
    ]
    assert {r.case for r in cases} == {SUBSCRIPTION, METERED, LOCAL, BARE}
    assert all(r.hands_enabled for r in cases)


def test_every_case_names_one_number_to_show_the_user():
    for r in [decide(agents=_registry("claude_code"), keys={}),
              decide(agents=_registry(), keys={"openai": "env"}),
              decide(agents=_registry(), keys={}, local_server=True),
              decide(agents=_registry(), keys={})]:
        assert r.metric
        assert r.describe()


# ---- tier 3: demotion at runtime ---------------------------------------

def test_a_dead_agent_is_skipped_and_routing_falls_through():
    reg = _registry("claude_code", "opencode")
    reg.mark("claude_code", DEAD)
    r = decide(agents=reg, keys={"gemini": "env"})
    assert r.labour == "opencode"


def test_all_agents_dead_falls_back_to_the_next_case():
    reg = _registry("claude_code")
    reg.mark("claude_code", DEAD)
    r = decide(agents=reg, keys={"openrouter": "env"})
    assert r.case == METERED


def test_marking_live_keeps_an_agent_usable():
    reg = _registry("claude_code")
    reg.mark("claude_code", LIVE)
    assert reg.best().status == LIVE
    assert decide(agents=reg, keys={}).case == SUBSCRIPTION


def test_marking_an_unknown_agent_is_a_no_op():
    reg = _registry("claude_code")
    reg.mark("nonexistent", DEAD)
    assert reg.best().key == "claude_code"
