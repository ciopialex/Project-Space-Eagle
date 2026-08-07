"""Connecting YouTube, as one button that knows which wall you are at.

It took four separate discoveries to get this working the first time: sign in
to Google, notice the token predates the YouTube scope, reconnect, then find
that the Data API is switched off on the Cloud project. The eagle blamed the
user's own account for that last one for two rounds running, sending them to
redo a sign-in they had already done correctly.

Nobody should have to learn that sequence. The states are genuinely different
and have genuinely different fixes, so the one thing this must not do is
collapse them into "something is wrong with your account".
"""
from __future__ import annotations

import json
import shutil
import subprocess

#: Every state carries a human label and something to actually do. A status
#: with no next step is a dead end wearing a badge.
STATES = {
    "ready": {
        "label": "Connected",
        "action": "Nothing to do — liked videos, playlists and subscriptions work.",
    },
    "needs_google": {
        "label": "Not connected",
        "action": "Connect your Google account.",
    },
    "needs_scope": {
        "label": "Missing YouTube access",
        "action": "Reconnect Google — one click, and everything else stays connected.",
    },
    "needs_api": {
        "label": "API switched off",
        "action": "Turn on YouTube Data API for this project.",
    },
    "error": {
        "label": "Could not check",
        "action": "Try again in a moment — this looks like a network problem, "
                  "not an account one.",
    },
}


def _scopes() -> list[str]:
    from core import user_paths
    try:
        rec = json.loads((user_paths.config_dir() / "google_token.json")
                         .read_text(encoding="utf-8"))
    except Exception:
        return []
    raw = rec.get("scopes") or rec.get("scope") or []
    return raw.split() if isinstance(raw, str) else list(raw)


def _probe() -> tuple[str, str]:
    """Ask YouTube one cheap question. Returns (state, detail)."""
    from actions.youtube_api import ApiNotEnabled, NeedsReconnect, _API, _get, _token
    token = _token()
    if not token:
        return "needs_scope", ""
    try:
        _get(f"{_API}/playlists", {"part": "id", "mine": "true",
                                   "maxResults": 1}, token)
        return "ok", ""
    except ApiNotEnabled as e:
        return "api_off", str(e)
    except NeedsReconnect:
        return "needs_scope", ""
    except Exception as e:
        # A dropped request is not a permission problem. Saying otherwise sends
        # someone to redo a sign-in that was already fine.
        return "error", str(e)


def youtube_status() -> dict:
    """Which wall, if any, stands between the eagle and this account."""
    scopes = _scopes()
    if not scopes:
        return _state("needs_google")
    if not any("youtube" in s for s in scopes):
        return _state("needs_scope")

    probe, detail = _probe()
    if probe == "ok":
        return _state("ready")
    if probe == "api_off":
        return _state("needs_api", detail=detail, fix_url=_console_url(detail),
                      project=_project_id(detail))
    if probe == "needs_scope":
        return _state("needs_scope")
    return _state("error", detail=detail)


def _state(name: str, **extra) -> dict:
    out = {"state": name, **STATES[name]}
    out.update(extra)
    return out


def _project_id(detail: str) -> str:
    import re
    m = re.search(r"project (\d+)", detail or "")
    return m.group(1) if m else ""


def _console_url(detail: str) -> str:
    project = _project_id(detail)
    if not project:
        return "https://console.cloud.google.com/apis/library/youtube.googleapis.com"
    return ("https://console.developers.google.com/apis/api/"
            f"youtube.googleapis.com/overview?project={project}")


def _gcloud_enable(project: str) -> tuple[bool, str]:
    gcloud = shutil.which("gcloud")
    if not gcloud:
        return False, "gcloud not installed"
    try:
        run = subprocess.run(
            [gcloud, "services", "enable", "youtube.googleapis.com",
             f"--project={project}"],
            capture_output=True, text=True, timeout=120)
    except Exception as e:
        return False, str(e)
    if run.returncode == 0:
        return True, "enabled"
    return False, (run.stderr or run.stdout or "gcloud refused").strip()[:200]


def enable_api(project: str) -> tuple[bool, str]:
    """Switch the API on for `project`, if this machine can.

    Most people do not have gcloud, and claiming success when nothing happened
    is the failure this codebase keeps fighting. When it cannot, it says so and
    hands back the exact console link rather than a shrug.
    """
    ok, detail = _gcloud_enable(project)
    if ok:
        return True, "YouTube Data API enabled."
    return False, (
        f"Could not switch it on from here ({detail}). Open this and press "
        f"Enable: {_console_url('project ' + project)}")
