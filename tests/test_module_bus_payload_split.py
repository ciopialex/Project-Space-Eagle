"""Geometry reaches the island; the model never sees it.

A browse returns five candidates, each carrying a few hundred quantised
triangles as base64. That is what the Dynamic Island draws. It is useless to
the language model: roughly 15k tokens of numbers per browse, and five of them
overrun the bus's 16000-character message ceiling, so what the model would
actually receive is a truncated wall of base64 with the useful fields cut off
the end.

So a module marks UI-only fields by prefixing them with an underscore. The
message handed to the model has them removed; `data["result"]` keeps
everything. The rule lives at the point the payload is written rather than in
a manifest field, because a manifest listing field paths drifts from the
payload it describes and nothing catches it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus.bus import MAX_OUTPUT_CHARS, ModuleBus, model_view


def test_underscore_keys_are_dropped_at_every_depth():
    payload = {"query": "watch stand", "_preview": {"vertices": "AAAA"},
               "candidates": [{"title": "a", "_preview": {"vertices": "BBBB"}},
                              {"title": "b", "_preview": {"vertices": "CCCC"}}]}
    view = model_view(payload)
    assert "_preview" not in view
    assert all("_preview" not in c for c in view["candidates"])
    assert [c["title"] for c in view["candidates"]] == ["a", "b"]
    assert view["query"] == "watch stand"


def test_the_original_payload_is_not_mutated():
    """The island reads the same object afterwards; stripping must not be in place."""
    payload = {"_preview": {"vertices": "AAAA"}, "keep": 1}
    model_view(payload)
    assert "_preview" in payload


def test_non_dict_values_pass_through_unchanged():
    assert model_view([1, "a", None]) == [1, "a", None]
    assert model_view("plain") == "plain"
    assert model_view(7) == 7
    assert model_view(None) is None


def test_a_leading_underscore_only_matters_on_keys_not_values():
    assert model_view({"name": "_private"}) == {"name": "_private"}


def _manifest(tmp_path, output="json"):
    (tmp_path / "fake.toml").write_text(
        'key = "fake"\nbinary = "fake"\n'
        f'output = "{output}"\n'
        'description = "d"\n\n'
        '[[tools]]\nname = "deck"\ndescription = "d"\nargv = ["deck"]\n')
    return ModuleBus([tmp_path], which=lambda n: "/bin/true").discover()


def _fake_popen(monkeypatch, stdout, returncode=0):
    class FakeProc:
        def __init__(self):
            self.returncode = returncode
        def communicate(self, timeout=None):
            return stdout, ""
    monkeypatch.setattr("core.module_bus.bus.subprocess.Popen",
                        lambda *a, **k: FakeProc())


def test_a_real_sized_deck_stays_under_the_message_ceiling(tmp_path, monkeypatch):
    """The load-bearing case: five candidates of real preview size.

    Each preview is ~12.3 KB of base64, bounded by PREVIEW_TRIANGLES = 500.
    Five of them is ~61 KB against a 16000-character ceiling, so without the
    split the model's copy is truncated mid-base64 and the fields that matter
    — title, dimensions — fall off the end.
    """
    payload = {"query": "watch stand", "printer": "CC1", "candidates": [
        {"id": i, "title": f"model {i}", "dimensions": {"x": 10, "y": 20, "z": 30},
         "_preview": {"triangles": 500, "vertices": "V" * 12000}}
        for i in range(5)]}
    bus = _manifest(tmp_path)
    _fake_popen(monkeypatch, json.dumps(payload))

    tr = bus.invoke("fake_deck", {})
    assert tr.ok
    assert "V" * 100 not in tr.message, "geometry reached the model"
    assert len(tr.message) < MAX_OUTPUT_CHARS, "the model's copy is truncated"
    assert "truncated" not in tr.message

    spoken = json.loads(tr.message)
    assert [c["title"] for c in spoken["candidates"]] == [f"model {i}" for i in range(5)]
    assert spoken["candidates"][0]["dimensions"] == {"x": 10, "y": 20, "z": 30}

    kept = tr.data["result"]
    assert len(kept["candidates"][0]["_preview"]["vertices"]) == 12000


def test_the_island_copy_is_untouched_by_the_split(tmp_path, monkeypatch):
    payload = {"candidates": [{"title": "a", "_preview": {"vertices": "XYZ"}}]}
    bus = _manifest(tmp_path)
    _fake_popen(monkeypatch, json.dumps(payload))
    tr = bus.invoke("fake_deck", {})
    assert tr.data["result"] == payload


def test_a_payload_with_nothing_to_strip_is_unchanged(tmp_path, monkeypatch):
    payload = {"printer": "CC1", "state": "READY", "nozzle_temp": 25.0}
    bus = _manifest(tmp_path)
    _fake_popen(monkeypatch, json.dumps(payload))
    tr = bus.invoke("fake_deck", {})
    assert json.loads(tr.message) == payload


def test_a_text_module_is_not_reserialised(tmp_path, monkeypatch):
    """output = "text" hands stdout to the model verbatim; that must not change."""
    bus = _manifest(tmp_path, output="text")
    _fake_popen(monkeypatch, "  a rich table with box drawing  ")
    tr = bus.invoke("fake_deck", {})
    assert tr.message == "a rich table with box drawing"


def test_invalid_json_still_fails_rather_than_being_reserialised(tmp_path, monkeypatch):
    bus = _manifest(tmp_path)
    _fake_popen(monkeypatch, "not json at all")
    tr = bus.invoke("fake_deck", {})
    assert not tr.ok
    assert "did not emit valid JSON" in tr.message


# ---- argv rendering: a structure must reach the CLI as JSON, not a repr ----

@pytest.mark.parametrize("value,expected", [
    ([{"model_id": "1", "printer": "CC1"}], '[{"model_id": "1", "printer": "CC1"}]'),
    ({"a": 1}, '{"a": 1}'),
    ([], "[]"),
    (True, "true"),
    (False, "false"),
    (5, "5"),
    ("plain", "plain"),
])
def test_a_structure_renders_as_json_not_a_python_repr(value, expected):
    """str() on a list gives single quotes, which is not JSON.

    A model that passes `jobs` as an array rather than a string sent
    [{'model_id': '1'}] down the command line, the module's json.loads failed,
    and the user heard that failure AFTER approving it — with the repr read
    back to them as the thing they were approving.
    """
    from core.module_bus.manifest import _render
    assert _render(value) == expected


def test_a_rendered_structure_survives_a_round_trip():
    from core.module_bus.manifest import _render
    jobs = [{"model_id": "124686", "printer": "CC1", "title": "Watch Stand"}]
    assert json.loads(_render(jobs)) == jobs
