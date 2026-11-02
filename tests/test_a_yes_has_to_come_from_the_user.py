"""A confirmation clears only on the user's own yes, said after the question."""
from core import confirm


def test_the_model_calling_straight_back_is_not_a_yes():
    gate = confirm.Gate()
    token = gate.issue("start_print", {"file": "benchy"})
    cleared, why, lead = gate.check("start_print", {"file": "benchy"}, token)
    assert not cleared and "not answered" in why and "Ask the user" in lead


def test_the_same_token_clears_once_the_user_says_yes():
    gate = confirm.Gate()
    token = gate.issue("start_print", {"file": "benchy"})
    gate.check("start_print", {"file": "benchy"}, token)
    confirm.note_user_turn(" Yeah,")
    confirm.note_user_turn(" go ahead.")
    assert gate.check("start_print", {"file": "benchy"}, token)[0]


def test_a_turn_before_the_question_does_not_count():
    confirm.note_user_turn("yes", typed=True)
    gate = confirm.Gate()
    token = gate.issue("shutdown", {})
    assert not gate.check("shutdown", {}, token)[0]


def test_speech_that_is_not_a_yes_keeps_the_question_open():
    gate = confirm.Gate()
    token = gate.issue("send_message", {"to": "Ana"})
    confirm.note_user_turn(" what does that do")
    cleared, why, _ = gate.check("send_message", {"to": "Ana"}, token)
    assert not cleared and "not said yes" in why
    confirm.note_user_turn("yes", typed=True)
    assert gate.check("send_message", {"to": "Ana"}, token)[0]


def test_a_no_spends_the_token():
    gate = confirm.Gate()
    token = gate.issue("shutdown", {})
    confirm.note_user_turn(" No, don't.")
    cleared, why, _ = gate.check("shutdown", {}, token)
    assert not cleared and "said no" in why
    confirm.note_user_turn("yes", typed=True)
    assert not gate.check("shutdown", {}, token)[0]
