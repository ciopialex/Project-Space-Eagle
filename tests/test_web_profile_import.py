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


# ── Importing while Chrome is running ───────────────────────────────────────
# The old rule was "quit Chrome completely, then run this again." That is not a
# product decision, it is an implementation detail leaking onto the user: the
# import used shutil.copy2, which can tear a live SQLite database, so it
# refused rather than risk it. Right instinct, wrong tool. SQLite's backup API
# exists for exactly this and produces a consistent snapshot while another
# process writes. Verified against the real Chrome profile with Chrome up:
# 625 cookies read cleanly.

def _live_chrome_profile(tmp_path, writer_keeps_writing=False):
    """A Chrome-shaped profile whose cookie DB has an open writer."""
    prof = tmp_path / "chrome"
    (prof / "Default").mkdir(parents=True)
    db = prof / "Default" / "Cookies"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
    for i in range(200):
        host = ".youtube.com" if i % 2 else ".bank.example"
        con.execute("INSERT INTO cookies VALUES (?,?,?)", (host, f"c{i}", "secret"))
    con.commit()
    (prof / "Local State").write_text("{}")
    (prof / "Default" / "Preferences").write_text("{}")
    if writer_keeps_writing:
        # Left open and uncommitted: a live Chrome, mid-write.
        con.execute("BEGIN")
        con.execute("INSERT INTO cookies VALUES ('.x.example','p','q')")
        return prof, con
    con.close()
    return prof, None


def test_the_import_no_longer_demands_that_chrome_be_closed(tmp_path, monkeypatch):
    """The UX fix, stated as behaviour. A user should never be asked to quit
    their browser to let their assistant do its job."""
    import actions.grounding.web.profile_import as P
    src, _ = _live_chrome_profile(tmp_path)
    # Patched so `source is None` resolves to this profile AND the running-Chrome
    # guard compares equal. Passing source= explicitly dodges the guard, which
    # made the first version of this test pass against the unfixed code.
    monkeypatch.setattr(P, "chrome_is_running", lambda: True)
    monkeypatch.setattr(P, "chrome_profile_dir", lambda: src)

    result = P.import_logins(["youtube.com"], into=tmp_path / "eagle")
    assert result.ok, result.detail
    assert result.imported["youtube.com"] == 100


def test_a_live_writer_does_not_corrupt_the_snapshot(tmp_path, monkeypatch):
    """The reason the old code refused. An uncommitted transaction is exactly
    the state a running Chrome is in most of the time."""
    import actions.grounding.web.profile_import as P
    src, writer = _live_chrome_profile(tmp_path, writer_keeps_writing=True)
    monkeypatch.setattr(P, "chrome_is_running", lambda: True)
    monkeypatch.setattr(P, "chrome_profile_dir", lambda: src)
    try:
        result = P.import_logins(["youtube.com"], into=tmp_path / "eagle")
        assert result.ok, result.detail
        # The uncommitted row must not appear — a snapshot, not a torn read.
        assert result.imported["youtube.com"] == 100
    finally:
        writer.rollback(); writer.close()


def test_other_peoples_cookies_still_never_arrive(tmp_path, monkeypatch):
    """Unchanged and non-negotiable. Making the import easier must not make it
    broader — the whole safety argument is that only named domains survive."""
    import actions.grounding.web.profile_import as P
    src, _ = _live_chrome_profile(tmp_path)
    monkeypatch.setattr(P, "chrome_is_running", lambda: True)
    monkeypatch.setattr(P, "chrome_profile_dir", lambda: src)
    dest = tmp_path / "eagle"

    P.import_logins(["youtube.com"], into=dest)

    con = sqlite3.connect(str(P._cookie_store(dest)))
    hosts = {h for (h,) in con.execute("SELECT DISTINCT host_key FROM cookies")}
    con.close()
    assert hosts == {".youtube.com"}, f"leaked: {hosts}"


def test_the_users_own_profile_is_never_written_to(tmp_path, monkeypatch):
    import actions.grounding.web.profile_import as P
    src, _ = _live_chrome_profile(tmp_path)
    monkeypatch.setattr(P, "chrome_is_running", lambda: True)
    monkeypatch.setattr(P, "chrome_profile_dir", lambda: src)
    before = (P._cookie_store(src).read_bytes(), P._cookie_store(src).stat().st_mtime)

    P.import_logins(["youtube.com"], into=tmp_path / "eagle")

    after = (P._cookie_store(src).read_bytes(), P._cookie_store(src).stat().st_mtime)
    assert before == after, "the import modified the user's own Chrome profile"


def test_importing_a_second_site_does_not_log_out_the_first(tmp_path, monkeypatch):
    """`into` is replaced wholesale, so a naive second import would silently
    discard the first site's session. Nobody would report that as a bug — they
    would just find themselves logged out of YouTube after asking about GitHub,
    and conclude the eagle is unreliable."""
    import actions.grounding.web.profile_import as P
    prof = tmp_path / "chrome"
    (prof / "Default").mkdir(parents=True)
    con = sqlite3.connect(str(prof / "Default" / "Cookies"))
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
    for host in (".youtube.com", ".github.com", ".bank.example"):
        con.execute("INSERT INTO cookies VALUES (?,?,?)", (host, "s", "v"))
    con.commit(); con.close()
    (prof / "Local State").write_text("{}")
    monkeypatch.setattr(P, "chrome_profile_dir", lambda: prof)
    monkeypatch.setattr(P, "chrome_is_running", lambda: True)

    dest = tmp_path / "eagle"
    P.import_logins(["youtube.com"], into=dest)
    second = P.import_logins(["github.com"], into=dest)

    assert second.ok
    con = sqlite3.connect(str(P._cookie_store(dest)))
    hosts = {h for (h,) in con.execute("SELECT DISTINCT host_key FROM cookies")}
    con.close()
    assert hosts == {".youtube.com", ".github.com"}, f"got {hosts}"


def test_an_accumulated_import_still_excludes_everything_unnamed(tmp_path, monkeypatch):
    """Accumulating must not drift into importing everything over time."""
    import actions.grounding.web.profile_import as P
    prof = tmp_path / "chrome"
    (prof / "Default").mkdir(parents=True)
    con = sqlite3.connect(str(prof / "Default" / "Cookies"))
    con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
    for host in (".youtube.com", ".github.com", ".bank.example"):
        con.execute("INSERT INTO cookies VALUES (?,?,?)", (host, "s", "v"))
    con.commit(); con.close()
    (prof / "Local State").write_text("{}")
    monkeypatch.setattr(P, "chrome_profile_dir", lambda: prof)

    dest = tmp_path / "eagle"
    for d in ("youtube.com", "github.com", "youtube.com"):
        P.import_logins([d], into=dest)

    con = sqlite3.connect(str(P._cookie_store(dest)))
    hosts = {h for (h,) in con.execute("SELECT DISTINCT host_key FROM cookies")}
    con.close()
    assert ".bank.example" not in hosts
