import json

from core.capability import agents as agent_mod
from core.capability import keys as key_mod
from core.capability.agents import PRESENT
from core.capability.profile import CapabilityProfile, load, save


# ---- keys ---------------------------------------------------------------

def test_env_discovery_finds_each_provider():
    env = {"ANTHROPIC_API_KEY": "sk-ant-abcdefghij",
           "OPENROUTER_API_KEY": "sk-or-abcdefghij"}
    found = key_mod.from_env(env)
    assert found["anthropic"] == "ANTHROPIC_API_KEY"
    assert found["openrouter"] == "OPENROUTER_API_KEY"


def test_env_discovery_ignores_blank_and_stub_values():
    assert key_mod.from_env({"OPENAI_API_KEY": ""}) == {}
    assert key_mod.from_env({"OPENAI_API_KEY": "   "}) == {}
    assert key_mod.from_env({"OPENAI_API_KEY": "short"}) == {}


def test_env_falls_back_to_the_secondary_variable_name():
    assert key_mod.from_env({"GOOGLE_API_KEY": "AIza-abcdefghij"}) == {
        "gemini": "GOOGLE_API_KEY"}


def test_discovers_keys_opencode_already_stored(tmp_path):
    """A human assistant doesn't ask you to re-enter a credential you typed
    into another tool on the same laptop."""
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({
        "anthropic": {"type": "api", "key": "sk-ant-abcdefghij"},
        "openrouter": {"type": "api", "key": "sk-or-abcdefghij"},
    }))
    found = key_mod.from_files(paths=[auth])
    assert set(found) == {"anthropic", "openrouter"}
    assert str(auth) in found["anthropic"]


def test_flat_config_style_keys_are_discovered(tmp_path):
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": "AIza-abcdefghij",
                               "openai_api_key": ""}))
    found = key_mod.from_files(paths=[cfg])
    assert found == {"gemini": str(cfg)}


def test_missing_and_corrupt_files_are_skipped(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json at all")
    assert key_mod.from_files(paths=[bad, tmp_path / "nope.json"]) == {}


def test_environment_overrides_a_file(tmp_path):
    cfg = tmp_path / "auth.json"
    cfg.write_text(json.dumps({"anthropic": {"key": "sk-ant-fromfile1"}}))
    found = key_mod.discover(environ={"ANTHROPIC_API_KEY": "sk-ant-fromenv12"},
                             paths=[cfg])
    assert found["anthropic"] == "ANTHROPIC_API_KEY"


def test_metered_providers_excludes_gemini_and_keeps_order():
    found = {"gemini": "env", "openai": "env", "openrouter": "env"}
    assert key_mod.metered_providers(found) == ["openrouter", "openai"]


def test_no_key_material_is_ever_returned():
    """The audit reports reachability, never secrets."""
    found = key_mod.discover(environ={"ANTHROPIC_API_KEY": "sk-ant-SECRET123"},
                             paths=[])
    assert "SECRET" not in json.dumps(found)


# ---- CLI agents ----------------------------------------------------------

def test_cli_discovery_returns_preference_order():
    installed = {"opencode": "/usr/bin/opencode", "claude": "/usr/bin/claude"}
    found = agent_mod.discover_clis(which=installed.get)
    assert [a.key for a in found] == ["claude_code", "opencode"]
    assert found[0].status == PRESENT


def test_cli_discovery_covers_the_agents_the_spec_named():
    keys = {k for k, _ in agent_mod.CLI_AGENTS}
    assert {"claude_code", "antigravity_cli", "opencode",
            "codex", "copilot", "aider"} <= keys


def test_cli_discovery_survives_an_exploding_which():
    def boom(_):
        raise OSError("PATH is on fire")
    assert agent_mod.discover_clis(which=boom) == []


# ---- GUI apps ------------------------------------------------------------

def test_gui_discovery_matches_linux_desktop_entries(tmp_path):
    found = agent_mod.discover_gui_apps(
        platform="linux", roots=[tmp_path],
        lister=lambda p: ["cursor.desktop", "lm-studio.desktop", "gimp.desktop"])
    assert set(found) == {"cursor", "lm_studio"}


def test_gui_discovery_matches_mac_app_bundles(tmp_path):
    found = agent_mod.discover_gui_apps(
        platform="darwin", roots=[tmp_path],
        lister=lambda p: ["Cursor.app", "Claude.app", "Safari.app"])
    assert set(found) == {"cursor", "claude_desktop"}


def test_gui_discovery_survives_unreadable_directories(tmp_path):
    def boom(p):
        raise PermissionError("nope")
    assert agent_mod.discover_gui_apps(platform="linux", roots=[tmp_path],
                                       lister=boom) == []


# ---- persistence ---------------------------------------------------------

def test_profile_roundtrips_through_disk(tmp_path):
    path = tmp_path / "capability.json"
    p = CapabilityProfile(scanned_at=123.0, gui_apps=["cursor"],
                          providers={"gemini": "env"})
    assert save(p, path=path) is True
    back = load(path=path)
    assert back.gui_apps == ["cursor"]
    assert back.providers == {"gemini": "env"}
    assert back.scanned_at == 123.0


def test_missing_profile_loads_as_empty(tmp_path):
    assert load(path=tmp_path / "nothing.json").providers == {}


def test_corrupt_profile_never_blocks_startup(tmp_path):
    path = tmp_path / "capability.json"
    path.write_text("{{{ not json")
    assert load(path=path).providers == {}


def test_a_future_schema_is_discarded_rather_than_misread():
    assert CapabilityProfile.from_dict({"schema": 999,
                                        "providers": {"x": "y"}}).providers == {}


def test_unknown_fields_are_ignored():
    p = CapabilityProfile.from_dict(
        {"schema": 1, "providers": {"gemini": "env"}, "bogus": 1})
    assert p.providers == {"gemini": "env"}


def test_staleness_is_measured_against_the_scan_time():
    p = CapabilityProfile(scanned_at=1000.0)
    assert p.is_stale(max_age=100.0, now=1050.0) is False
    assert p.is_stale(max_age=100.0, now=1200.0) is True


def test_a_persisted_profile_can_route_without_rescanning():
    """The whole point: the eagle wakes up already knowing the machine."""
    p = CapabilityProfile(
        cli_agents=[{"key": "claude_code", "binary": "claude",
                     "status": "present", "path": "/usr/bin/claude"}],
        providers={"gemini": "env", "openrouter": "env"})
    r = p.route()
    assert r.case == "subscription"
    assert r.labour == "claude_code"


def test_onboarding_persists_the_scan_instead_of_discarding_it():
    """The gap this plan closes: detect_machine() used to be pushed to the
    onboarding HTML and thrown away, so the eagle knew nothing about the
    machine at task time."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "aethelark_web.py"
    text = src.read_text(encoding="utf-8")
    assert "from core.capability.profile import save, scan" in text
    assert "save(profile)" in text
    assert "setCapabilities" in text


def test_fast_rescan_reuses_hardware_and_skips_the_expensive_probe():
    """Tier 2: every launch must not pay for nvidia-smi and lspci again."""
    from core.capability.profile import CapabilityProfile, scan
    prior = CapabilityProfile(hardware={"ram_gb": 15.3, "marker": "reused"})
    fresh = scan(full=False, previous=prior, now=42.0)
    assert fresh.hardware["marker"] == "reused"
    assert fresh.scanned_at == 42.0
