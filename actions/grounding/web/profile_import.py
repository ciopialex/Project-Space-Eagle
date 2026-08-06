"""Bring named logins across from the user's Chrome, and nothing else.

The eagle keeps its own browser profile, so it starts signed into nothing. Two
ways to fix that: sign in once per site inside the eagle's browser (see
`web_agency` action='sign_in'), or copy sessions across from the Chrome the
user already lives in. This module is the second one, deliberately narrowed.

**Only the domains the user names are imported.** Not "everything, but we
promise not to look" — the cookies for every other site are deleted from the
copy before the eagle's browser is ever pointed at it. That is a property the
user can check rather than a promise they have to take on trust, which is the
whole reason for doing it this way: importing the lot would hand the eagle
their bank, their email, and every session they have, to fetch a playlist.

Three hard rules, enforced below rather than documented and hoped for:

1. The user's Chrome profile is opened read-only and never written to. Every
   mutation happens on a copy.
2. Chrome must not be running. A live Chrome holds locks on its cookie store
   and rewrites it constantly; copying underneath that yields a torn database.
3. Cookie *values* are never read, logged, or returned. The import counts rows
   and reports domains; it has no reason to see a session token and does not.

Why this drives Chrome rather than Playwright's bundled Chromium: on Linux the
cookie store is encrypted with a key held in the system keyring, under an entry
named for the browser that wrote it ("Chrome Safe Storage"). Chromium looks up
"Chromium Safe Storage", finds a different key, and silently reads every cookie
as garbage — no error, just a profile that appears signed out. Only Chrome can
decrypt Chrome's cookies, so an imported profile is pinned to Chrome for life
(see `browser.py`, which records the channel in the profile directory).

What this does NOT bring across: `localStorage`. Chrome keeps it in a LevelDB
keyed by origin, which cannot be filtered by domain without shipping a LevelDB
reader — and copying it wholesale would import origins the user never named,
which is the one thing this module exists to avoid. Sites that keep their
session in `localStorage` rather than cookies will still need `sign_in`.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

#: Where Chrome keeps its cookie store. Newer builds moved it under Network/;
#: older ones keep it beside Preferences. Both are checked, newest first.
_COOKIE_PATHS = ("Default/Network/Cookies", "Default/Cookies")

#: Copied alongside the cookies. `Local State` carries the encryption metadata
#: Chrome needs to make sense of the store at all; `Preferences` keeps the
#: profile from looking freshly-installed and triggering first-run flows.
_SUPPORTING_FILES = ("Local State", "Default/Preferences")


@dataclass
class ImportResult:
    """What crossed over. Deliberately counts and names only — never values."""
    ok: bool
    detail: str
    imported: dict[str, int] = field(default_factory=dict)
    dropped: int = 0
    guidance: str = ""

    @property
    def total(self) -> int:
        return sum(self.imported.values())


def chrome_profile_dir() -> Path:
    return Path.home() / ".config" / "google-chrome"


def chrome_is_running() -> bool:
    """True if a Chrome process is up. Best-effort; assumes running on error.

    Assuming *running* is the safe default: the cost of a false positive is
    the user closes a browser that was already closed, and the cost of a false
    negative is a torn copy of a database being rewritten underneath us.
    """
    try:
        out = subprocess.run(["pgrep", "-x", "chrome"],
                             capture_output=True, timeout=5)
        if out.returncode == 0:
            return True
        out = subprocess.run(["pgrep", "-f", "google-chrome"],
                             capture_output=True, timeout=5)
        return out.returncode == 0
    except FileNotFoundError:
        return False           # no pgrep; nothing better to check with
    except Exception:
        return True


def _normalise(domain: str) -> str:
    """'https://www.YouTube.com/feed' -> 'youtube.com'."""
    d = (domain or "").strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.split("/")[0].split("?")[0]
    if d.startswith("www."):
        d = d[4:]
    return d.strip(".")


def _matches(host_key: str, domain: str) -> bool:
    """Does a cookie's host match a named domain, including its subdomains?

    Chrome stores hosts as `youtube.com`, `.youtube.com` (domain cookie) or
    `m.youtube.com`. All three belong to the site the user named; a host that
    merely *ends* with the same letters — `notyoutube.com` — does not.
    """
    host = (host_key or "").lower().lstrip(".")
    return host == domain or host.endswith("." + domain)


def _cookie_store(profile: Path) -> Path | None:
    for rel in _COOKIE_PATHS:
        candidate = profile / rel
        if candidate.exists():
            return candidate
    return None


def filter_cookie_store(db_path: Path, domains: list[str]) -> tuple[dict, int]:
    """Delete every cookie not belonging to `domains`. Returns (kept, dropped).

    Operates on a copy — never call this on the user's own store. The filter
    is a whitelist by construction: rows are matched *in*, and everything
    unmatched is deleted, so a domain nobody named cannot survive by accident.
    """
    kept: dict[str, int] = {d: 0 for d in domains}
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute("SELECT rowid, host_key FROM cookies").fetchall()
        doomed = []
        for rowid, host_key in rows:
            owner = next((d for d in domains if _matches(host_key, d)), None)
            if owner is None:
                doomed.append(rowid)
            else:
                kept[owner] += 1
        for start in range(0, len(doomed), 500):
            chunk = doomed[start:start + 500]
            placeholders = ",".join("?" * len(chunk))
            connection.execute(
                f"DELETE FROM cookies WHERE rowid IN ({placeholders})", chunk)
        connection.commit()
        return kept, len(doomed)
    finally:
        connection.close()


def import_logins(domains: list[str], *,
                  into: Path,
                  source: Path | None = None) -> ImportResult:
    """Copy the named sites' sessions from Chrome into `into`.

    `into` is replaced wholesale — an import is a fresh start for the eagle's
    browser, not a merge, because merging two cookie stores encrypted under
    different keys silently loses whichever set the running browser cannot
    read. Anything the eagle had signed into before is re-imported or
    re-signed-in after.
    """
    wanted = sorted({_normalise(d) for d in (domains or []) if _normalise(d)})
    if not wanted:
        return ImportResult(False, "No sites named to import.",
                            guidance=("Name the sites to bring across, e.g. "
                                      "domains=['youtube.com', 'github.com']."))

    source = source or chrome_profile_dir()
    if not source.exists():
        return ImportResult(
            False, f"No Chrome profile found at {source}.",
            guidance=("If the user's logins live in a different browser, this "
                      "cannot import them — use action='sign_in' per site."))

    if source == chrome_profile_dir() and chrome_is_running():
        return ImportResult(
            False, "Chrome is running, so its cookie store cannot be copied "
                   "safely.",
            guidance=("Ask the user to quit Chrome completely, then run this "
                      "again. It takes a few seconds and only has to happen "
                      "once."))

    store = _cookie_store(source)
    if store is None:
        return ImportResult(
            False, "Chrome's profile has no cookie store to import.",
            guidance="Check the user is signed in to those sites in Chrome.")

    staging = Path(tempfile.mkdtemp(prefix="aethelark-import-"))
    try:
        # Rebuild the layout Chrome expects, copying only what carries or
        # decodes sessions. The user's own profile is never opened for writing.
        relative = store.relative_to(source)
        (staging / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(store, staging / relative)
        for rel in _SUPPORTING_FILES:
            src = source / rel
            if src.exists():
                (staging / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, staging / rel)

        kept, dropped = filter_cookie_store(staging / relative, wanted)

        # Only now, with every other site's cookies already deleted from the
        # copy, does anything land where the eagle's browser will read it.
        if into.exists():
            shutil.rmtree(into)
        into.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staging), str(into))
        try:
            into.chmod(0o700)
        except OSError:
            pass
        # Pin the profile to Chrome. Chromium cannot decrypt what Chrome
        # encrypted (different keyring entry), and the failure is silent — a
        # browser that looks signed out rather than one that errors.
        (into / ".aethelark-browser-channel").write_text("chrome")
    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        return ImportResult(False, f"The import failed: {e}",
                            guidance=("Nothing was changed. The user's Chrome "
                                      "profile was not modified."))

    empty = [d for d, n in kept.items() if n == 0]
    detail = ("Imported " + ", ".join(f"{d} ({n} cookies)"
                                      for d, n in kept.items() if n)
              if any(kept.values()) else "No cookies matched.")
    if dropped:
        detail += f". Left behind {dropped} cookies from other sites."

    guidance = ""
    if empty:
        guidance = (f"Nothing came across for {', '.join(empty)} — the user "
                    "may not be signed in there in Chrome. Note that Google "
                    "sites (YouTube, Gmail, Drive) keep their sign-in on "
                    "google.com, so import that alongside them.")
    return ImportResult(bool(any(kept.values())), detail, kept, dropped,
                        guidance)
