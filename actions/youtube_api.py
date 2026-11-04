"""The user's own YouTube account through the YouTube Data API, using the Google
token from Settings -> Connect Google (needs a configured OAuth client). One HTTPS
call, about 20x faster than scraping. Without a token, `liked` falls back to
reading the page in the eagle's browser.

Importing a Google session into another browser profile does not work: Google
detects and revokes it.

Not a replacement for web_agency. When the API cannot answer (watch history,
unexposed since 2016), it says so.
"""
from __future__ import annotations

import re
from typing import Any

from core.tool_result import ToolResult

_API = "https://www.googleapis.com/youtube/v3"

# Read aloud: length is latency.
MAX_ITEMS = 10

# YouTube's id for "Liked videos", newest first. videos.list(myRating=liked) sorts by
# upload date instead.
_LIKED_PLAYLIST = "LL"


# Returned when the browser is not signed in; [] means signed in with no liked videos.
SIGNED_OUT = object()

# The liked-videos playlist as a page.
_LIKED_URL = "https://www.youtube.com/playlist?list=LL"

# Titles from a YouTube list page, keyed on watch links (stable across redesigns;
# #video-title matches nothing now), deduplicated by video id.
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

# YouTube's accessible label ends with the runtime; strip it.
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
    """Read the liked-videos page in the eagle's browser. Reads first, then judges:
    YouTube's sidebar says "sign in" even on readable pages. SIGNED_OUT when there
    is nothing to read and the page says why; [] when there are no liked videos.
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
    """The YouTube Data API is off on the Cloud project. Its own type: it looks like
    a scope problem (both 403) but signing in again does not help.
    """


class NeedsReconnect(Exception):
    """The token is missing, expired, or predates the YouTube scope: one reconnect
    fixes it.
    """


def _token() -> str | None:
    from actions.google_auth import get_access_token
    return get_access_token()


def _get(url: str, params: dict, token: str) -> dict:
    import requests
    response = requests.get(url, params=params, timeout=15,
                            headers={"Authorization": f"Bearer {token}"})
    if response.status_code in (401, 403):
        # accessNotConfigured: the API is disabled on the Cloud project.
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
    """One line per item: the spoken title, then the exact link, so the next tool
    does not have to search again (titles collide). In the message because `data`
    never reaches the model; core/prompt.txt forbids reading URLs aloud.
    """
    out = []
    for item in (payload.get("items") or []):
        snippet = item.get("snippet") or {}
        title = (snippet.get("title") or "").strip()
        if not title:
            continue
        channel = (snippet.get("videoOwnerChannelTitle")
                   or snippet.get("channelTitle") or "").strip()
        line = f"{title} — {channel}" if channel else title

        # playlistItems: snippet.resourceId; videos/search: item.id, sometimes a dict.
        vid = (snippet.get("resourceId") or {}).get("videoId")
        if not vid:
            raw_id = item.get("id")
            if isinstance(raw_id, str):
                vid = raw_id
            elif isinstance(raw_id, dict):
                vid = raw_id.get("videoId")
        if isinstance(vid, str) and vid.strip():
            line += f"  [link, do not read aloud: https://www.youtube.com/watch?v={vid.strip()}]"
        out.append(line)
    return out


def _speak(items: list[str], noun: str, limit: int) -> ToolResult:
    if not items:
        return ToolResult.success(f"There are no {noun} on this account.")
    shown = items[:limit]
    return ToolResult.success("\n".join(shown), count=len(items))


def _api_disabled(detail: str) -> ToolResult:
    """The API is off at the Cloud project; the account and sign-in are fine."""
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

    # True: Google removed the watch-history API in 2016.
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
        # The page fallback is only for `liked`; other actions still get a real answer.
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
