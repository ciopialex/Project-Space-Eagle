import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.contact_match import match, normalise  # noqa: E402

BOOK = ["Mama ❤️", "Alex Popescu", "Alex Ionescu", "Ștefan 🎸", "Andreea Work",
        "Andrei", "Dr. Ionescu Clinic", "Tata"]


def test_normalise_strips_what_speech_cannot_carry():
    assert normalise("Ștefan 🎸") == "stefan"
    assert normalise("  Mama ❤️ ") == "mama"


@pytest.mark.parametrize("spoken,kind,title", [
    ("Tata", "exact", "Tata"),
    ("stefan", "exact", "Ștefan 🎸"),
    ("popescu", "sole", "Alex Popescu"),
    ("alex popescu", "exact", "Alex Popescu"),
    ("mom", "close", "Mama ❤️"),
    ("dad", "close", "Tata"),
])
def test_one_person_is_found(spoken, kind, title):
    m = match(spoken, BOOK)
    assert (m.kind, m.title) == (kind, title)


def test_two_people_with_the_name_is_a_question_never_a_pick():
    m = match("alex", BOOK)
    assert m.kind == "ambiguous" and m.title is None and not m.certain
    assert set(m.candidates) == {"Alex Popescu", "Alex Ionescu"}


def test_andrei_and_andreea_are_not_confused():
    assert match("andrei", BOOK).title == "Andrei"


def test_a_nickname_is_not_certain_until_learned():
    assert not match("mom", BOOK).certain
    m = match("mom", BOOK, aliases={"mom": "Mama ❤️"})
    assert (m.kind, m.title, m.certain) == ("alias", "Mama ❤️", True)


def test_a_stale_alias_for_a_chat_that_is_gone_is_ignored():
    assert match("mom", ["Tata"], aliases={"mom": "Mama ❤️"}).kind != "alias"


def test_nobody_close_is_none_with_near_misses():
    m = match("bogdan", BOOK)
    assert m.kind == "none" and m.title is None


def test_contact_aliases_learns_and_loads_per_platform(monkeypatch, tmp_path):
    from core import contact_aliases, user_paths

    monkeypatch.setattr(user_paths, "user_data_dir", lambda: tmp_path)

    contact_aliases.learn("whatsapp", "mom", "Mama ❤️")

    assert contact_aliases.load("whatsapp") == {"mom": "Mama ❤️"}
    assert contact_aliases.load("telegram") == {}

    contact_aliases.learn("whatsapp", "tata", "Tata")
    assert "tata" not in contact_aliases.load("whatsapp")


@pytest.mark.parametrize("spoken,titles", [
    ("florin", ["Florina", "Mihai"]),
    ("daniel", ["Daniela"]),
    ("adi", ["Adrian", "Adina"]),
    ("dan", ["Daniel", "Bogdan"]),
])
def test_a_word_prefix_is_not_certain(spoken, titles):
    assert not match(spoken, titles).certain


def test_a_single_word_prefix_cover_is_close():
    m = match("florin", ["Florina", "Mihai"])
    assert m.kind == "close"


def test_popescu_is_still_sole_in_the_book():
    assert match("popescu", BOOK).kind == "sole"


def test_alex_is_still_ambiguous_in_the_book():
    m = match("alex", BOOK)
    assert m.kind == "ambiguous"
    assert set(m.candidates) == {"Alex Popescu", "Alex Ionescu"}


def test_identical_duplicate_titles_are_ambiguous():
    assert match("alex", ["Alex", "Alex"]).kind == "ambiguous"


def test_a_duplicate_sole_match_is_ambiguous():
    assert match("popescu", ["Alex Popescu", "Alex Popescu"]).kind == "ambiguous"


def test_a_duplicate_alias_target_is_ambiguous():
    m = match("mom", ["Mama ❤️", "Mama ❤️", "Tata"], aliases={"mom": "Mama ❤️"})
    assert m.kind == "ambiguous"


def test_normalise_folds_extra_latin_letters():
    assert normalise("Łukasz") == "lukasz"
    assert normalise("Øystein") == "oystein"
    assert normalise("Đorđe") == "dorde"
    assert normalise("Straße") == "strasse"


def test_a_repeated_spoken_word_does_not_fake_a_sole_match():
    assert match("ana ana", ["Ana Pop"]).kind != "sole"


def test_learn_on_a_read_only_data_dir_fails_quietly(monkeypatch, tmp_path):
    from core import contact_aliases, user_paths

    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    ro_dir.chmod(0o500)
    monkeypatch.setattr(user_paths, "user_data_dir", lambda: ro_dir)
    try:
        assert contact_aliases.learn("whatsapp", "mom", "Mama ❤️") is False
    finally:
        ro_dir.chmod(0o700)


def test_load_ignores_a_platform_table_that_is_not_a_dict(monkeypatch, tmp_path):
    from core import contact_aliases, user_paths

    monkeypatch.setattr(user_paths, "user_data_dir", lambda: tmp_path)
    (tmp_path / "contact_aliases.json").write_text(
        json.dumps({"whatsapp": ["x"]}), encoding="utf-8"
    )
    assert contact_aliases.load("whatsapp") == {}


