"""Only what the user named crosses over.

The whole reason this module exists rather than a bulk profile copy is that
"we import only these sites" is a property you can verify, where "we import
everything but promise not to look" is a claim you have to trust. So the
tests check the property directly, against a synthetic Chrome profile —
never the real one.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.grounding.web.profile_import import (  # noqa: E402
    _matches, _normalise, filter_cookie_store, import_logins)


def _fake_chrome(tmp_path: Path, hosts: list[str]) -> Path:
    """A Chrome-shaped profile with one cookie per host."""
    profile = tmp_path / "chrome"
    (profile / "Default" / "Network").mkdir(parents=True)
    (profile / "Local State").write_text("{}")
    (profile / "Default" / "Preferences").write_text("{}")

    db = profile / "Default" / "Network" / "Cookies"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
    con.executemany("INSERT INTO cookies VALUES (?, ?, ?)",
                    [(h, "sid", "secret-value") for h in hosts])
    con.commit()
    con.close()
    return profile


def _hosts_in(profile: Path) -> set[str]:
    db = profile / "Default" / "Network" / "Cookies"
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute("SELECT host_key FROM cookies")}
    finally:
        con.close()


def test_only_the_named_sites_survive(tmp_path):
    source = _fake_chrome(tmp_path, [
        ".youtube.com", "www.youtube.com",
        ".github.com",
        ".mybank.example", "mail.google.com", ".facebook.com",
    ])
    into = tmp_path / "eagle"

    result = import_logins(["youtube.com", "github.com"],
                           into=into, source=source)

    assert result.ok
    survivors = _hosts_in(into)
    assert survivors == {".youtube.com", "www.youtube.com", ".github.com"}
    # The point of the whole module: nothing unnamed came across.
    assert not any("bank" in h or "google" in h or "facebook" in h
                   for h in survivors)
    assert result.dropped == 3


def test_the_users_own_profile_is_never_modified(tmp_path):
    source = _fake_chrome(tmp_path, [".youtube.com", ".mybank.example"])
    before = _hosts_in(source)

    import_logins(["youtube.com"], into=tmp_path / "eagle", source=source)

    assert _hosts_in(source) == before, "the source profile was mutated"


def test_subdomains_of_a_named_site_come_across():
    assert _matches(".youtube.com", "youtube.com")
    assert _matches("m.youtube.com", "youtube.com")
    assert _matches("youtube.com", "youtube.com")


def test_a_lookalike_domain_does_not_sneak_in():
    """The filter matches on domain boundaries, not string suffixes —
    otherwise `notyoutube.com` would ride in on `youtube.com`."""
    assert not _matches("notyoutube.com", "youtube.com")
    assert not _matches("youtube.com.evil.test", "youtube.com")
    assert not _matches(".evil-youtube.com", "youtube.com")


@pytest.mark.parametrize("given, expected", [
    ("https://www.youtube.com/feed/liked", "youtube.com"),
    ("YouTube.com", "youtube.com"),
    ("www.github.com", "github.com"),
    (".emag.ro", "emag.ro"),
])
def test_users_can_name_a_site_however_they_say_it(given, expected):
    assert _normalise(given) == expected


def test_naming_nothing_imports_nothing(tmp_path):
    source = _fake_chrome(tmp_path, [".youtube.com"])
    result = import_logins([], into=tmp_path / "eagle", source=source)
    assert result.ok is False
    assert not (tmp_path / "eagle").exists()


def test_a_site_the_user_is_not_signed_into_is_reported_not_hidden(tmp_path):
    source = _fake_chrome(tmp_path, [".youtube.com"])
    result = import_logins(["youtube.com", "github.com"],
                           into=tmp_path / "eagle", source=source)
    assert result.imported["github.com"] == 0
    assert "github.com" in result.guidance
    # The Google gotcha is worth naming: YouTube's sign-in lives on google.com.
    assert "google.com" in result.guidance


def test_the_filter_is_a_whitelist_not_a_blocklist(tmp_path):
    """A host nobody anticipated must be dropped by default, not kept."""
    source = _fake_chrome(tmp_path, [".youtube.com", ".something-new.test"])
    import_logins(["youtube.com"], into=tmp_path / "eagle", source=source)
    assert _hosts_in(tmp_path / "eagle") == {".youtube.com"}


def test_an_existing_eagle_profile_is_replaced_not_merged(tmp_path):
    """Merging two stores encrypted under different keys silently loses one
    side, so an import is a clean slate by design."""
    into = tmp_path / "eagle"
    into.mkdir()
    (into / "stale-marker").write_text("from an earlier life")

    source = _fake_chrome(tmp_path, [".youtube.com"])
    import_logins(["youtube.com"], into=into, source=source)

    assert not (into / "stale-marker").exists()


def test_cookie_values_never_appear_in_what_is_reported(tmp_path):
    source = _fake_chrome(tmp_path, [".youtube.com"])
    result = import_logins(["youtube.com"], into=tmp_path / "eagle",
                           source=source)
    blob = f"{result.detail} {result.guidance} {result.imported}"
    assert "secret-value" not in blob
