"""The socket external domain modules plug into.

Three properties are worth more than everything else here, and each one is a
failure this codebase has already paid for somewhere else:

  1. **A module cannot take the eagle down.** It runs in its own process, on a
     timeout, and every failure path returns a `ToolResult` instead of raising.
     The turn loop runs tools on a shared executor — one module that blocks
     forever blocks a thread the voice loop needs.
  2. **Absent is reported, never guessed.** `alaw` and `atrade` are not
     installed on this machine. A bus that quietly dropped them would leave the
     model believing capabilities exist that do not, which is the same class of
     lie as a tool returning ok=True for work it did not do.
  3. **No invented structure.** `a3d` prints Rich tables, not JSON. Its output
     is handed to the model verbatim rather than run through a parser that
     would silently drift the first time a column moved — the whole point of
     leaving GUI scraping behind was to stop depending on someone else's
     rendering.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus import (  # noqa: E402
    ModuleBus, ModuleManifest, build_argv, load_manifest, load_manifests,
)

REPO = Path(__file__).resolve().parent.parent


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body))
    return path


A3D_LIKE = """
    key = "a3d"
    binary = "a3d"
    description = "Additive manufacturing: search, slice and print."
    output = "text"

    [[tools]]
    name = "search"
    description = "Search 3D models."
    argv = ["search", "{query}", "--limit={limit}"]

    [tools.params.query]
    type = "STRING"
    description = "What to search for."
    required = true

    [tools.params.limit]
    type = "INTEGER"
    description = "How many results."

    [[tools]]
    name = "printers"
    description = "List the configured printer fleet."
    argv = ["printers"]
