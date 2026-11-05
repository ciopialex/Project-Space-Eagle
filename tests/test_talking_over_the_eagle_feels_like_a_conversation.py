"""Talking over the eagle, the way people talk over each other. It lowers its voice
the moment it hears the user, gives way only if they keep talking, and carries on
after a short "mhm" or a cough. Whatever the user said is kept from their first
word: on giving way, Gemini gets all of it."""
import main
from core import prefs


class _UI:
    muted = False
    assistant_name = "Aethelark"
    current_file = None

    def __getattr__(self, name):
        return lambda *a, **k: None


def _frame(n: int) -> bytes:
    return n.to_bytes(2, "little") * main.CHUNK_SIZE


def _talked_over(monkeypatch, voiced: list[int], talk_over=True):
    """Feed one frame per entry while the eagle speaks; 1 = the user's voice."""
    monkeypatch.setattr(prefs, "get", lambda name: {"barge_in_enabled": talk_over}.get(
        name, prefs.DEFAULTS.get(name)))
    live = main.AethelarkLive(_UI())
    live._is_speaking = True
    sent, stops, gains = [], [], []
    # Everything bound for Gemini passes through this queue.
    live._push_mic_queue = lambda msg: sent.append(int.from_bytes(msg["data"][:2], "little"))
    live._mic_shield_on = False
    live.interrupt = lambda: stops.append(len(sent))
    real_feed = live._barge.feed
    script = iter(voiced)

    def feed(frame, *a, **k):
        real_feed(frame, *a, **k)          # keeps the frame, for what is sent later
        live._barge.last_voiced = bool(next(script))
        return False
    live._barge.feed = feed
    for i in range(len(voiced)):
        live._on_mic_frame(_frame(i + 1))
        gains.append(live._duck_gain)
    return live, sent, stops, gains


def test_a_short_mhm_lowers_its_voice_and_it_carries_on(monkeypatch):
    live, sent, stops, gains = _talked_over(monkeypatch, [0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0])
    assert min(gains) < 1.0, "it faltered"
    assert gains[-1] == 1.0, "and came back up"
    assert not stops and not sent, "it did not stop, and Gemini never heard the mhm"


def test_talking_on_makes_it_give_way_and_nothing_said_is_lost(monkeypatch):
    voiced = [0, 0, 0, 0, 0, 0] + [1] * 9
    live, sent, stops, gains = _talked_over(monkeypatch, voiced)
    assert stops, "it gave way"
    assert gains[7] < 1.0, "it lowered its voice before giving way"
    first_word = voiced.index(1) + 1
    assert first_word in sent, "Gemini got the user's first word"
    assert sent == sorted(sent), "in order"


def test_a_cough_is_not_enough_to_stop_it(monkeypatch):
    _, sent, stops, _ = _talked_over(monkeypatch, [1] + [0] * 10)
    assert not stops and not sent


def test_it_does_not_give_way_before_about_half_a_second_of_voice(monkeypatch):
    frame_ms = 1000 * main.CHUNK_SIZE / main.SEND_SAMPLE_RATE
    n = int(400 / frame_ms)
    _, _, stops, _ = _talked_over(monkeypatch, [1] * n)
    assert not stops


def test_when_it_finishes_mid_word_the_held_words_still_reach_gemini(monkeypatch):
    live, sent, stops, _ = _talked_over(monkeypatch, [0, 0, 1, 1, 1])
    assert not sent
    live._is_speaking = False                 # its sentence ended
    live._on_mic_frame(_frame(99))
    assert sent[-1] == 99 and 3 in sent and sent == sorted(sent), sent


def test_talk_over_switched_off_neither_lowers_nor_stops(monkeypatch):
    _, sent, stops, gains = _talked_over(monkeypatch, [1] * 15, talk_over=False)
    assert not stops and not sent and set(gains) == {1.0}
