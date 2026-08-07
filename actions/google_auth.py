"""Google sign-in for onboarding — Desktop OAuth (loopback + PKCE).

Design notes (why it's built this way):
  • Desktop apps can't keep a secret, so we use the PKCE flow with a loopback
    redirect (http://127.0.0.1:<port>). The client_id is NOT a secret and may
    ship; there is no client_secret in this flow.
  • It's a SEAM: if no client_id is configured yet, sign_in_google() returns
    {"status": "not_configured"} so the onboarding shell still runs end-to-end
    (Guest mode). Drop a client_id into config/api_keys.json → it goes live with
    zero code changes.
  • Scopes are minimal: identity (openid/email/profile) + gmail.readonly so the
    email-briefing connector can build on the same grant later.
  • Tokens are written to config/google_token.json. (A later hardening pass can
    move these into the OS keyring.)

Reality check baked into the plan: gmail.readonly is a *restricted* scope.
Unverified, this works for the developer + up to 100 test users — enough for now.
Public distribution later needs Google's OAuth verification.
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from core import user_paths

BASE = pathlib.Path(__file__).resolve().parent.parent
CONFIG = user_paths.api_keys_path()
TOKEN_STORE = user_paths.google_token_path()

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
USERINFO_URI = "https://openidconnect.googleapis.com/v1/userinfo"
# One consent covers the eagle's Google surface. Read-leaning + light write
# (calendar events, tasks) so it can brief AND act. Each maps to an API you enable
# in the Cloud project (Gmail, Calendar, People, Tasks). Trim this list if you want
# a smaller consent — messages_brief only needs gmail.readonly.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.modify",          # read + mark-as-read
    "https://www.googleapis.com/auth/gmail.readonly",        # read mail (briefing)
    "https://www.googleapis.com/auth/calendar.events",       # read + create events
    "https://www.googleapis.com/auth/calendar.readonly",     # read calendars/availability
    "https://www.googleapis.com/auth/contacts.readonly",     # resolve people (People API)
    "https://www.googleapis.com/auth/tasks",                 # Google Tasks
    # Read-only, and it replaces an entire class of browser work. A Google
    # session cannot be lifted into the eagle's browser - Chrome binds it to
    # the profile that created it - so the user's own liked videos, playlists
    # and subscriptions are reachable through the API or not at all.
    "https://www.googleapis.com/auth/youtube.readonly",      # own playlists/likes
]


def _load_client_id() -> str:
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        return (cfg.get("google_client_id") or "").strip()
    except Exception:
        return ""


def _load_client_secret() -> str:
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        return (cfg.get("google_client_secret") or "").strip()
    except Exception:
        return ""


def set_client_secret(secret: str) -> bool:
    """Persist the Desktop client secret (required by Google's token endpoint)."""
    secret = (secret or "").strip()
    if not secret:
        return False
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["google_client_secret"] = secret
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    return True


def set_client_id(client_id: str) -> bool:
    """Persist the Google OAuth Desktop client ID into config (merge-safe)."""
    client_id = (client_id or "").strip()
    if not client_id:
        return False
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["google_client_id"] = client_id
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    return True


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


class _CatchHandler(BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None
    state_expected: str = ""

    def do_GET(self):  # noqa: N802
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        code = (params.get("code") or [None])[0]
        error = (params.get("error") or [None])[0]
        got_state = (params.get("state") or [""])[0]

        # The browser also fires incidental requests (notably /favicon.ico) right
        # after the redirect. Those carry no code — and the old version wrote them
        # straight onto the class attrs, NULLING an already-captured code, so the
        # waiting flow timed out even though consent had succeeded. Ignore them.
        if not code and not error:
            self.send_response(204)
            self.end_headers()
            return

        ok = bool(code) and got_state == _CatchHandler.state_expected
        # Record only the FIRST real callback; never clobber a captured result.
        if _CatchHandler.code is None and _CatchHandler.error is None:
            if ok:
                _CatchHandler.code = code
            else:
                _CatchHandler.error = error or "state_mismatch"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = ("Aethelark is connected. You can return to the eagle."
               if ok else "Sign-in could not be completed. Return to the eagle and retry.")
        self.wfile.write(
            f"<html><body style='background:#0A0A0A;color:#E5E5EA;font-family:sans-serif;"
            f"display:grid;place-items:center;height:100vh;margin:0'>"
            f"<div style='text-align:center'><h2 style='letter-spacing:.1em'>AETHELARK</h2>"
            f"<p style='color:#7C7C86'>{msg}</p></div></body></html>".encode()
        )

    def log_message(self, *_):  # silence the default stderr logging
        pass


def _exchange_token(client_id: str, code: str, verifier: str, redirect_uri: str) -> dict:
    # Google's "Desktop app" (installed) clients ARE issued a client_secret and the
    # token endpoint REQUIRES it — PKCE alone returns 400 Bad Request. It isn't a
    # true secret (it ships inside the app), but Google still validates it.
    payload = {
        "client_id": client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    secret = _load_client_secret()
    if secret:
        payload["client_secret"] = secret
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(TOKEN_URI, data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _fetch_userinfo(access_token: str) -> dict:
    req = urllib.request.Request(USERINFO_URI,
                                 headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


def sign_in_google(timeout_s: float = 600.0) -> dict:
    """Run the interactive desktop OAuth flow. Blocking — call from a worker
    thread. Returns a status dict the onboarding UI can render."""
    client_id = _load_client_id()
    if not client_id:
        return {
            "status": "not_configured",
            "message": ("Google sign-in isn't wired to credentials yet. "
                        "Add 'google_client_id' to config/api_keys.json to enable it. "
                        "You can continue as Guest for now."),
        }

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    # Loopback server on an OS-assigned free port.
    server = HTTPServer(("127.0.0.1", 0), _CatchHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/"
    _CatchHandler.code = None
    _CatchHandler.error = None
    _CatchHandler.state_expected = state

    auth_url = AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    })

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    # Print the URL too: each run listens on a NEW random loopback port, so an old
    # consent tab redirects to a dead port and silently does nothing. Having the
    # current URL lets the user open the right one if the browser didn't launch.
    print(f"[GoogleAuth] Open this URL to approve (port {port}):\n{auth_url}", flush=True)
    webbrowser.open(auth_url)

    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            if _CatchHandler.code or _CatchHandler.error:
                break
            time.sleep(0.2)
    finally:
        server.shutdown()

    if _CatchHandler.error:
        return {"status": "error", "message": f"Google returned: {_CatchHandler.error}"}
    if not _CatchHandler.code:
        return {"status": "timeout", "message": "Sign-in timed out. Please retry."}

    try:
        tok = _exchange_token(client_id, _CatchHandler.code, verifier, redirect_uri)
    except Exception as e:
        return {"status": "error", "message": f"Token exchange failed: {e}"}

    info = _fetch_userinfo(tok.get("access_token", "")) if tok.get("access_token") else {}
    record = {
        "provider": "google",
        "email": info.get("email", ""),
        "name": info.get("name", ""),
        "access_token": tok.get("access_token", ""),
        "refresh_token": tok.get("refresh_token", ""),
        "expires_at": time.time() + float(tok.get("expires_in", 0) or 0),
        "scopes": SCOPES,
        "obtained_at": time.time(),
    }
    try:
        TOKEN_STORE.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception as _e:
        print(f"[google_auth.py] Non-fatal error at line 261: {_e}")

    return {"status": "ok", "email": record["email"], "name": record["name"]}


def is_connected() -> bool:
    try:
        rec = json.loads(TOKEN_STORE.read_text(encoding="utf-8"))
        return bool(rec.get("refresh_token") or rec.get("access_token"))
    except Exception:
        return False


def get_access_token() -> str | None:
    """A valid Gmail access token, refreshing via the stored refresh_token when
    the current one has expired. Returns None if Google isn't connected. Desktop
    PKCE flow → refresh needs only client_id (no secret)."""
    try:
        rec = json.loads(TOKEN_STORE.read_text(encoding="utf-8"))
    except Exception:
        return None

    at = rec.get("access_token")
    if at and float(rec.get("expires_at", 0) or 0) > time.time() + 60:
        return at  # still valid

    refresh = rec.get("refresh_token")
    client_id = _load_client_id()
    if not (refresh and client_id):
        return at  # can't refresh — hand back whatever we have (may be expired)

    try:
        refresh_payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": client_id,
        }
        _secret = _load_client_secret()
        if _secret:  # Desktop clients must send the secret on refresh too
            refresh_payload["client_secret"] = _secret
        data = urllib.parse.urlencode(refresh_payload).encode()
        req = urllib.request.Request(
            TOKEN_URI, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
        new_at = resp.get("access_token")
        if new_at:
            rec["access_token"] = new_at
            rec["expires_at"] = time.time() + float(resp.get("expires_in", 3600) or 3600)
            try:
                TOKEN_STORE.write_text(json.dumps(rec, indent=2), encoding="utf-8")
            except Exception as _e:
                print(f"[google_auth.py] Non-fatal error at line 314: {_e}")
            return new_at
    except Exception as e:
        print(f"[google_auth] token refresh failed: {e}")
    return at


def connected_account() -> dict | None:
    """The signed-in Google account for the Settings panel, or None. Returns only
    the non-secret identity fields — never the tokens themselves."""
    try:
        rec = json.loads(TOKEN_STORE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not (rec.get("refresh_token") or rec.get("access_token")):
        return None
    return {"email": rec.get("email", ""), "name": rec.get("name", "")}


def disconnect_google() -> bool:
    """Forget the local Google grant. Best-effort token revocation, then delete
    the on-disk token so is_connected() flips to False."""
    try:
        rec = json.loads(TOKEN_STORE.read_text(encoding="utf-8"))
    except Exception:
        rec = {}
    tok = rec.get("refresh_token") or rec.get("access_token")
    if tok:
        try:
            data = urllib.parse.urlencode({"token": tok}).encode()
            req = urllib.request.Request(
                "https://oauth2.googleapis.com/revoke", data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass  # revocation is best-effort; local deletion is what matters
    try:
        TOKEN_STORE.unlink(missing_ok=True)
    except Exception as _e:
        print(f"[google_auth.py] Non-fatal error at line 353: {_e}")
    return True


if __name__ == "__main__":
    print(json.dumps(sign_in_google(), indent=2))
