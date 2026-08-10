#youtube_video.py
import json
import re
import sys
import time
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from urllib.parse import quote_plus

try:
    import pyautogui
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    _NUMPY = False

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _TRANSCRIPT_OK = True
except ImportError:
    _TRANSCRIPT_OK = False

from config import get_os, is_windows, is_mac, is_linux
from core import user_paths
from core.tool_result import Failed, ToolResult, settled


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = user_paths.api_keys_path()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_YT_VIDEO_FILTER = "EgIQAQ%3D%3D"


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _open_url(url: str) -> None:
    try:
        if is_mac():
            subprocess.Popen(["open", url])
        elif is_linux():
            subprocess.Popen(["xdg-open", url])
        else:
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
    except Exception as e:
        print(f"[YouTube] ⚠️ open_url failed: {e}")

def _scrape_first_video_url(query: str) -> str | None:

    if not _REQUESTS_OK:
        return None

    search_url = (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}"
        f"&sp={_YT_VIDEO_FILTER}"
    )

    try:
        r    = requests.get(search_url, headers=HEADERS, timeout=10)
        html = r.text

        video_ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html)

        seen = set()
        for vid in video_ids:
            if vid in seen:
                continue
            seen.add(vid)

            if f'/shorts/{vid}' in html:
                continue
            return f"https://www.youtube.com/watch?v={vid}"

    except Exception as e:
        print(f"[YouTube] ⚠️ scrape_first_video_url failed: {e}")

    return None

def _extract_video_id(url: str) -> str | None:
    match = re.search(
        r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/)([A-Za-z0-9_-]{11})", url
    )
    return match.group(1) if match else None


def _is_valid_youtube_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)", url or ""))


def _ask_for_url(prompt_text: str = "YouTube video URL:") -> str | None:
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk._default_root
        if root is None:
            root = tk.Tk()
            root.withdraw()

        url = simpledialog.askstring("Aethelark", prompt_text, parent=root)
        return url.strip() if url else None
    except Exception as e:
        print(f"[YouTube] ⚠️ URL dialog failed: {e}")
        return None


#: Tried in order. English first because most captioned content has it, then
#: the languages this user actually speaks, then anything at all - a Romanian
#: video with only Romanian captions is still summarisable.
_TRANSCRIPT_LANGS = ("en", "ro", "es", "de", "fr", "it", "pt", "tr",
                     "ru", "ja", "ko", "ar", "zh")


def _get_transcript(video_id: str) -> str | None:
    """The video's captions as plain text, or None if it genuinely has none.

    This was calling `YouTubeTranscriptApi.list_transcripts`, which the library
    removed. Every fetch raised AttributeError, was caught, logged as a
    non-fatal error and returned None - so the eagle reported "no transcript
    available" for every video on YouTube, including ones with perfectly good
    captions, while its own tool description advertised summarising them.

    A dead capability that is still declared is the mirror of reporting a
    limit you have not hit, and it costs the user more, because they ask for
    something the tool said it could do.
    """
    if not _TRANSCRIPT_OK:
        return None
    try:
        api = YouTubeTranscriptApi()
    except Exception as e:
        print(f"[YouTube] transcript API unavailable: {e}")
        return None

    # One language at a time rather than one call with the whole list: the
    # library returns the FIRST match for a list, and asking separately keeps
    # the preference order meaningful when a video carries several tracks.
    last_error = None
    for lang in _TRANSCRIPT_LANGS:
        try:
            fetched = api.fetch(video_id, languages=[lang])
        except Exception as e:
            last_error = e
            continue
        text = " ".join(
            (getattr(snippet, "text", "") or "") for snippet in fetched).strip()
        if text:
            return text

    try:                       # last resort: whatever track exists
        fetched = api.fetch(video_id)
        text = " ".join(
            (getattr(snippet, "text", "") or "") for snippet in fetched).strip()
        if text:
            return text
    except Exception as e:
        last_error = e

    print(f"[YouTube] no transcript for {video_id}: {last_error}")
    return None


def _summarize_with_gemini(transcript: str, video_url: str) -> str:
    from google import genai as _genai
    from google.genai import types

    _client = _genai.Client(api_key=_get_api_key())
    max_chars = 80000
    truncated = transcript[:max_chars] + ("..." if len(transcript) > max_chars else "")
    response  = _client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"Please summarize this YouTube video transcript:\n\n{truncated}",
        config=types.GenerateContentConfig(
            system_instruction=(
                "You are Aethelark, an AI assistant. "
                "Summarize YouTube video transcripts clearly and concisely. "
                "Structure: 1-sentence overview, then 3-5 key points. "
                "Be direct. Address the user as 'sir'. "
                "Match the language of the transcript."
            )
        )
    )
    return response.text.strip()


