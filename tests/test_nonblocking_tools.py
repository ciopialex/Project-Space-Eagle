"""The eagle must keep hearing while its hands are busy.

Run:  .venv/bin/python -m pytest tests/ -q

THE DEAFNESS THIS PINS DOWN
---------------------------
The tool batch was awaited *inside* `async for response in session.receive()`:

    if response.tool_call:
        fn_responses = await self._schedule_tool_calls(...)   # ← blocks the loop
        await session.send_tool_response(...)

So for the entire duration of any tool, nothing was pulled off the wire. Four
separate failures fell out of that one await:

  • Audio the server had already generated sat unread — the eagle went silent
    mid-task instead of narrating it.
  • `_last_server_activity` could not update, so the wedge watchdog counted a
    perfectly healthy long tool as a dead session and reconnected on top of it.
    send_message budgets 130s and swarm_mode 300s against a 25s stall window;
    this was not a rare race, it was the default outcome for slow tools.
  • A `go_away` frame could not be seen until the tool finished, so the graceful
    migration path was skipped and the hard 1011 it exists to prevent arrived
    instead.
  • Barge-in could not be honoured, because the loop that notices it was parked.

Dispatch is now fire-and-forget onto a tracked task. The receive loop returns to
the wire immediately, and the tool answers on its own time.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


class FakeCall:
    def __init__(self, name="slow_tool", cid="c1"):
        self.name = name
        self.id = cid
        self.args = {}


class FakeSession:
    """Records what the dispatcher sends back, so we can assert the reply
    actually lands rather than asserting on a mock's call log."""

    def __init__(self):
        self.responses = []

    async def send_tool_response(self, function_responses):
        self.responses.append(function_responses)


class Dispatcher:
    """The dispatch/tracking behaviour in isolation. AethelarkLive.__init__
    needs audio, a Qt UI and an API key; the scheduler itself has its own
    hazard tests. What is under test here is that dispatch does not block."""

    _dispatch_tool_calls    = main.AethelarkLive._dispatch_tool_calls
    _run_and_reply          = main.AethelarkLive._run_and_reply
    _session_is_wedged      = main.AethelarkLive._session_is_wedged
    _batch_needs_exclusion  = main.AethelarkLive._batch_needs_exclusion
    _cancel_inflight_tools  = main.AethelarkLive._cancel_inflight_tools
    # Borrowed too, and deliberately not stubbed out. `_run_and_reply` marks
    # the trace, and its broad `except Exception` turns anything missing here
    # into a silent "Tool dispatch failed" with no tool response sent — so a
    # double that fakes the tracing would hide exactly the class of breakage
    # this file exists to catch.
    _trace_mark             = main.AethelarkLive._trace_mark
    _trace                  = None

    def __init__(self, tool_duration=0.2, fail=False, durations=None):
        self._inflight_tools = set()
        self._tool_batch_lock = asyncio.Lock()
        self._tool_duration = tool_duration
        self._durations = durations or {}     # per-tool overrides
        self._fail = fail
        self.scheduled = []
        self.overlap = 0          # peak concurrent batches actually executing
        self._active = 0
        # wedge-predicate state; defaults describe a healthy idle session
        self._last_server_activity = 0.0
        self._last_user_speech = 0.0

    async def _schedule_tool_calls(self, tool_calls, call_epoch):
        self.scheduled.append((tuple(tool_calls), call_epoch))
        self._active += 1
        self.overlap = max(self.overlap, self._active)
        slowest = max(self._durations.get(c.name, self._tool_duration)
                      for c in tool_calls)
        try:
            await asyncio.sleep(slowest)
            if self._fail:
                raise RuntimeError("tool blew up")
            return [f"response-for-{c.id}" for c in tool_calls]
        finally:
            self._active -= 1


# ── the regression ──────────────────────────────────────────────────────────

def test_dispatch_returns_before_the_tool_has_finished():
    """THE regression. Dispatching a 200ms tool must cost the receive loop
    essentially nothing — that time is what used to be spent deaf."""
    async def scenario():
        d = Dispatcher(tool_duration=0.2)
        session = FakeSession()

        t0 = time.monotonic()
        d._dispatch_tool_calls([FakeCall()], call_epoch=1, session=session)
        elapsed = time.monotonic() - t0

        assert elapsed < 0.05, f"dispatch blocked for {elapsed:.3f}s"
        await asyncio.sleep(0.35)   # let it finish so we don't leak the task

    asyncio.run(scenario())


