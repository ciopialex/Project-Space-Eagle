"""The user's own YouTube account, through the API they already authorised.

Built after the browser route was proven impossible for Google, not instead of
trying it. Chrome binds a Google session to the profile that created it, so
importing one into the eagle's browser is detected and revoked — measured on
the real profile: every cookie transferred and decrypted, the browser loaded
all 61 including SID/SAPISID/__Secure-1PSID, and YouTube reported signed out
anyway and deleted LOGIN_INFO. That is session-theft protection working, and
the answer is not to defeat it.

Aethelark is already an authorised OAuth client for this account — the user
signs into Google during onboarding, and the token has a refresh token. So the
question "what did I like most recently" is one HTTPS call against credentials
that already exist: no browser, no window to sign into, nothing revocable, and
about 20x faster than loading and scraping the page.

The user's rule for choosing: use an API over emulation when it does the same
job at least 30-40% faster. Here it is also the difference between working and
not working.

What this deliberately does NOT do is replace `web_agency`. Most of the web has
no API, and emulation is the general capability. This is the narrow, faster
path for one site that happens to have both an API and a hard defence against
the alternative — and when the API cannot answer (watch history, which Google
has not exposed since 2016), it says so and names the route that might.
"""
from __future__ import annotations

import re
from typing import Any

from core.tool_result import ToolResult

_API = "https://www.googleapis.com/youtube/v3"

#: Read aloud, not printed. Fifty titles is a minute of somebody's life, and
#: this is a voice assistant where length is latency.
MAX_ITEMS = 10

#: YouTube's own id for "Liked videos". Reading this playlist returns them
#: newest-first, which is what "my latest liked video" actually means —
#: `videos.list(myRating=liked)` sorts by upload date instead and would answer
#: a different question convincingly.
_LIKED_PLAYLIST = "LL"


#: Returned by `_liked_from_page` when the browser is not signed in. Distinct
#: from [], which means "signed in, and this account has no liked videos".
#: Collapsing the two told an empty account to sign in, and told a signed-out
#: browser that the account was empty.
SIGNED_OUT = object()

#: The liked-videos playlist, as a page rather than an API call.
_LIKED_URL = "https://www.youtube.com/playlist?list=LL"

#: Pull the titles out of a YouTube list page.
#:
#: Deliberately not `#video-title`, which is what every tutorial says and what
#: YouTube used to ship - measured live, that selector now matches ZERO
#: elements. Watch links are the thing that has survived every redesign, so
#: they are what this keys on, deduplicated by video id because each video
#: appears as several links (thumbnail, title, channel).
_TITLES_JS = r"""(() => {
  const seen = new Map();
  for (const a of document.querySelectorAll('a[href*="watch?v="]')) {
    const m = a.href.match(/[?&]v=([\w-]{6,})/);
    if (!m) continue;
    const text = (a.getAttribute('title')
                  || a.getAttribute('aria-label')
                  || a.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text || /^\d+:\d+$/.test(text)) continue;
    if (!seen.has(m[1]) || text.length > seen.get(m[1]).length) {
      seen.set(m[1], text);
    }
  }
  return [...seen.values()];
})()"""

#: YouTube's accessible label ends with the runtime — "... 5 minute și 32 de
#: secunde", "... 16 minute". Fine for a screen reader, noise read aloud.
_DURATION_TAIL = re.compile(
    r"\s+\d+\s*(?:h|hr|hours?|ore|or[ăa]|min|mins?|minutes?|minute|minut|"
    r"sec|secs?|seconds?|secunde|secund[ăa])\b.*$", re.IGNORECASE)


def _clean_title(text: str) -> str:
    return _DURATION_TAIL.sub("", (text or "").strip()).strip(" -–—·")


def _browser():
    from actions.grounding.web.browser import default_browser
    return default_browser()


def _settle(browser, url: str) -> None:
    from actions import web_agency as W
    W._open_and_settle(browser, url)


