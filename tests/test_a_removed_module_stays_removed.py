"""A module the user took out is not put back by the installer's bundle step."""
import pytest

from core.module_bus import installer


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "eagle_home", lambda: tmp_path)
    monkeypatch.setattr(installer, "shop", lambda: [{"name": "trade", "installed": False},
                                                      {"name": "3d", "installed": False}])
    return tmp_path


def test_bundle_installs_what_is_missing(home):
    done = []
    added = installer.bundle(["trade"], install_one=done.append)
    assert added == ["trade"] and done == ["trade"]


def test_bundle_skips_a_module_the_user_removed(home):
    installer._set_removed("trade", True)
    done = []
    assert installer.bundle(["trade"], install_one=done.append) == []
    assert done == []


def test_asking_for_it_by_name_brings_it_back(home, monkeypatch):
    installer._set_removed("trade", True)
    assert "trade" in installer.removed_by_user()
    installer._set_removed("trade", False)
    done = []
    assert installer.bundle(["trade"], install_one=done.append) == ["trade"]


def test_bundle_skips_a_module_already_installed(home, monkeypatch):
    monkeypatch.setattr(installer, "shop", lambda: [{"name": "trade", "installed": True}])
    done = []
    assert installer.bundle(["trade"], install_one=done.append) == []
