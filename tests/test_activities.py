import json
import os

from core.activities import MAX_AGE_S, ActivityBoard
from core.ambient import AmbientWatcher


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _event(pct, state="active", alert=False):
    return {"event": "print_progress", "title": "CC2", "detail": f"{pct}%",
            "alert": alert,
            "activity": {"id": "CC2", "state": state, "leading": "CC2",
                         "trailing": f"{pct}%", "progress": pct / 100,
                         "stale_after_s": 60}}


def test_watcher_to_board_lifecycle(tmp_path):
    slot = tmp_path / "a3d.json"
    clock = Clock()
    board = ActivityBoard(clock=clock)
    watcher = AmbientWatcher({"a3d": slot})

    slot.write_text(json.dumps(_event(10)))
    events = watcher.poll()
    assert [board.apply(e.module, e.payload) for e in events] == ["started"]
    assert board.visible()[0]["trailing"] == "10%"

    clock.t += 30
    stamp = slot.stat().st_mtime_ns + 1_000_000
    os.utime(slot, ns=(stamp, stamp))
    assert watcher.poll() == []
    assert set(watcher.heard) == {"a3d"}
    board.heard("a3d", watcher.heard["a3d"])
    clock.t += 45
    assert board.visible()[0]["stale"] is False

    clock.t += 30
    watcher.poll()
    assert watcher.heard == {}
    assert board.visible()[0]["stale"] is True

    slot.write_text(json.dumps(_event(100, state="ended", alert=True)))
    events = watcher.poll()
    assert board.apply(events[0].module, events[0].payload) == "ended"
    assert board.visible() == []


def test_relevance_then_start_order_and_age_cap():
    clock = Clock()
    board = ActivityBoard(clock=clock)
    board.apply("a3d", {"activity": {"id": "A", "trailing": "1%"}})
    clock.t += 1
    board.apply("a3d", {"activity": {"id": "B", "trailing": "2%"}})
    clock.t += 1
    board.apply("swarm", {"activity": {"id": "site", "relevance": 90}})
    assert [v["key"] for v in board.visible()] == ["swarm:site", "a3d:A", "a3d:B"]
    clock.t += MAX_AGE_S + 5
    assert board.visible() == []


def test_rejects_out_of_contract_progress():
    board = ActivityBoard()
    board.apply("a3d", {"activity": {"id": "A", "progress": 42}})
    assert board.visible()[0]["progress"] is None


def test_list_of_activities_and_end_of_unseen_job_is_silent():
    board = ActivityBoard()
    both = {"printer": "CC1", "activity": [
        {"id": "CC1", "trailing": "10%"}, {"id": "CC2", "trailing": "70%"}]}
    assert board.apply("a3d", both) == "started"
    cards = {v["key"]: v["card"] for v in board.visible()}
    assert cards["a3d:CC1"]["printer"] == "CC1" and cards["a3d:CC2"] == {}
    assert board.apply("a3d", {"activity": {"id": "CC9", "state": "ended"}}) == "gone"
    assert board.apply("a3d", {"activity": {"id": "CC2", "state": "ended"}}) == "ended"
    assert [v["key"] for v in board.visible()] == ["a3d:CC1"]


def test_alert_id_is_said_once():
    board = ActivityBoard()
    frame = {"event": "first_layer_passed", "alert": "CC2:job:first_layer",
             "activity": {"id": "CC2"}}
    assert board.first_alert("a3d", frame) is True
    assert board.first_alert("a3d", frame) is False
    assert board.first_alert("a3d", dict(frame, alert="CC2:job:complete")) is True
