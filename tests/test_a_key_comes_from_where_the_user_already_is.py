"""Setup opens Google's key page in the browser profile signed in to Google,
and offers a Gemini key already set up on the computer. Both read files other
programs own: Chrome's Local State and the Gemini CLI's .env."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web import chrome_link  # noqa: E402
from core import gemini_key  # noqa: E402

KEY = "AIza" + "k" * 35


def test_the_most_recent_signed_in_profile_is_the_one_opened(tmp_path, monkeypatch):
    home = tmp_path / "google-chrome"
    home.mkdir()
    (home / "Local State").write_text(json.dumps({"profile": {"info_cache": {
        "Default": {"active_time": 300, "name": "Guest-ish"},
        "Profile 1": {"active_time": 100, "gaia_id": "1", "user_name": "old@x.com",
                      "gaia_given_name": "Old"},
        "Profile 2": {"active_time": 200, "gaia_id": "2", "user_name": "ana@x.com",
                      "gaia_given_name": "Ana Maria"},
    }}}))
    monkeypatch.setattr(chrome_link, "_homes", lambda: [("Chrome", home, "")])
    launched = []
    monkeypatch.setattr(chrome_link, "_launch", lambda b, args: launched.append(args) or True)

    g = chrome_link.open_signed_in("https://aistudio.google.com/apikey")

    assert (g.directory, g.email) == ("Profile 2", "ana@x.com")
    assert launched == [["--profile-directory=Profile 2", "https://aistudio.google.com/apikey"]]
    assert chrome_link.account_name() == "Ana"


def test_a_key_already_on_the_computer_is_found(tmp_path):
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / ".env").write_text(f'export GEMINI_API_KEY="{KEY}"\nOTHER=1\n')
    (tmp_path / ".env").write_text(f"GOOGLE_API_KEY={KEY}\n")
    env = {"GEMINI_API_KEY": "not-a-key", "GOOGLE_API_KEY": ""}

    assert gemini_key.on_this_computer(environ=env, home=tmp_path) == [KEY]
    assert gemini_key.on_this_computer(environ={}, home=tmp_path / "nowhere") == []
