"""A confirmation clears only after the user takes a turn: the model cannot
answer its own question by calling straight back with the token."""
from core import confirm


def test_the_model_calling_straight_back_is_not_a_yes():
    gate = confirm.Gate()
    token = gate.issue("start_print", {"file": "benchy"})
    cleared, why, lead = gate.check("start_print", {"file": "benchy"}, token)
    assert not cleared and "not answered" in why and "Ask the user" in lead


def test_the_same_token_clears_once_the_user_has_answered():
    gate = confirm.Gate()
    token = gate.issue("start_print", {"file": "benchy"})
    gate.check("start_print", {"file": "benchy"}, token)
    confirm.note_user_turn()
    assert gate.check("start_print", {"file": "benchy"}, token)[0]


def test_a_turn_before_the_question_does_not_count():
    confirm.note_user_turn()
    gate = confirm.Gate()
    token = gate.issue("shutdown", {})
    assert not gate.check("shutdown", {}, token)[0]
