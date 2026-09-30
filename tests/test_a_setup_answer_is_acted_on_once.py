from core.setup_wishes import add_wish, take_wish


def test_a_wish_is_taken_once_and_only_once():
    cfg = add_wish({}, "install:3d")
    assert take_wish(cfg, "install:3d") is True
    assert take_wish(cfg, "install:3d") is False


def test_no_wish_means_nothing_is_installed():
    assert take_wish({"gemini_api_key": "x"}, "install:3d") is False


def test_other_wishes_and_settings_survive():
    cfg = add_wish(add_wish({"user_name": "A"}, "install:3d"), "install:other")
    take_wish(cfg, "install:3d")
    assert cfg == {"user_name": "A", "wishes": ["install:other"]}


def test_every_ticked_module_is_returned_once_and_other_wishes_stay():
    from core.setup_wishes import INSTALL, take_installs
    cfg = add_wish(add_wish(add_wish({}, INSTALL + "trade"), INSTALL + "3d"), "something-else")
    assert sorted(take_installs(cfg)) == ["3d", "trade"]
    assert take_installs(cfg) == []
    assert cfg["wishes"] == ["something-else"]


def test_nothing_ticked_changes_nothing():
    from core.setup_wishes import take_installs
    cfg = {"user_name": "A"}
    assert take_installs(cfg) == [] and cfg == {"user_name": "A"}
