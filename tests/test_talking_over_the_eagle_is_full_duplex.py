"""Talking over the eagle, like a phone call. Where its own voice cannot reach the
mic (echo cancellation, or headphones), the mic keeps streaming to Gemini while it
speaks and Gemini stops itself when the user cuts in. Elsewhere the mic stays shut
while it speaks and the local detector decides, or the eagle would hear itself and
stop mid-sentence."""
import main
from core import prefs
from core.barge_in import full_duplex


class _UI:
    muted = False
    assistant_name = "Aethelark"
    current_file = None

    def __getattr__(self, name):
        return lambda *a, **k: None


FRAME = b"\x10\x00" * main.CHUNK_SIZE


def _speaking_eagle(monkeypatch, duplex_ready, talk_over=True):
    monkeypatch.setattr(prefs, "get", lambda name: {"barge_in_enabled": talk_over}.get(
        name, prefs.DEFAULTS.get(name)))
    live = main.AethelarkLive(_UI())
    live._is_speaking = True
    live._duplex_ready = duplex_ready
    sent, local = [], []
    live._enqueue_mic = lambda msg: sent.append(msg["data"])
    live._barge.feed = lambda *a, **k: local.append(1) or False
    return live, sent, local


def test_with_its_voice_kept_out_the_mic_streams_while_it_speaks(monkeypatch):
    live, sent, local = _speaking_eagle(monkeypatch, duplex_ready=True)
    for _ in range(5):
        live._on_mic_frame(FRAME)
    assert sent == [FRAME] * 5


def test_in_full_duplex_a_clear_voice_also_stops_it_locally(monkeypatch):
    live, sent, local = _speaking_eagle(monkeypatch, duplex_ready=True)
    live._barge.feed = lambda *a, **k: True
    stopped = []
    live.interrupt = lambda: stopped.append(1)
    live._on_mic_frame(FRAME)
    assert stopped and sent == [FRAME], "stopped, and the frame went to Gemini once"


def test_without_it_the_mic_is_shut_and_the_local_detector_listens(monkeypatch):
    live, sent, local = _speaking_eagle(monkeypatch, duplex_ready=False)
    for _ in range(5):
        live._on_mic_frame(FRAME)
    assert sent == [] and len(local) == 5


def test_talk_over_switched_off_keeps_the_mic_shut(monkeypatch):
    live, sent, local = _speaking_eagle(monkeypatch, duplex_ready=True, talk_over=False)
    for _ in range(5):
        live._on_mic_frame(FRAME)
    assert sent == [] and not local


def test_auto_follows_echo_cancellation():
    assert full_duplex("auto", lambda: True) is True
    assert full_duplex("auto", lambda: False) is False


def test_auto_without_an_answer_is_not_full_duplex():
    def broken():
        raise OSError("wpctl missing")
    assert full_duplex("auto", broken) is False


def test_headphones_can_force_it_either_way():
    never_asked = lambda: (_ for _ in ()).throw(AssertionError("asked"))
    assert full_duplex(True, never_asked) is True
    assert full_duplex(False, never_asked) is False