def test_the_reply_still_reaches_the_session_after_the_tool_finishes():
    """Non-blocking must not mean fire-and-forget-the-answer. The model is
    waiting on that tool response — losing it hangs the turn forever."""
    async def scenario():
        d = Dispatcher(tool_duration=0.05)
        session = FakeSession()

        d._dispatch_tool_calls([FakeCall(cid="abc")], call_epoch=7, session=session)
        assert session.responses == []          # not yet — the tool is still running
        await asyncio.sleep(0.25)

        assert session.responses == [["response-for-abc"]]

    asyncio.run(scenario())


def test_the_dispatch_epoch_is_carried_through_to_the_scheduler():
    """Barge-in correctness depends on the epoch captured at DISPATCH time. If
    the task read self._turn_epoch later it would see the post-interrupt value
    and stale results would be accepted as current."""
    async def scenario():
        d = Dispatcher(tool_duration=0.01)
        d._dispatch_tool_calls([FakeCall()], call_epoch=42, session=FakeSession())
        await asyncio.sleep(0.15)
        assert d.scheduled[0][1] == 42

    asyncio.run(scenario())


# ── task lifetime ───────────────────────────────────────────────────────────

def test_an_inflight_tool_is_tracked_and_released():
    """An untracked bare task can be garbage-collected mid-flight, and the
    watchdog needs to know work is happening. Tracked while running, released
    when done — a set that only grows is a leak."""
    async def scenario():
        d = Dispatcher(tool_duration=0.1)
        d._dispatch_tool_calls([FakeCall()], call_epoch=1, session=FakeSession())

        assert len(d._inflight_tools) == 1
        await asyncio.sleep(0.3)
        assert len(d._inflight_tools) == 0

    asyncio.run(scenario())


def test_a_failing_tool_still_releases_its_slot():
    """A tool that raises must not leave a phantom entry behind, or the
    watchdog is suppressed forever by work that already ended."""
    async def scenario():
        d = Dispatcher(tool_duration=0.02, fail=True)
        d._dispatch_tool_calls([FakeCall()], call_epoch=1, session=FakeSession())
        await asyncio.sleep(0.25)
        assert len(d._inflight_tools) == 0

    asyncio.run(scenario())


def test_concurrent_batches_are_tracked_independently():
    """Two tool batches can overlap now that dispatch does not serialise them."""
    async def scenario():
        d = Dispatcher(tool_duration=0.15)
        s = FakeSession()
        d._dispatch_tool_calls([FakeCall(cid="a")], call_epoch=1, session=s)
        d._dispatch_tool_calls([FakeCall(cid="b")], call_epoch=1, session=s)

        assert len(d._inflight_tools) == 2
        await asyncio.sleep(0.4)
        assert len(d._inflight_tools) == 0
        assert len(s.responses) == 2

    asyncio.run(scenario())


# ── the watchdog must not shoot the worker ──────────────────────────────────

def test_a_long_tool_is_not_mistaken_for_a_wedged_session():
    """THE second regression. A 300s swarm_mode against a 25s stall window used
    to guarantee a reconnect mid-task. Work in flight is proof of life."""
    d = Dispatcher()
    now = 1000.0
    d._last_server_activity = now - (main._TURN_STALL_S + 60)
    d._last_user_speech = now - 1.0

    assert d._session_is_wedged(now) is True    # no work in flight → genuinely wedged

    d._inflight_tools.add(object())             # a tool is running
    assert d._session_is_wedged(now) is False


# ── cross-batch safety (the guarantee the old blocking await gave for free) ──

def test_two_exclusive_batches_never_execute_at_the_same_time():
    """THE hazard introduced by going concurrent. The old blocking await made
    overlapping batches impossible, so the scoreboard only ever had to reason
    about hazards WITHIN one batch. Now that batches can overlap, two
    `desktop_control` calls could drive the mouse simultaneously. Exclusivity
    has to hold across batches or non-blocking dispatch is a downgrade."""
    async def scenario():
        d = Dispatcher(tool_duration=0.1)
        s = FakeSession()
        d._dispatch_tool_calls([FakeCall(name="desktop_control", cid="a")], 1, s)
        d._dispatch_tool_calls([FakeCall(name="desktop_control", cid="b")], 1, s)
        await asyncio.sleep(0.45)

        assert d.overlap == 1, f"{d.overlap} exclusive batches ran concurrently"
        assert len(s.responses) == 2      # serialised, but both still answered

    asyncio.run(scenario())


def test_an_unknown_tool_is_treated_as_exclusive():
    """TOOL_SPECS' default for an unregistered name is exclusive. A new tool
    must be safe before it is fast — opting into concurrency is a decision."""
    async def scenario():
        d = Dispatcher(tool_duration=0.1)
        s = FakeSession()
        d._dispatch_tool_calls([FakeCall(name="brand_new_tool", cid="a")], 1, s)
        d._dispatch_tool_calls([FakeCall(name="brand_new_tool", cid="b")], 1, s)
        await asyncio.sleep(0.45)
        assert d.overlap == 1

    asyncio.run(scenario())


