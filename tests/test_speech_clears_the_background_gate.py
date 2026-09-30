"""A turn the user did not ask for may look but not touch -- until they speak.

The gate that refuses tools on a proactive check or a system alert was lifted
by speech in exactly one place: the latency tracer's detector. AETHELARK_TRACE=0
switches that detector off, and with it off, one background wake-up left every
later spoken request refused ("nobody asked for this turn"). The always-on
detector that drives the island's hearing state lifts it now.
"""
import numpy as np

import main


class _UI:
    muted = False
    assistant_name = "Aethelark"
    current_file = None

    def __getattr__(self, name):
        return lambda *a, **k: None


def _frame(rms: float) -> bytes:
    if rms < 100:
        rng = np.random.default_rng(0)
        return (rng.standard_normal(main.CHUNK_SIZE) * rms).astype("<i2").tobytes()
    # Formant synthesized vowel (/a/ at F1=700Hz, F2=1200Hz, F0=130Hz)
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


def test_speech_lifts_the_gate_with_the_tracer_off(monkeypatch):
    monkeypatch.setenv("AETHELARK_TRACE", "0")
    live = main.AethelarkLive(_UI())
    assert live._vad is None            # the tracer really is off
    live._unprompted_turn = True        # a proactive check just ran
    for _ in range(live._hearing_vad.warmup_frames + 5):
        live._feed_hearing(_frame(40))  # a quiet room teaches the floor
    for _ in range(10):
        live._feed_hearing(_frame(3000))  # the user starts talking
    assert live._unprompted_turn is False
