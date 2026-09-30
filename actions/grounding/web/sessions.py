"""Which sites the eagle's browser is signed into.

One record, one way in. The eagle gets access to a site exactly one way: a
human signs in once, in the eagle's own window. That is the only route that
works everywhere — it needs no API, no developer console, no per-site
integration, and no site can tell it apart from a person, because it is one.

The alternative that used to live here, copying sessions out of the user's
Chrome, is gone. It could not work for Google at all (Chrome binds a session
to the profile that created it, so a copy is detected and revoked), and while
failing it deleted a session the user had just created by hand. A second path
that works sometimes, silently, is worse than no second path.
"""
from __future__ import annotations

from pathlib import Path

#: Kept at its old filename so existing profiles keep their record.
MARKER = ".aethelark-imported"


def normalise(domain: str) -> str:
    """'https://www.YouTube.com/feed' -> 'youtube.com'."""
    d = (domain or "").strip().lower()
    for prefix in ("https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    d = d.split("/")[0].split("?")[0]
    if d.startswith("www."):
        d = d[4:]
    return d.strip(".")


def signed_in_sites(profile: Path) -> list[str]:
    try:
        raw = (profile / MARKER).read_text()
    except Exception:
        return []
    return sorted({d.strip() for d in raw.splitlines() if d.strip()})


def record(profile: Path, domain: str) -> None:
    """Note a completed sign-in. Never raises: bookkeeping must not fail a
    sign-in that already succeeded."""
    site = normalise(domain)
    if not site:
        return
    try:
        marker = profile / MARKER
        have = set(marker.read_text().split()) if marker.exists() else set()
        if site in have:
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("\n".join(sorted(have | {site})))
    except Exception:
        pass