def test_a_read_only_question_never_queues_behind_the_work_it_asks_about():
    """The property TOOL_SPECS documents in prose: swarm_status is read-only and
    deliberately not exclusive, because 'what are you building?' must answer
    while the building happens. A blunt batch-wide lock would break exactly the
    interaction non-blocking dispatch exists to enable."""
    async def scenario():
        # a long build, a quick question — so the ordering is observable
        d = Dispatcher(durations={"swarm_mode": 0.6, "swarm_status": 0.05})
        s = FakeSession()
        d._dispatch_tool_calls([FakeCall(name="swarm_mode", cid="build")], 1, s)
        await asyncio.sleep(0.02)
        d._dispatch_tool_calls([FakeCall(name="swarm_status", cid="ask")], 1, s)
        await asyncio.sleep(0.2)

        # the status answer landed while the build was still running
        assert s.responses == [["response-for-ask"]]
        assert d.overlap == 2, "the question waited for the work it asked about"
        await asyncio.sleep(0.6)

    asyncio.run(scenario())


def test_read_only_batches_run_fully_in_parallel():
    """Nothing read-only should ever serialise against anything."""
    async def scenario():
        d = Dispatcher(tool_duration=0.1)
        s = FakeSession()
        d._dispatch_tool_calls([FakeCall(name="system_status", cid="a")], 1, s)
        d._dispatch_tool_calls([FakeCall(name="swarm_status", cid="b")], 1, s)
        await asyncio.sleep(0.3)
        assert d.overlap == 2

    asyncio.run(scenario())


def test_a_writing_tool_is_excluded_even_without_the_exclusive_flag():
    """`file_controller` writes but is not flagged exclusive. Two concurrent
    batches writing files is the WAW hazard the scoreboard already refuses
    inside a batch; it must refuse it across batches too."""
    async def scenario():
        d = Dispatcher(tool_duration=0.1)
        s = FakeSession()
        d._dispatch_tool_calls([FakeCall(name="file_controller", cid="a")], 1, s)
        d._dispatch_tool_calls([FakeCall(name="file_controller", cid="b")], 1, s)
        await asyncio.sleep(0.45)
        assert d.overlap == 1

    asyncio.run(scenario())


# ── session teardown ────────────────────────────────────────────────────────

def test_inflight_tools_are_cancelled_when_the_session_ends():
    """Dispatch used to be awaited inside the receive loop, so tearing the
    TaskGroup down for a reconnect cancelled the running tool as a side effect.
    Backgrounding the task removes that, and a tool surviving its own session
    is a leak with consequences: it holds the batch lock, it keeps the wedge
    watchdog suppressed into the NEXT session, and it answers to a socket that
    no longer exists. Teardown must be explicit now that it is not implicit."""
    async def scenario():
        d = Dispatcher(tool_duration=5.0)
        s = FakeSession()
        d._dispatch_tool_calls([FakeCall()], 1, s)
        await asyncio.sleep(0.05)
        assert len(d._inflight_tools) == 1

        await d._cancel_inflight_tools()

        assert len(d._inflight_tools) == 0
        assert s.responses == []      # nowhere to answer — the session is gone

    asyncio.run(scenario())


def test_cancelling_with_nothing_in_flight_is_harmless():
    """The common case: a clean reconnect with no tool running."""
    async def scenario():
        d = Dispatcher()
        await d._cancel_inflight_tools()
        assert len(d._inflight_tools) == 0

    asyncio.run(scenario())


def test_the_batch_lock_is_released_when_a_tool_is_cancelled():
    """A cancelled exclusive tool must not leave the lock held, or every tool
    in the next session deadlocks behind a batch that no longer exists."""
    async def scenario():
        d = Dispatcher(tool_duration=5.0)
        d._dispatch_tool_calls([FakeCall(name="desktop_control")], 1, FakeSession())
        await asyncio.sleep(0.05)
        assert d._tool_batch_lock.locked()

        await d._cancel_inflight_tools()

        assert not d._tool_batch_lock.locked()

    asyncio.run(scenario())


def test_a_session_that_goes_quiet_after_the_tool_ends_is_still_caught():
    """Suppression must last exactly as long as the work does. If a finished
    tool kept the watchdog muted, the wedge recovery would be dead code."""
    d = Dispatcher()
    now = 1000.0
    d._last_server_activity = now - (main._TURN_STALL_S + 60)
    d._last_user_speech = now - 1.0

    sentinel = object()
    d._inflight_tools.add(sentinel)
    assert d._session_is_wedged(now) is False

    d._inflight_tools.discard(sentinel)
    assert d._session_is_wedged(now) is True
