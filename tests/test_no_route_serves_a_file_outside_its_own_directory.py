"""The dashboard serves files, and it sits above the API keys.

`dashboard/server.py` has two routes that turn a URL segment into a path on
disk: `/uploads/{filename}` and `/static/fonts/{fontname}`. The uploads
directory is `<user data dir>/uploads`, so `../config/api_keys.json` -- the
Gemini key -- is a real file one segment away, and the fonts route is not
authenticated at all. (Uploads used to live in the app checkout, with the same
file two segments away.)

The server binds `0.0.0.0` and `serve()` calls `netaccess.ensure_open(port)`,
so "reachable" means the network, not localhost.

Both hold today. The uploads route resolves and calls `relative_to`; the fonts
route was contained only by the ROUTER — a path parameter does not match "/",
and Starlette decodes percent-encoding before matching, so `..%2f` never
survives to reach the join. That is a property of the framework rather than of
the route, it is invisible while reading the route, and it would quietly stop
holding if anyone switched the parameter to `{fontname:path}`. The fonts route
now checks for itself, and this file measures the outcome rather than either
mechanism.

Earned on criterion 1 in CLAUDE.md: an API key read off the wire cannot be
un-read, and the recovery is to rotate every credential on the machine.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import user_paths  # noqa: E402
from dashboard.server import UPLOADS_DIR, DashboardServer  # noqa: E402

BASE = Path(__file__).resolve().parent.parent
UPLOADS = UPLOADS_DIR
FONTS = BASE / "dashboard" / "static" / "fonts"
#: What each route's traversal aims at: the most valuable real file near it.
TARGETS = {UPLOADS: user_paths.api_keys_path(), FONTS: BASE / "main.py"}


def _climb(from_dir: Path, target: Path) -> str:
    """`../` repeated exactly as far as it really is, computed not guessed.

    The first version of this file used a fixed "../../" for both routes. That
    is the right depth from uploads/ and the wrong depth from
    dashboard/static/fonts/, so the relative payloads aimed at a file that was
    never there and passed against a deliberately broken route. A traversal
    test that does not reach anything proves nothing.
    """
    return os.path.relpath(target.resolve(), from_dir.resolve())


def _payloads(from_dir: Path) -> list:
    """Absolute and relative routes out of one directory, several encodings."""
    rel = _climb(from_dir, TARGETS[from_dir])
    return [
        rel,
        rel.replace("../", "..%2f"),
        rel.replace("../", "%2e%2e%2f"),
        rel.replace("../", "....//"),
        rel.replace("/", "\\"),
        "/etc/passwd",
        "%2Fetc%2Fpasswd",
        "/etc/hostname",
        "..%252fmain.py",
        "../.env",
        "./" + rel,
    ]


@pytest.fixture(autouse=True)
def _targets_exist():
    """The traversal has to aim at something real, or it proves nothing."""
    UPLOADS.mkdir(parents=True, exist_ok=True)
    key = TARGETS[UPLOADS]
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        key.write_text("{}")


@pytest.fixture()
def srv():
    import asyncio
    async def make(): return DashboardServer()
    return asyncio.run(make())


@pytest.fixture()
def client(srv):
    with TestClient(srv.app) as c:
        yield c


def _get(client, url, headers=None):
    """A request that refuses to be a false pass.

    A payload that makes the client itself raise has not proved containment —
    it proved the request never left. Those are reported, not silently counted
    as a refusal.
    """
    try:
        return client.get(url, headers=headers or {})
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"{url} raised {type(e).__name__}: {e}")


@pytest.mark.parametrize("payload", _payloads(UPLOADS))
def test_the_uploads_route_stays_in_the_uploads_directory(srv, client, payload):
    tok = client.post("/login", json={"pin": srv.new_key()}).json()["token"]
    r = _get(client, f"/uploads/{payload}", {"Authorization": f"Bearer {tok}"})
    assert r.status_code != 200, (
        f"/uploads/{payload} served {len(r.content)} bytes from outside the "
        f"uploads directory")


@pytest.mark.parametrize("payload", _payloads(FONTS))
def test_the_fonts_route_stays_in_the_fonts_directory(client, payload):
    """Unauthenticated, so this one needs no token to be worth anything."""
    r = _get(client, f"/static/fonts/{payload}")
    assert r.status_code != 200, (
        f"/static/fonts/{payload} served {len(r.content)} bytes")


@pytest.mark.parametrize("from_dir", [UPLOADS, FONTS])
def test_the_traversals_actually_aim_at_a_file_that_exists(from_dir):
    """The premise, and the thing that was quietly wrong the first time.

    Each relative payload is built by climbing from its own route's directory,
    so it must land on main.py when resolved by hand. If this drifts, the
    traversal tests above stop reaching anything and pass for free.
    """
    target = TARGETS[from_dir]
    landed = (from_dir / _climb(from_dir, target)).resolve()
    assert landed == target.resolve() and landed.exists(), (
        f"a traversal from {from_dir.name} resolves to {landed}, not {target}")
    assert landed.is_file()


# ── and the routes still serve what they are for ────────────────────────────

def test_a_real_upload_can_still_be_fetched(srv, client, tmp_path):
    tok = client.post("/login", json={"pin": srv.new_key()}).json()["token"]
    H = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/upload", headers=H,
                    files={"file": ("note.txt", b"hello there", "text/plain")})
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    try:
        got = client.get(f"/uploads/{name}", headers=H)
        assert got.status_code == 200
        assert got.content == b"hello there"
    finally:
        (Path(__file__).resolve().parent.parent / "uploads" / name).unlink(missing_ok=True)


def test_an_upload_named_like_a_traversal_lands_in_uploads(srv, client):
    """The write side. `Path(name).name` plus the strips must not let a file
    be created outside the directory it is listed from."""
    tok = client.post("/login", json={"pin": srv.new_key()}).json()["token"]
    H = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/upload", headers=H,
                    files={"file": ("../../pwned.txt", b"x", "text/plain")})
    assert r.status_code == 200, r.text
    landed = UPLOADS / r.json()["name"]
    try:
        assert landed.is_file(), "the upload did not land in uploads/"
        assert not (UPLOADS.parent / "pwned.txt").exists(), "an upload escaped the directory"
        assert not (UPLOADS.parent.parent / "pwned.txt").exists()
    finally:
        landed.unlink(missing_ok=True)