def test_a_corrupt_file_is_quarantined_by_learn(monkeypatch, tmp_path):
    from core import contact_aliases, user_paths

    monkeypatch.setattr(user_paths, "user_data_dir", lambda: tmp_path)
    path = tmp_path / "contact_aliases.json"
    path.write_text("{not valid json", encoding="utf-8")

    assert contact_aliases.learn("whatsapp", "mom", "Mama ❤️") is True

    bad = tmp_path / "contact_aliases.json.bad"
    assert bad.read_text(encoding="utf-8") == "{not valid json"
    assert contact_aliases.load("whatsapp") == {"mom": "Mama ❤️"}


def test_an_alias_duplicated_across_case_is_ambiguous():
    m = match("mom", ["Mama", "MAMA"], aliases={"mom": "Mama"})
    assert m.kind == "ambiguous" and not m.certain


def test_an_alias_duplicated_across_decoration_is_ambiguous():
    m = match("mom", ["Mama ❤️", "Mama"], aliases={"mom": "Mama ❤️"})
    assert m.kind == "ambiguous" and not m.certain


def test_fuzzy_close_offers_runner_up_candidates(monkeypatch):
    import core.contact_match as cm

    scores = {"aa": 0.99, "bb": 0.85, "cc": 0.83, "dd": 0.5}
    monkeypatch.setattr(cm, "_score", lambda spoken, title: scores[title])
    m = cm.match("x", ["Aa", "Bb", "Cc", "Dd"])
    assert m.kind == "close" and m.title == "Aa"
    assert m.candidates == ("Aa", "Bb", "Cc")


def test_a_bad_utf8_file_is_quarantined_and_learn_succeeds(monkeypatch, tmp_path):
    from core import contact_aliases, user_paths

    monkeypatch.setattr(user_paths, "user_data_dir", lambda: tmp_path)
    path = tmp_path / "contact_aliases.json"
    path.write_bytes(b"\xff\xfe{bad")

    assert contact_aliases.learn("whatsapp", "mom", "Mama ❤️") is True

    bad = tmp_path / "contact_aliases.json.bad"
    assert bad.read_bytes() == b"\xff\xfe{bad"
    assert contact_aliases.load("whatsapp") == {"mom": "Mama ❤️"}


@pytest.mark.parametrize("platform,spoken,title", [
    ("whatsapp", 5, "x"),
    ("whatsapp", "x", 5),
    (None, "x", "y"),
])
def test_learn_with_a_bad_argument_type_fails_quietly(monkeypatch, tmp_path, platform, spoken, title):
    from core import contact_aliases, user_paths

    monkeypatch.setattr(user_paths, "user_data_dir", lambda: tmp_path)
    assert contact_aliases.learn(platform, spoken, title) is False


def test_learn_with_title_none_fails_quietly(monkeypatch, tmp_path):
    from core import contact_aliases, user_paths

    monkeypatch.setattr(user_paths, "user_data_dir", lambda: tmp_path)
    assert contact_aliases.learn("whatsapp", "mom", None) is False


def test_load_with_none_platform_returns_empty(monkeypatch, tmp_path):
    from core import contact_aliases, user_paths

    monkeypatch.setattr(user_paths, "user_data_dir", lambda: tmp_path)
    assert contact_aliases.load(None) == {}


def test_learn_leaves_an_unreadable_file_untouched(monkeypatch, tmp_path):
    from core import contact_aliases, user_paths

    monkeypatch.setattr(user_paths, "user_data_dir", lambda: tmp_path)
    path = tmp_path / "contact_aliases.json"
    path.write_text(json.dumps({"whatsapp": {"dad": "Tata"}}), encoding="utf-8")
    path.chmod(0o000)
    try:
        assert contact_aliases.learn("whatsapp", "mom", "Mama ❤️") is False
    finally:
        path.chmod(0o600)
    assert json.loads(path.read_text(encoding="utf-8")) == {"whatsapp": {"dad": "Tata"}}
    assert not (tmp_path / "contact_aliases.json.bad").exists()


def test_a_second_corrupt_file_gets_a_timestamped_bad_name(monkeypatch, tmp_path):
    from core import contact_aliases, user_paths

    monkeypatch.setattr(user_paths, "user_data_dir", lambda: tmp_path)
    path = tmp_path / "contact_aliases.json"
    bad = tmp_path / "contact_aliases.json.bad"
    bad.write_text("first corrupt", encoding="utf-8")
    path.write_text("{also not valid json", encoding="utf-8")

    assert contact_aliases.learn("whatsapp", "mom", "Mama ❤️") is True

    assert bad.read_text(encoding="utf-8") == "first corrupt"
    timestamped = list(tmp_path.glob("contact_aliases.json.bad.*"))
    assert len(timestamped) == 1
    assert timestamped[0].read_text(encoding="utf-8") == "{also not valid json"
