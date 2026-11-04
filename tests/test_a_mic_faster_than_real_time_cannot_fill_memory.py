"""A mic that delivers faster than real time loses frames; it does not fill memory.

On 2026-11-03 a test mic read from /dev/zero ran about 5,000 times faster than real
time. Every frame was queued on the event loop with no limit, the voice detector
could not keep up, and the backlog grew by about 170 MB/s until the machine froze.
A typed request sat behind 158,000 waiting frames and was never answered.
"""
import asyncio
import threading
import time
import tracemalloc

import main


class _UI:
    muted = False
    assistant_name = "Aethelark"
    current_file = None

    def __getattr__(self, name):
        return lambda *a, **k: None


FRAMES = 20_000
FRAME_BYTES = main.CHUNK_SIZE * 2


def test_a_mic_faster_than_real_time_cannot_fill_memory():
    live = main.AethelarkLive(_UI())
    got: list[int] = []

    def detector(data: bytes) -> None:
        # The voice detector runs in onnxruntime, which lets go of the GIL, so the
        # audio thread keeps delivering while a frame is being scored.
        time.sleep(0.0005)
        got.append(int.from_bytes(data[:4], "little"))

    live._on_mic_frame = detector

    async def run():
        loop = asyncio.get_running_loop()

        def device():
            pad = bytes(FRAME_BYTES - 4)
            for i in range(FRAMES):
                live._mic_callback(loop, i.to_bytes(4, "little") + pad)

        tracemalloc.start()
        try:
            thread = threading.Thread(target=device)
            thread.start()
            worst_wait = 0.0
            while thread.is_alive():
                asked = time.monotonic()
                await asyncio.sleep(0)
                worst_wait = max(worst_wait, time.monotonic() - asked)
            thread.join()
            while live._mic_handoff or live._mic_drain_due:
                await asyncio.sleep(0)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        return worst_wait, peak

    worst_wait, peak = asyncio.run(run())

    # Every frame queued would be 20,000 x 2 KB = 40 MB.
    assert peak < 4 * 2**20, f"peak {peak / 2**20:.1f} MB while the mic flooded"
    # Other work on the loop (a typed request, Gemini's reply) still gets its turn.
    assert worst_wait < 0.25, f"the loop was busy for {worst_wait:.2f} s at a stretch"
    # The newest audio survives, in order, and every lost frame is counted.
    assert got == sorted(got) and got[-1] == FRAMES - 1
    assert live._mic_handoff_drops == FRAMES - len(got)


def test_a_real_time_mic_loses_nothing():
    live = main.AethelarkLive(_UI())
    got: list[int] = []
    live._on_mic_frame = lambda data: got.append(int.from_bytes(data[:4], "little"))

    async def run():
        loop = asyncio.get_running_loop()
        pad = bytes(FRAME_BYTES - 4)
        for i in range(50):
            live._mic_callback(loop, i.to_bytes(4, "little") + pad)
            await asyncio.sleep(0.002)
        while live._mic_handoff or live._mic_drain_due:
            await asyncio.sleep(0)

    asyncio.run(run())
    assert got == list(range(50))
    assert live._mic_handoff_drops == 0