def _save_summary(content: str, video_url: str) -> str:
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"youtube_summary_{ts}.txt"
    desktop  = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    filepath = desktop / filename

    header = (
        f"Aethelark — YouTube Summary\n"
        f"{'─' * 50}\n"
        f"URL    : {video_url}\n"
        f"Date   : {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{'─' * 50}\n\n"
    )
    filepath.write_text(header + content, encoding="utf-8")

    try:
        if is_windows():
            subprocess.Popen(["notepad.exe", str(filepath)])
        elif is_mac():
            subprocess.Popen(["open", "-t", str(filepath)])
        else:
            subprocess.Popen(["xdg-open", str(filepath)])
    except Exception as e:
        print(f"[YouTube] ⚠️ Could not open text editor: {e}")

    return str(filepath)


def _scrape_video_info(video_id: str) -> dict:
    if not _REQUESTS_OK:
        return {}
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=12)
        html = r.text
        info = {}

        for key, pattern in [
            ("title",    r'"title":\{"runs":\[\{"text":"([^"]+)"'),
            ("channel",  r'"ownerChannelName":"([^"]+)"'),
            ("views",    r'"viewCount":"(\d+)"'),
            ("duration", r'"lengthSeconds":"(\d+)"'),
            ("likes",    r'"label":"([0-9,]+ likes)"'),
        ]:
            match = re.search(pattern, html)
            if match:
                raw = match.group(1)
                if key == "views":
                    info[key] = f"{int(raw):,}"
                elif key == "duration":
                    secs = int(raw)
                    info[key] = f"{secs // 60}:{secs % 60:02d}"
                else:
                    info[key] = raw

        return info
    except Exception as e:
        print(f"[YouTube] ⚠️ Info scrape failed: {e}")
        return {}


def _scrape_trending(region: str = "TR", max_results: int = 8) -> list[dict]:
    if not _REQUESTS_OK:
        return []
    url = f"https://www.youtube.com/feed/trending?gl={region.upper()}"
    try:
        r    = requests.get(url, headers=HEADERS, timeout=12)
        html = r.text

        titles   = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}\]', html)
        channels = re.findall(r'"ownerText":\{"runs":\[\{"text":"([^"]+)"', html)

        results, seen = [], set()
        for i, title in enumerate(titles):
            if title in seen or len(title) < 5:
                continue
            seen.add(title)
            channel = channels[i] if i < len(channels) else "Unknown"
            results.append({"rank": len(results) + 1, "title": title, "channel": channel})
            if len(results) >= max_results:
                break

        return results
    except Exception as e:
        print(f"[YouTube] ⚠️ Trending scrape failed: {e}")
        return []

def _handle_play(parameters: dict, player) -> str:
    query = parameters.get("query", "").strip()
    if not query:
        return "Please tell me what you'd like to watch."

    if player:
        player.write_log(f"[YouTube] Searching: {query}")

    print(f"[YouTube] 🔍 Scraping first non-Shorts video for: {query}")

    video_url = _scrape_first_video_url(query)

    if video_url:
        print(f"[YouTube] ▶️ Opening: {video_url}")
        _open_url(video_url)
        return f"Playing: {query}"

    print(f"[YouTube] ⚠️ Scrape failed, opening filtered search page")
    fallback_url = (
        f"https://www.youtube.com/results"
        f"?search_query={quote_plus(query)}"
        f"&sp={_YT_VIDEO_FILTER}"
    )
    _open_url(fallback_url)
    return f"Opened YouTube search for: {query} (manual selection required)"


