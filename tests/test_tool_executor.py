"""Tools must not be able to starve the rest of the app of threads.

Run:  .venv/bin/python -m pytest tests/ -q

THE STARVATION THIS PINS DOWN
-----------------------------
Every tool ran on the loop's DEFAULT executor:

    r = await loop.run_in_executor(None, lambda: some_tool(...))

That pool is min(32, cpu+4) workers — 16 on this machine — and it is not the
tools' private property. `asyncio.to_thread` uses it too, which in this app
means `load_memory` on the proactive path, the dashboard's swarm snapshots,
and the briefing's news fetch. Enough concurrently slow tools and those stop
running: not slowly, not with an error, just never.

Giving tools their own bounded pool means a pile-up of slow tools degrades
tools only. The rest of the eagle keeps breathing.

Note this is the *containment*; core/run_cmd.py is the cure. A tool thread can
no longer hang forever now that every command has a deadline, so the pool
drains on its own. Containment still matters because a tool can be legitimately
slow — send_message budgets 130s, swarm_mode 300s — without being broken.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


def test_the_tool_pool_is_bounded():
    """An unbounded pool is just a slower way to run out of memory."""
    assert 0 < main._TOOL_WORKERS <= 16


def test_the_tool_pool_is_not_the_loops_default_pool():
    """THE regression, stated structurally: tools own their threads, so
    saturating them cannot reach anything else."""
    ex = main._make_tool_executor()
    try:
        assert isinstance(ex, ThreadPoolExecutor)

        async def scenario():
            loop = asyncio.get_running_loop()
            # asyncio.to_thread goes to the loop default; a tool goes to `ex`.
            # Those must be different objects or the isolation is imaginary.
            default_thread = await asyncio.to_thread(threading.current_thread)
            tool_thread = await loop.run_in_executor(ex, threading.current_thread)
            assert default_thread is not tool_thread
            assert "aethelark-tool" in tool_thread.name

        asyncio.run(scenario())
    finally:
        ex.shutdown(wait=False, cancel_futures=True)


def test_a_saturated_tool_pool_does_not_block_the_rest_of_the_app():
    """THE behaviour that matters. Every tool slot is occupied by something
    slow; internal background work must still get a thread promptly."""
    ex = main._make_tool_executor()
    release = threading.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        # jam every tool slot, and queue extra beyond capacity for good measure
        jammed = [
            loop.run_in_executor(ex, release.wait)
            for _ in range(main._TOOL_WORKERS * 2)
        ]
        await asyncio.sleep(0.1)

        t0 = time.monotonic()
        result = await asyncio.wait_for(asyncio.to_thread(lambda: "alive"), timeout=5)
        elapsed = time.monotonic() - t0

        assert result == "alive"
        assert elapsed < 1.0, f"internal work waited {elapsed:.2f}s behind tools"

        release.set()
        await asyncio.gather(*jammed)

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        ex.shutdown(wait=False, cancel_futures=True)


def test_shutdown_drops_queued_work_instead_of_waiting_for_it():
    """Quitting must not mean waiting out a backlog of slow tools. Work that
    has not started yet is abandoned; that is the whole point of quitting."""
    ex = main._make_tool_executor()
    release = threading.Event()
    started = threading.Event()

    def _block():
        started.set()
        release.wait(timeout=30)

    running = [ex.submit(_block) for _ in range(main._TOOL_WORKERS)]
    queued = [ex.submit(_block) for _ in range(50)]
    started.wait(timeout=5)

    t0 = time.monotonic()
    main._shutdown_tool_executor(ex)
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0, f"shutdown blocked for {elapsed:.2f}s"
    assert any(f.cancelled() for f in queued), "queued work was not abandoned"

    release.set()
    for f in running:
        f.cancel()