def _page_blocked(page) -> str:
    from actions import web_agency as W
    return W._wall_or_signed_out(page)


def _liked_from_page(limit: int):
    """Read the liked-videos page in the eagle's own browser.

    Reads FIRST, then judges. The obvious order - check for a sign-in wall,
    then read - does not work on YouTube: its sidebar carries "sign in to like
    videos" on every page while signed out, including pages whose content is
    fully readable. Gating on that made the eagle refuse to read a list it
    could already see. Titles on the page are better evidence that the page
    was readable than the absence of a banner somewhere else on it.

    Returns SIGNED_OUT when there is nothing to read AND the page says why,
    [] when the account genuinely has no liked videos.
    """
    browser = _browser()
    browser.start()
    if not browser.running:
        return SIGNED_OUT
    _settle(browser, _LIKED_URL)
    page = browser.page()
    if page is None:
        return SIGNED_OUT

    raw = browser.call(lambda pg: pg.evaluate(_TITLES_JS), timeout=30.0) or []
    titles = [t for t in (_clean_title(x) for x in raw) if t][:limit]
    if titles:
        return titles
    return SIGNED_OUT if _page_blocked(page) else []


_ACTIONS = ("liked", "subscriptions", "playlists", "history")


class ApiNotEnabled(Exception):
    """The YouTube Data API is switched off on the Google Cloud project.

    Its own type because it is indistinguishable from a scope problem over the
    wire - both are 403 - and the remedies have nothing in common. The user
    reconnected Google, granted YouTube, and was still told to "sign in to
    Google again", which is the same sin as claiming their data is private:
    sending somebody to redo work they have already done.
    """


class NeedsReconnect(Exception):
    """The token is missing, expired, or predates the YouTube scope.

    Its own type because the remedy is one click of the user's, and it must
    never be reported as "YouTube did not answer" - true, useless, and it
    reads as a broken service rather than a consent the user has not given
    yet. Every existing token is in this state until they reconnect once.
    """


def _token() -> str | None:
    from actions.google_auth import get_access_token
    return get_access_token()


def _get(url: str, params: dict, token: str) -> dict:
    import requests
    response = requests.get(url, params=params, timeout=15,
                            headers={"Authorization": f"Bearer {token}"})
    if response.status_code in (401, 403):
        # Read the REASON. accessNotConfigured means the API is disabled on
        # the Cloud project and no amount of signing in will help.
        try:
            error = response.json().get("error", {})
            reasons = {d.get("reason") for d in error.get("errors", [])}
            if "accessNotConfigured" in reasons:
                raise ApiNotEnabled(error.get("message", ""))
        except ApiNotEnabled:
            raise
        except Exception:
            pass
        raise NeedsReconnect(str(response.status_code))
    response.raise_for_status()
    return response.json()


def _titles(payload: dict) -> list[str]:
    out = []
    for item in (payload.get("items") or []):
        snippet = item.get("snippet") or {}
        title = (snippet.get("title") or "").strip()
        if not title:
            continue
        channel = (snippet.get("videoOwnerChannelTitle")
                   or snippet.get("channelTitle") or "").strip()
        out.append(f"{title} — {channel}" if channel else title)
    return out


def _speak(items: list[str], noun: str, limit: int) -> ToolResult:
    if not items:
        return ToolResult.success(f"There are no {noun} on this account.")
    shown = items[:limit]
    return ToolResult.success("\n".join(shown), count=len(items))


def _api_disabled(detail: str) -> ToolResult:
    """The API is off at the Cloud project. Their account and sign-in are fine."""
    m = re.search(r"project (\d+)", detail or "")
    where = (f"https://console.developers.google.com/apis/api/"
             f"youtube.googleapis.com/overview?project={m.group(1)}" if m else
             "https://console.cloud.google.com/apis/library/youtube.googleapis.com")
    return ToolResult.failure(
        "The YouTube Data API is switched off on this Google Cloud project - "
        "the user's account and sign-in are fine.",
        guidance=(f"Either enable YouTube Data API v3 at {where} and retry in a "
                  f"minute, or call web_agency action='sign_in' with {_LIKED_URL} "
                  "so the eagle reads the page instead. Do NOT ask them to sign "
                  "in to Google again - they already have, and it will not help. "
                  "Never say their videos are private."))


