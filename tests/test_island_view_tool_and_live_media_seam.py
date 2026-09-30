"""The two seams the island's views add: the voice tool answers honestly about
what it opened, and the bus runs a module's declared live tool with the
subject and on/off, returning the URL it streams at."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from core.module_bus import ModuleBus  # noqa: E402


class _UI:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def island_view(self, stage, target):
        self.calls.append((stage, target))
        return self.answer


def test_opening_something_running_says_so():
    ui = _UI(True)
    r = main.island_view(ui, "details", "Centauri Carbon")
    assert r.ok and ui.calls == [("expanded", "Centauri Carbon")]


def test_nothing_running_is_a_failure_that_says_what_is():
    r = main.island_view(_UI(["Centauri Carbon 2"]), "summary", "Neptune")
    assert not r.ok and "Centauri Carbon 2" in r.guidance


def test_nothing_at_all_is_a_failure():
    r = main.island_view(_UI([]), "details", "")
    assert not r.ok and "nothing" in r.guidance.lower()


def test_an_unknown_view_is_refused_with_the_three_that_exist():
    r = main.island_view(_UI(True), "zoom", "")
    assert not r.ok and "summary, details or close" in r.guidance


MANIFEST = """
    key = "cam"
    binary = "{binary}"
    description = "test module"
    output = "json"

    [island]
    about = "printer_key"
    live = "camera"
    live_param = "printer"

    [[tools]]
    name = "camera"
    description = "switch the camera"
    internal = true
    argv = ["-c", "import sys,json; a=sys.argv[1:]; print(json.dumps({{'url': 'http://' + a[0] + '/video' if a[1] == 'on' else ''}}))", "{{printer}}", "{{state}}"]

    [tools.params.printer]
    type = "STRING"
    description = "printer"
    required = true

    [tools.params.state]
    type = "STRING"
    description = "on or off"
    required = true
"""


def test_the_bus_runs_the_declared_live_tool_with_subject_and_state(tmp_path):
    (tmp_path / "cam.toml").write_text(textwrap.dedent(MANIFEST).format(binary=sys.executable))
    bus = ModuleBus(manifest_dirs=[tmp_path], which=lambda b: b)
    bus.discover()
    assert bus.live_media("cam", "10.0.0.6", True) == "http://10.0.0.6/video"
    assert bus.live_media("cam", "10.0.0.6", False) == ""
    assert bus.live_media("nope", "x", True) == ""