"""


# ------------------------------------------------------------------- manifests

def test_a_manifest_declares_its_tools(tmp_path):
    manifest = load_manifest(_write(tmp_path, "a3d.toml", A3D_LIKE))
    assert manifest.key == "a3d"
    assert manifest.binary == "a3d"
    assert [t.name for t in manifest.tools] == ["search", "printers"]


def test_tool_names_are_namespaced_so_a_module_cannot_shadow_a_core_tool(tmp_path):
    """`main.py` already has 35 tools. A module called its command `search`;
    without a namespace that collides with `web_search`'s neighbours and the
    model picks whichever the dict happened to keep."""
    manifest = load_manifest(_write(tmp_path, "a3d.toml", A3D_LIKE))
    assert [t.qualified_name for t in manifest.tools] == ["a3d_search", "a3d_printers"]


def test_a_manifest_with_no_key_is_refused(tmp_path):
    with pytest.raises(ValueError):
        load_manifest(_write(tmp_path, "bad.toml", 'binary = "x"\n'))


def test_a_malformed_manifest_does_not_take_the_others_down(tmp_path):
    _write(tmp_path, "good.toml", A3D_LIKE)
    _write(tmp_path, "broken.toml", "this is not = valid toml [[[\n")
    manifests = load_manifests([tmp_path])
    assert [m.key for m in manifests] == ["a3d"]


def test_the_shipped_a3d_manifest_is_valid():
    """It ships in the repo, so a typo in it is a boot failure for everyone."""
    manifests = load_manifests([REPO / "tests" / "fixtures" / "module_bus" / "manifests"])
    keys = {m.key for m in manifests}
    assert "a3d" in keys
    a3d = next(m for m in manifests if m.key == "a3d")
    assert a3d.binary == "a3d"
    assert a3d.tools, "a3d manifest declares no tools"


# ----------------------------------------------------------------------- argv

def test_argv_is_built_from_the_template_and_the_arguments(tmp_path):
    manifest = load_manifest(_write(tmp_path, "a3d.toml", A3D_LIKE))
    tool = manifest.tool("search")
    assert build_argv(manifest, tool, {"query": "benchy", "limit": 5}) == [
        "a3d", "search", "benchy", "--limit=5"]


def test_an_unfilled_optional_drops_its_whole_argv_element(tmp_path):
    """`--limit={limit}` is one element on purpose. Split across two, an absent
    value leaves a bare `--limit` that eats the next argument."""
    manifest = load_manifest(_write(tmp_path, "a3d.toml", A3D_LIKE))
    tool = manifest.tool("search")
    assert build_argv(manifest, tool, {"query": "benchy"}) == [
        "a3d", "search", "benchy"]


def test_a_missing_required_argument_is_refused_before_spawning(tmp_path):
    manifest = load_manifest(_write(tmp_path, "a3d.toml", A3D_LIKE))
    tool = manifest.tool("search")
    with pytest.raises(ValueError, match="query"):
        build_argv(manifest, tool, {"limit": 5})


def test_arguments_are_argv_elements_and_never_touch_a_shell(tmp_path):
    manifest = load_manifest(_write(tmp_path, "a3d.toml", A3D_LIKE))
    tool = manifest.tool("search")
    argv = build_argv(manifest, tool, {"query": "benchy; rm -rf /"})
    assert argv == ["a3d", "search", "benchy; rm -rf /"]


# ------------------------------------------------------------------ discovery

def test_only_modules_whose_binary_resolves_are_available(tmp_path):
    _write(tmp_path, "a3d.toml", A3D_LIKE)
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: None)
    bus.discover()
    assert bus.available() == []
    assert [m.key for m in bus.absent()] == ["a3d"]


def test_a_module_whose_binary_exists_is_available(tmp_path):
    _write(tmp_path, "a3d.toml", A3D_LIKE)
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: "/usr/bin/" + b)
    bus.discover()
    assert [m.key for m in bus.available()] == ["a3d"]
    assert bus.absent() == []


def test_an_absent_module_contributes_no_tool_declarations(tmp_path):
    """The model must not be told it can print something when it cannot."""
    _write(tmp_path, "a3d.toml", A3D_LIKE)
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: None)
    bus.discover()
    assert bus.tool_declarations() == []


def test_declarations_match_the_shape_main_py_already_uses(tmp_path):
    _write(tmp_path, "a3d.toml", A3D_LIKE)
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: "/usr/bin/" + b)
    bus.discover()
    decls = bus.tool_declarations()

    assert [d["name"] for d in decls] == ["a3d_search", "a3d_printers"]
    search = decls[0]
    assert search["parameters"]["type"] == "OBJECT"
    assert search["parameters"]["properties"]["query"]["type"] == "STRING"
    assert search["parameters"]["required"] == ["query"]
    assert "Search 3D models." in search["description"]
    # No required key at all rather than an empty list: the same reason
    # ToolResult omits `ok` instead of claiming one.
    assert "required" not in decls[1]["parameters"]


def test_discovery_survives_a_directory_that_does_not_exist(tmp_path):
    bus = ModuleBus(manifest_dirs=[tmp_path / "nope"], which=lambda b: b)
    bus.discover()
    assert bus.available() == []


# ----------------------------------------------------------------- invocation

def _python_module(tmp_path: Path, body: str, *, output: str = "text") -> ModuleBus:
    """A manifest whose 'binary' is this interpreter running `body`.

    A real process with real pipes and a real exit code — the module boundary
    is a process boundary, so testing it with anything less tests nothing.
    """
    script = tmp_path / "mod.py"
    script.write_text(textwrap.dedent(body))
    _write(tmp_path, "m.toml", f"""
        key = "m"
        binary = "{sys.executable}"
        description = "test module"
        output = "{output}"

        [[tools]]
        name = "go"
        description = "does the thing"
        argv = ["{script}", "{{value}}"]

        [tools.params.value]
        type = "STRING"
        description = "anything"
    """)
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b)
    bus.discover()
    return bus


def test_a_successful_text_module_returns_its_output_verbatim(tmp_path):
    bus = _python_module(tmp_path, """
        import sys
        print("Printer  Status\\nCC1      idle")
    """)
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is True
    assert "CC1      idle" in result.message
    assert result.data["module"] == "m"


def test_a_json_module_returns_parsed_data(tmp_path):
    bus = _python_module(tmp_path, """
        import json
        print(json.dumps({"printers": [{"id": "CC1", "state": "idle"}]}))
    """, output="json")
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is True
    assert result.data["result"]["printers"][0]["id"] == "CC1"


def test_a_json_module_that_prints_junk_fails_honestly(tmp_path):
    """Declaring JSON and emitting a Rich table is the module's bug. The bus
    says so rather than handing the model a string it will treat as data."""
    bus = _python_module(tmp_path, """
        print("┏━━━ Printers ━━━┓")
    """, output="json")
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is False
    assert "json" in result.message.lower()


def test_a_nonzero_exit_is_a_failure_with_the_error_text(tmp_path):
    bus = _python_module(tmp_path, """
        import sys
        sys.stderr.write("no printer configured\\n")
        sys.exit(2)
    """)
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is False
    assert "no printer configured" in result.message
    assert result.data["exit_code"] == 2


def test_a_module_that_hangs_is_killed_and_reported(tmp_path):
    """The turn loop runs tools on a shared executor. A module that never
    returns holds one of its threads for as long as the process lives."""
    bus = _python_module(tmp_path, """
        import time
        time.sleep(30)
    """)
    result = bus.invoke("m_go", {"value": "x"}, timeout_s=1.0)
    assert result.ok is False
    assert "timed out" in result.message.lower()


def test_a_module_that_crashes_on_start_does_not_raise(tmp_path):
    _write(tmp_path, "m.toml", """
        key = "m"
        binary = "/definitely/not/a/binary/9f3a"
        description = "gone"
        [[tools]]
        name = "go"
        description = "x"
        argv = ["go"]
    """)
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b)
    bus.discover()
    result = bus.invoke("m_go", {})
    assert result.ok is False
    assert result.data["module"] == "m"


def test_an_unknown_tool_is_refused_without_spawning_anything(tmp_path):
    bus = _python_module(tmp_path, "print('hi')")
    result = bus.invoke("m_nonexistent", {})
    assert result.ok is False
    assert "m_nonexistent" in result.message


def test_a_missing_required_argument_becomes_a_failure_not_an_exception(tmp_path):
    _write(tmp_path, "m.toml", f"""
        key = "m"
        binary = "{sys.executable}"
        description = "x"
        [[tools]]
        name = "go"
        description = "x"
        argv = ["-c", "print(1)", "{{needed}}"]
        [tools.params.needed]
        type = "STRING"
        description = "x"
        required = true
    """)
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b)
    bus.discover()
    result = bus.invoke("m_go", {})
    assert result.ok is False
    assert "needed" in result.message


def test_output_is_bounded_so_a_chatty_module_cannot_flood_the_context(tmp_path):
    bus = _python_module(tmp_path, """
        print("x" * 200000)
    """)
    result = bus.invoke("m_go", {"value": "x"})
    assert result.ok is True
    assert len(result.message) < 20000


def test_invoking_an_absent_module_says_it_is_not_installed(tmp_path):
    _write(tmp_path, "a3d.toml", A3D_LIKE)
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: None)
    bus.discover()
    result = bus.invoke("a3d_search", {"query": "benchy"})
    assert result.ok is False
    assert "not installed" in result.message.lower()
    assert result.guidance


# ------------------------------------------------------------------- reporting

def test_the_bus_can_describe_what_it_found_for_the_doctor(tmp_path):
    _write(tmp_path, "a3d.toml", A3D_LIKE)
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: None)
    bus.discover()
    summary = bus.summary()
    assert "a3d" in summary
    assert "not installed" in summary.lower()