def _youtube_api(params: dict) -> ToolResult:
    action = str((params or {}).get("action") or "liked").strip().lower()
    if action not in _ACTIONS:
        return ToolResult.failure(
            f"'{action}' is not something this tool does.",
            guidance=f"Use one of: {', '.join(_ACTIONS)}.")

    # Said plainly because it is TRUE. The failure this codebase guards against
    # is claiming a limit that does not exist — not admitting one that does.
    # Google removed the watch-history API in 2016 and never replaced it.
    if action == "history":
        return ToolResult.failure(
            "Google does not expose watch history through the YouTube API — "
            "it was removed in 2016 and never replaced.",
            guidance=("This one genuinely needs the page. Use web_agency on "
                      "youtube.com/feed/history, which will need the user "
                      "signed into the eagle's browser once."))

    token = _token()

    if not token:
        return ToolResult.failure(
            "Aethelark needs the user to sign in to Google again — the "
            "existing connection does not cover YouTube.",
            guidance=("One click, in Aethelark's Google connection. Everything "
                      "they already connected stays connected. Do NOT tell "
                      "them their videos are private."))

    try:
        limit = max(1, min(int(params.get("limit") or 5), MAX_ITEMS))
    except Exception:
        limit = 5

    reconnect = ToolResult.failure(
        "Aethelark needs the user to sign in to Google again — the existing "
        "connection does not cover YouTube.",
        guidance=("One click, in Aethelark's Google connection. Everything "
                  "they already connected stays connected. Do NOT tell them "
                  "their videos are private; nothing is private here, the "
                  "eagle simply has not been given YouTube access yet."))

    try:
        if action == "liked":
            data = _get(f"{_API}/playlistItems",
                        {"part": "snippet", "playlistId": _LIKED_PLAYLIST,
                         "maxResults": min(limit, 50)}, token)
            return _speak(_titles(data), "liked videos", limit)

        if action == "subscriptions":
            data = _get(f"{_API}/subscriptions",
                        {"part": "snippet", "mine": "true",
                         "maxResults": min(limit, 50),
                         "order": "unread"}, token)
            return _speak(_titles(data), "subscriptions", limit)

        data = _get(f"{_API}/playlists",
                    {"part": "snippet", "mine": "true",
                     "maxResults": min(limit, 50)}, token)
        return _speak(_titles(data), "playlists", limit)

    except (ApiNotEnabled, NeedsReconnect) as first_choice_failed:
        # The page fallback is for `liked` only - it is the one with a page
        # worth reading. Every OTHER action still gets a real answer below,
        # rather than being re-raised into the generic handler, which is what
        # leaked "unexpected error: project 145572196912" to the user.
        if action == "liked":
            titles = _liked_from_page(limit)
            if titles is not SIGNED_OUT:
                return (ToolResult.success("\n".join(titles), count=len(titles),
                                           via="browser") if titles
                        else ToolResult.success(
                            "There are no liked videos on this account."))

        if isinstance(first_choice_failed, NeedsReconnect):
            return reconnect
        return _api_disabled(str(first_choice_failed))

    except Exception as e:
        return ToolResult.failure(
            f"YouTube did not answer: {e}",
            guidance=("Do not tell the user their data is private — this is a "
                      "request that failed, not a permission they lack. If it "
                      "keeps failing, web_agency can read the page instead."))


def youtube_api(parameters: dict | None = None, **_ignored: Any) -> ToolResult:
    """Read the user's own YouTube account. Never raises."""
    try:
        return _youtube_api(parameters or {})
    except Exception as e:
        return ToolResult.failure(
            f"The YouTube tool hit an unexpected error: {e}",
            guidance="Tell the user it failed; do not claim it worked.")