def _handle_summarize(parameters: dict, player, speak) -> str:
    if not _TRANSCRIPT_OK:
        return Failed("youtube-transcript-api is not installed. Run: pip install youtube-transcript-api",
                      guidance="Tell the user the dependency is missing; nothing was summarised.")

    # Use what the model was given. This used to go straight to a GUI box and
    # wait for a paste, ignoring `url` entirely - so "summarise this video"
    # by voice was impossible, and whatever the user happened to paste is what
    # got summarised. Live, that produced a confident summary of a completely
    # different video. A voice assistant that opens a dialog and waits is not
    # a voice assistant.
    url = str(parameters.get("url") or parameters.get("query") or "").strip()
    if not url:
        return Failed("I need to know which video.",
                      guidance="Ask the user for the link or the exact title. Do not guess one.")
    if not _is_valid_youtube_url(url):
        # Not a link — treat it as a title and look it up, which is exactly
        # what `play` already does with the same input. Straight from a real
        # session: `youtube_api` returned "Put Yourself First & Success Will
        # Follow Relentlessly | Napoleon Hill", the model passed that title
        # here, and this refused it — while `_scrape_first_video_url` sat 250
        # lines up in this same file, doing precisely this job for `play`.
        # Two actions of one tool disagreeing about what an argument means is
        # the model's fault only in the sense that we gave it the trap.
        found = _scrape_first_video_url(url)
        if not found:
            return (f"Could not find a video called {url!r} on YouTube. Ask "
                    "the user for the link, or for a more exact title.")
        print(f"[YouTube] 🔎 Resolved title → {found}")
        url = found

    video_id = _extract_video_id(url)
    if not video_id:
        return Failed("Could not extract video ID from that URL.",
                      guidance="Ask the user to paste the link again.")

    if player:
        player.write_log(f"[YouTube] Summarizing: {url}")
    if speak:
        speak("Fetching the transcript now. One moment.")

    transcript = _get_transcript(video_id)
    if not transcript:
        return Failed("I couldn't retrieve a transcript for that video.",
                      guidance="The video has no captions. Say so; do not invent a summary.")

    if speak:
        speak("Transcript retrieved. Generating summary now.")

    try:
        summary = _summarize_with_gemini(transcript, url)
    except Exception as e:
        detail = str(e)
        if "429" in detail or "RESOURCE_EXHAUSTED" in detail or "quota" in detail.lower():
            return ("Got the transcript, but the summary could not be generated: "
                    "the Gemini API quota is exhausted. Tell the user that "
                    "plainly - the video and its captions were fine, this is a "
                    "billing limit on the API key, and it resets.")
        return f"Got the transcript, but summarising it failed: {detail}"

    if speak:
        speak(summary)

    if parameters.get("save", False):
        saved_path = _save_summary(summary, url)
        return f"Summary complete and saved to Desktop: {saved_path}"

    return summary


def _handle_get_info(parameters: dict, player, speak) -> str:
    url = parameters.get("url", "").strip()
    if not url:
        url = _ask_for_url("Please paste the YouTube video URL:")
    if not url or not _is_valid_youtube_url(url):
        return "Please provide a valid YouTube URL."

    video_id = _extract_video_id(url)
    if not video_id:
        return "Could not extract video ID."

    if player:
        player.write_log(f"[YouTube] Getting info: {url}")

    info = _scrape_video_info(video_id)
    if not info:
        return "Could not retrieve video information."

    lines = [
        f"{key.capitalize()}: {info[key]}"
        for key in ("title", "channel", "views", "duration", "likes")
        if key in info
    ]
    result = "\n".join(lines)

    if speak:
        speak(f"Here's the video info. {result.replace(chr(10), '. ')}")

    return result


def _handle_trending(parameters: dict, player, speak) -> str:
    region = parameters.get("region", "TR").upper()

    if player:
        player.write_log(f"[YouTube] Trending: {region}")

    trending = _scrape_trending(region=region, max_results=8)
    if not trending:
        return f"Could not fetch trending videos for region {region}."

    lines  = [f"Top trending videos in {region}:"]
    lines += [f"{v['rank']}. {v['title']} — {v['channel']}" for v in trending]
    result = "\n".join(lines)

    if speak:
        top3   = trending[:3]
        spoken = "Here are the top trending videos. " + ". ".join(
            f"Number {v['rank']}: {v['title']} by {v['channel']}" for v in top3
        )
        speak(spoken)

    return result

_ACTION_MAP = {
    "play":      _handle_play,
    "summarize": _handle_summarize,
    "get_info":  _handle_get_info,
    "trending":  _handle_trending,
}


def youtube_video(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None,
) -> ToolResult:
    """Returns a ToolResult. Handlers keep their prose; refusals decided HERE
    carry a verdict, because this tool reported `?` no-status on every call in
    a real session - including a 0ms self-refusal the model could not tell
    apart from a success."""
    params = parameters or {}
    action = params.get("action", "play").lower().strip()

    if player:
        player.write_log(f"[YouTube] Action: {action}")
    print(f"[YouTube] ▶️  Action: {action}  Params: {params}")

    handler = _ACTION_MAP.get(action)
    if handler is None:
        return ToolResult.failure(
            f"Unknown YouTube action: '{action}'. "
            "Available: play, summarize, get_info, trending.",
            guidance="Nothing ran. Call this again with a listed action.")

    try:
        if action == "play":
            return settled(handler(params, player) or "Done.")
        return settled(handler(params, player, speak) or "Done.")
    except Exception as e:
        print(f"[YouTube] ❌ Error in {action}: {e}")
        return ToolResult.failure(
            f"YouTube {action} failed: {e}",
            guidance="Tell the user it did not work; do not claim the video "
                     "played or was summarised.")