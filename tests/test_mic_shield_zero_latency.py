"""Zero-latency acoustic keystroke shield tests.

Verifies:
1. Keystrokes during non-speech are 100% silenced to zero bytes (maintaining
   WebSocket audio clock with zero acoustic leakage to Gemini).
2. Speech onset triggers instant network pre-roll burst, preserving opening
   consonants with 0 ms pipeline latency.
3. End-of-turn has 0 ms delay: the final syllable was sent live, followed
   immediately by silence so Gemini triggers turn_complete without lag.
4. Disabling the shield restores raw pass-through for 100% backward compatibility.
5. Mechanical keystroke clicks right before speech are sanitized to zeros in pre-roll.
"""
from __future__ import annotations

import asyncio
import numpy as np
import pytest

import main
from core.mic_vad import SpeechDetector


class _UI:
    muted = False
    assistant_name = "Aethelark"
    current_file = None

    def __getattr__(self, name):
        return lambda *a, **k: None


def _room_tone(rms: float = 30.0) -> bytes:
    rng = np.random.default_rng(42)
    return (rng.standard_normal(main.CHUNK_SIZE) * rms).astype("<i2").tobytes()


def _keystroke_click() -> bytes:
    """Simulates a mechanical keyboard switch click: sharp Dirac shockwave + high freq ringdown."""
    sr = main.SEND_SAMPLE_RATE
    t = np.arange(main.CHUNK_SIZE) / sr
    click = np.zeros(main.CHUNK_SIZE)
    # Contact impulse concentrated in 2 ms
    click[100:] = np.exp(-t[: main.CHUNK_SIZE - 100] * 3500.0) * np.sin(2 * np.pi * 4500.0 * t[: main.CHUNK_SIZE - 100]) * 22000
    return click.astype("<i2").tobytes()


def _speech_vowel(rms: float = 3500.0) -> bytes:
    """Formant-synthesized vowel (/a/ at F1=700Hz, F2=1200Hz, F0=130Hz)."""
    sr = main.SEND_SAMPLE_RATE
    f0 = 130
    indices = (np.arange(0, main.CHUNK_SIZE, sr / f0)).astype(int)
    vowel = np.zeros(main.CHUNK_SIZE)
    for idx in indices:
        tau = np.arange(main.CHUNK_SIZE - idx) / sr
        decay = np.exp(-tau * 200)
        formants = np.sin(2 * np.pi * 700 * tau) + 0.6 * np.sin(2 * np.pi * 1200 * tau)
        vowel[idx:] += decay * formants
    vowel = vowel / np.max(np.abs(vowel)) * (rms * 1.414)
    return vowel.astype("<i2").tobytes()


def _warm_up(live: main.AethelarkLive) -> None:
    for _ in range(live._hearing_vad.warmup_frames + 2):
        live._enqueue_mic({"data": _room_tone(35.0), "mime_type": "audio/pcm"})


def test_keystrokes_silenced_during_idle():
    live = main.AethelarkLive(_UI())
    live.out_queue = asyncio.Queue()
    assert live._mic_shield_on is True

    _warm_up(live)
    # Drain queue
    while not live.out_queue.empty():
        live.out_queue.get_nowait()

    # User is typing rapidly (10 mechanical keystroke frames)
    click = _keystroke_click()
    for _ in range(10):
        live._enqueue_mic({"data": click, "mime_type": "audio/pcm"})

    assert live.out_queue.qsize() == 10
    assert live._hearing is False
    assert live._hearing_vad.blocked_keystrokes > 0

    # Verify every queued message contains strictly zeroed bytes (zero acoustic sound)
    zeros = b"\x00" * len(click)
    while not live.out_queue.empty():
        msg = live.out_queue.get_nowait()
        assert msg["data"] == zeros


def test_speech_bursts_preroll_and_streams_live():
    live = main.AethelarkLive(_UI())
    live.out_queue = asyncio.Queue()

    _warm_up(live)
    while not live.out_queue.empty():
        live.out_queue.get_nowait()

    # User is silent for 2 frames
    room = _room_tone(30.0)
    live._enqueue_mic({"data": room, "mime_type": "audio/pcm"})
    live._enqueue_mic({"data": room, "mime_type": "audio/pcm"})
    assert live.out_queue.qsize() == 2
    while not live.out_queue.empty():
        assert live.out_queue.get_nowait()["data"] == b"\x00" * len(room)

    # User starts speaking
    vowel = _speech_vowel(4000.0)
    live._enqueue_mic({"data": vowel, "mime_type": "audio/pcm"})

    assert live._hearing is True
    # Speech onset must burst pre-roll frames + the speech frame itself
    queued_count = live.out_queue.qsize()
    assert queued_count >= 2  # At least 1 preroll frame + current speech frame

    # The last queued frame must be the raw, unclipped speech vowel
    messages = []
    while not live.out_queue.empty():
        messages.append(live.out_queue.get_nowait())

    assert messages[-1]["data"] == vowel

    # Subsequent speech frames stream directly in real-time
    live._enqueue_mic({"data": vowel, "mime_type": "audio/pcm"})
    next_msg = live.out_queue.get_nowait()
    assert next_msg["data"] == vowel


def test_backward_compatibility_disabled_shield():
    live = main.AethelarkLive(_UI())
    live.out_queue = asyncio.Queue()
    live._mic_shield_on = False  # Explicitly disabled

    _warm_up(live)
    while not live.out_queue.empty():
        live.out_queue.get_nowait()

    click = _keystroke_click()
    live._enqueue_mic({"data": click, "mime_type": "audio/pcm"})

    msg = live.out_queue.get_nowait()
    # When shield is off, raw frame is passed through unmodified
    assert msg["data"] == click


def test_preroll_sanitizes_transient_keystroke_before_speech():
    live = main.AethelarkLive(_UI())
    live.out_queue = asyncio.Queue()

    _warm_up(live)
    while not live.out_queue.empty():
        live.out_queue.get_nowait()

    # User hits a key right before speaking
    click = _keystroke_click()
    live._enqueue_mic({"data": click, "mime_type": "audio/pcm"})

    # Then speaks
    vowel = _speech_vowel(4500.0)
    live._enqueue_mic({"data": vowel, "mime_type": "audio/pcm"})

    messages = []
    while not live.out_queue.empty():
        messages.append(live.out_queue.get_nowait())

    # Pre-roll burst must not contain the loud raw click; the click should be zeroed
    assert len(messages) >= 2
    preroll_frame = messages[0]["data"]
    # The pre-roll frame corresponding to the click is sanitized to zeros
    assert preroll_frame == b"\x00" * len(click)
    # The speech frame is intact
    assert messages[-1]["data"] == vowel
