"""A pairing key that never expires, on a login that never says no.

Tokens went into a set that only grew — no expiry, no revocation, valid for
the lifetime of the process. /login accepted unlimited attempts against a
6-character key drawn from a 31-character alphabet. And /api/device-login
returned the session key itself, in cleartext, over plain HTTP.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.server import DashboardServer  # noqa: E402


@pytest.fixture()
def srv():
    import asyncio
    async def make(): return DashboardServer()
    return asyncio.run(make())


@pytest.fixture()
def client(srv):
    with TestClient(srv.app) as c:
        yield c


def test_a_valid_key_returns_a_token(srv, client):
    r = client.post("/login", json={"pin": srv.new_key()})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(r.json()["token"]) >= 32


def test_a_key_works_exactly_once(srv, client):
    key = srv.new_key()
    assert client.post("/login", json={"pin": key}).status_code == 200
    assert client.post("/login", json={"pin": key}).status_code == 401


def test_the_key_is_accepted_case_insensitively(srv, client):
    key = srv.new_key()
    assert client.post("/login", json={"pin": key.lower()}).status_code == 200


def test_an_expired_key_is_refused(srv, client):
    key = srv.new_key(expiry_secs=0)
    assert client.post("/login", json={"pin": key}).status_code == 401


def test_a_wrong_key_is_refused(client):
    assert client.post("/login", json={"pin": "ZZZZZZ"}).status_code == 401


def test_brute_force_is_rate_limited(client):
    codes = [client.post("/login", json={"pin": "ZZZZZZ"}).status_code
             for _ in range(40)]
    assert 429 in codes, "unlimited guesses against a 6-character key"


def test_an_unauthenticated_request_reaches_nothing(client):
    for method, path in (("post", "/api/command"), ("post", "/api/wake"),
                         ("get", "/api/files"), ("post", "/api/revoke-devices"),
                         ("get", "/api/swarm/events")):
        assert getattr(client, method)(path).status_code == 401, path


def test_a_forged_bearer_token_is_refused(client):
    r = client.get("/api/files", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_tokens_expire(srv, client, monkeypatch):
    monkeypatch.setattr(srv, "TOKEN_TTL", 0.0)
    tok = client.post("/login", json={"pin": srv.new_key()}).json()["token"]
    assert client.get("/api/files",
                      headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_the_token_table_is_capped(srv, client):
    for _ in range(srv.MAX_TOKENS + 20):
        client.post("/login", json={"pin": srv.new_key()})
    assert len(srv._tokens) <= srv.MAX_TOKENS


def test_device_login_never_returns_the_session_secret(srv, client):
    """It used to return {'ok':..,'token':..,'key': session_key} in cleartext."""
    key = srv.new_key()
    page = client.get(f"/auto-login?key={key}").text
    dev = page.split("aethelark_device_token','")[1].split("'")[0]
    body = client.post("/api/device-login", json={"device_token": dev}).json()
    assert body["ok"] is True and body["token"]
    assert key not in str(body), "the pairing key came back over the wire"


def test_an_unknown_device_token_is_refused(client):
    assert client.post("/api/device-login",
                       json={"device_token": "nope"}).status_code == 401


def test_revoking_devices_stops_them_reconnecting(srv, client):
    key = srv.new_key()
    page = client.get(f"/auto-login?key={key}").text
    dev = page.split("aethelark_device_token','")[1].split("'")[0]
    tok = client.post("/login", json={"pin": srv.new_key()}).json()["token"]
    r = client.post("/api/revoke-devices", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["revoked"] >= 1
    assert client.post("/api/device-login",
                       json={"device_token": dev}).status_code == 401


def test_an_expired_auto_login_link_says_so(client):
    assert "Link Expired" in client.get("/auto-login?key=ZZZZZZ").text


def test_a_command_arrives_on_the_queue_main_py_reads(srv, client):
    tok = client.post("/login", json={"pin": srv.new_key()}).json()["token"]
    r = client.post("/api/command", json={"text": "lights on"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert srv._command_queue.get_nowait() == "lights on"


def test_an_encrypted_command_round_trips(srv, client):
    from dashboard import crypto
    tok = client.post("/login", json={"pin": srv.new_key()}).json()["token"]
    secret = srv._secret_for(tok)
    blob = crypto.encrypt(crypto.derive(secret), "open the pod bay doors")
    r = client.post("/api/command", json={"enc": blob},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert srv._command_queue.get_nowait() == "open the pod bay doors"


def test_a_tampered_command_is_refused_and_never_queued(srv, client):
    from dashboard import crypto
    tok = client.post("/login", json={"pin": srv.new_key()}).json()["token"]
    blob = crypto.encrypt(crypto.derive(srv._secret_for(tok)), "safe")
    broken = blob[:-6] + ("A" if blob[-6] != "A" else "B") + blob[-5:]
    r = client.post("/api/command", json={"enc": broken},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400
    assert srv._command_queue.empty()


def test_wake_admits_when_nothing_is_listening(srv, client):
    """It used to return {'ok': True} while the callback was never assigned by
    anyone, and the phone printed 'Sending wake signal...'."""
    tok = client.post("/login", json={"pin": srv.new_key()}).json()["token"]
    body = client.post("/api/wake", headers={"Authorization": f"Bearer {tok}"}).json()
    assert body["delivered"] is False


def test_wake_reports_delivery_when_something_is_listening(srv, client):
    fired = []
    srv.set_wake_callback(lambda: fired.append(1))
    tok = client.post("/login", json={"pin": srv.new_key()}).json()["token"]
    body = client.post("/api/wake", headers={"Authorization": f"Bearer {tok}"}).json()
    assert body["delivered"] is True and fired == [1]
