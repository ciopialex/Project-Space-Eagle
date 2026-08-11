import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            # CREATE_NO_WINDOW is 0x08000000 on Windows
            creation_flags = getattr(_subprocess, "CREATE_NO_WINDOW", 0x08000000)
            kw["creationflags"] = kw.get("creationflags", 0) | creation_flags
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import contextlib
from concurrent.futures import ThreadPoolExecutor
import re
import threading
import time
import json
import sys

# Line-buffer stdout before anything prints. Redirected output is
# block-buffered by default, so `eagle > log.txt` loses everything
# still in the buffer when the process dies - which is exactly the
# moment the log matters most.
from core import logsetup  # noqa: E402,F401
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import sounddevice as sd
import queue
from google import genai
from google.genai import types
from ui import AethelarkUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)
from actions.mission import mission
from core.tool_result import ToolResult, normalize
from core.tool_fallback import with_fallback_guidance
from core import diag
from core.vision_guard import VisionGuard
from core.quota import explain_quota
from core import proc_registry
from core.turn_trace import TraceLog, TurnTrace, _enabled_by_default as _trace_enabled
from core.mic_vad import SpeechDetector
from core.intent import decode as _decode_intent

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.web_agency        import web_agency
from actions.youtube_api       import youtube_api
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from core import user_paths
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.autostart         import autostart
from actions.messages_brief    import messages_brief, gmail_mark_read
from actions.proactive         import ProactiveEngine
from actions.web_search        import _news as _fetch_news_sync
from memory.config_manager     import get_brief_enabled


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = user_paths.api_keys_path()
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = "models/gemini-2.5-flash-native-audio-preview-12-2025"


class _ReconnectSignal(Exception):
    """Raised to intentionally collapse the live-session TaskGroup for a clean,
    context-preserving reconnect (e.g. on a server GoAway). Not an error —
    the run loop treats it as a graceful, fast reconnect using the resume handle."""


def _flatten_exc(exc: BaseException) -> list[BaseException]:
    """Flatten an exception into itself plus any nested ExceptionGroup members
    and __cause__/__context__ links. TaskGroup wraps failures in a
    BaseExceptionGroup, so the real cause (e.g. a 1011 APIError) is only
    reachable by unwrapping — plain str(group) hides it."""
    seen: set[int] = set()
    out: list[BaseException] = []

    def _walk(e: BaseException | None) -> None:
        if e is None or id(e) in seen:
            return
        seen.add(id(e))
        out.append(e)
        for sub in getattr(e, "exceptions", None) or ():
            _walk(sub)
        _walk(getattr(e, "__cause__", None))
        _walk(getattr(e, "__context__", None))

    _walk(exc)
    return out


CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024

# How long after a barge-in we keep discarding the cancelled turn's straggler
# audio. Frames arrive within milliseconds, so this is a generous backstop that
# guarantees the discard can never latch permanently.
_INTERRUPT_LATCH_S = 1.5

# No server frame at all for this long, while a turn is in flight, means the
# session is wedged. Reconnect with the resumption handle rather than sit silent.
_TURN_STALL_S = 25.0

# How often the health loop wakes. Kept well under _TURN_STALL_S so the stall
# window — not the log cadence — decides how fast a wedge is caught.
_WATCHDOG_TICK_S = 2.0

# Telemetry prints every Nth tick, preserving the original ~30s log cadence
# without slowing the health check down to match it.
_TELEMETRY_EVERY_N_TICKS = 15

# Tools used to run on the loop's DEFAULT executor (min(32, cpu+4) workers),
# which `asyncio.to_thread` also uses — so enough concurrently slow tools
# starved memory loads, dashboard snapshots and the news fetch of threads.
# Tools now own a bounded pool of their own; a pile-up degrades tools only.
_TOOL_WORKERS = 8


def _make_tool_executor() -> "ThreadPoolExecutor":
    """The private pool tools run on. Named so a stuck thread is identifiable
    in a stack dump rather than an anonymous ThreadPoolExecutor-N."""
    return ThreadPoolExecutor(
        max_workers=_TOOL_WORKERS, thread_name_prefix="aethelark-tool"
    )


def _shutdown_tool_executor(executor: "ThreadPoolExecutor") -> None:
    """Stop taking new work and abandon the backlog.

    `cancel_futures=True` drops everything not yet started; `wait=False` means
    quitting does not block on tools that are mid-flight. Combined with the
    deadlines in core.run_cmd — which stop a command hanging forever in the
    first place — this is what turns "quit" into an action rather than a wish.
    """
    executor.shutdown(wait=False, cancel_futures=True)


def _shutdown_web_browser() -> None:
    """Close the eagle's own browser, if `web_agency` ever started one.

    Nothing else in this process closes it: `EagleBrowser` runs its own
    daemon thread and holds an un-`stop()`ped Playwright driver process for
    as long as the process lives, and a daemon thread does not get a chance
    to run its own cleanup on interpreter exit. Importing
    `actions.grounding.web.browser` here rather than at module load time
    keeps this file from paying for Playwright's import (and the module
    it's nested under) on every startup, including the vast majority of
    sessions that never touch the web tool at all — `default_browser()`
    only ever constructs the real thing the first time `web_agency` needs
    it, and if that never happened, closing it here is a safe no-op.
    """
    try:
        from actions.grounding.web.browser import default_browser
        default_browser().close()
    except Exception as e:
        print(f"[main.py] Non-fatal error closing the eagle's browser: {e}")


class ConnectionBackoff:
    """Reconnect delay that grows under failure and forgets that growth once a
    session proves healthy.

    The delay used to only ever go up. It doubled on each network error, capped
    at 60, and was never reset on a successful connect — so one bad-Wi-Fi
    stretch pinned every later reconnect at a full minute for the rest of the
    process, long after the network had recovered.

    The reset is gated on HEALTH rather than on merely connecting: a session
    that dies on arrival is a crash loop, and a crash loop that resets its own
    backoff is a hot loop with extra steps.
    """

    BASE            = 3.0
    MAX             = 60.0
    HEALTHY_AFTER_S = 30.0   # a session that lives this long proves the path works

    def __init__(self, clock=time.monotonic):
        self._clock        = clock
        self.delay         = self.BASE
        self._connected_at = None

    def on_connected(self) -> None:
        """A live session was established — start the health timer."""
        self._connected_at = self._clock()

    def on_failure(self) -> None:
        """The session ended. Drop the accumulated delay only if the session had
        lived long enough to be evidence that the path is healthy."""
        started = self._connected_at
        self._connected_at = None
        if started is not None and (self._clock() - started) >= self.HEALTHY_AFTER_S:
            self.delay = self.BASE

    def grow(self) -> None:
        """Unclassified/network failure — back off, bounded."""
        self.delay = min(self.delay * 2, self.MAX)

    def set(self, seconds: float) -> None:
        """A classified error with a known-good delay (GoAway, 1011, bad key)."""
        self.delay = float(seconds)


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are Aethelark, an ultra-advanced AI assistant. "
            "CRITICAL: The user speaks Romanian. You MUST understand Romanian but ONLY reply in English. "
            "Never speak Romanian. Be concise."
        )

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")

def _clean_transcript(text: str) -> str:
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()


def _collapse_repeats(text: str) -> str:
    """The native-audio transcription sometimes emits an utterance twice in a row
    ('X. X.'), so a spoken reply logs doubled. Collapse (1) an exact whole-string
    doubling and (2) consecutive duplicate sentences, so it reads once."""
    text = (text or "").strip()
    if not text:
        return text
    # 1) Whole-string doubling: two identical halves (word-for-word).
    words = text.split()
    n = len(words)
    if n >= 2 and n % 2 == 0:
        h = n // 2
        if [w.lower() for w in words[:h]] == [w.lower() for w in words[h:]]:
            return " ".join(words[:h])
    # 2) Consecutive duplicate sentences.
    out: list[str] = []
    last = ""
    for part in _SENT_SPLIT.split(text):
        norm = part.strip().lower()
        if norm and norm != last:
            out.append(part.strip())
            last = norm
    return " ".join(out) if out else text


def _computer_control_actions() -> str:
    """Read from computer_control's own _ACTIONS, for the same reason as
    below: the hand-typed version had already lost drag, wait_for_element and
    scroll_into_view, which work and which the model was never told about."""
    try:
        from actions.computer_control import _ACTIONS
        return " | ".join(_ACTIONS)
    except Exception:
        return "type | click | hotkey | press | scroll | screenshot"


def _computer_settings_actions() -> str:
    """The action vocabulary, read from the implementation itself.

    This was the string "The action to perform", and computer_settings
    implements 66 actions. The model cannot invoke what it has never heard of,
    so 59 working capabilities - lock screen, mute, snap window, switch tab,
    toggle wifi, task manager - were unreachable by voice. The eagle was less
    capable than the eagle, and no test could see it: the code worked, the
    tests passed, and the features were dead because nothing advertised them.

    Generated rather than typed, because a hand-written list is exactly how
    they went missing in the first place.
    """
    try:
        from actions.computer_settings import ACTION_MAP
        names = sorted(ACTION_MAP)
    except Exception:
        names = []
    extra = ["volume_set", "type_text", "press_key", "reload_n"]
    return ("One of: " + ", ".join(names + extra) + ". "
            "volume_set/type_text/press_key/reload_n take `value`.")


TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens an application installed on the computer. "
            "Use this whenever the user asks to open, launch, or start an app or "
            "program. Always call this tool — never just say you opened it. "
            "For a WEBSITE this only opens it and stops: if the user wants anything "
            "DONE on that site — find something, read their account, click through a "
            "flow — use web_agency instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": (
            "Searches the web. Use for ANY question about current facts, events, prices, "
            "or topics — always prefer this over guessing. "
            "Modes: 'search' (default), 'news' (latest headlines on a topic), "
            "'research' (deep comprehensive answer), 'price' (product cost lookup), "
            "'compare' (side-by-side comparison of items)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query or topic"},
                "mode":   {"type": "STRING", "description": "search | news | research | price | compare"},
                "items":  {"type": "ARRAY",  "items": {"type": "STRING"}, "description": "Items to compare (compare mode)"},
                "aspect": {"type": "STRING", "description": "Comparison aspect: price | specs | reviews | features"},
            },
            "required": ["query"]
        }
    },
    {
        "name": "system_status",
        "description": (
            "Returns real-time system metrics: CPU usage, RAM, GPU load, CPU temperature, "
            "uptime, and process count. Use when the user asks about computer performance, "
            "temperature, memory, or resource usage."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": (
            "Sends a text message via WhatsApp or Telegram. These are the only platforms it "
            "supports. For Instagram, Facebook, LinkedIn or any other site's messaging, there "
            "is no API — use web_agency to do it through the website like a person would, and "
            "never tell the user it is impossible without trying that."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Plays a YouTube video by name, summarizes a video's content, gets video "
            "info, or shows trending videos. It works by searching YouTube publicly — "
            "it is NOT signed in as the user and cannot see anything account-specific. "
            "For the user's own liked videos, watch history, subscriptions, playlists, "
            "comments, or anything else behind their login, use web_agency instead: it "
            "drives a real browser and can be signed in. Never tell the user something "
            "is private or impossible without trying web_agency first."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures the screen or webcam image and lets you analyze it. "
            "MUST be called when user asks what is on screen, what you see, "
            "look at camera, analyze my screen, etc. "
            "You have NO visual ability without this tool. "
            "After the image is captured it is sent directly to you — describe what you see and answer the user's question. "
            "When using camera: the live view stays open until user says close it or calls close_camera."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "close_camera",
        "description": (
            "Closes the live camera view shown on screen. "
            "Call when user says: close camera, stop camera, turn off camera, "
            "kamerayı kapat, kapat, creepy, etc."
        ),
        "parameters": {"type": "OBJECT", "properties": {}, "required": []}
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Use for ANY single computer control command."
            "It presses keys and toggles settings; to SEE what is on screen use screen_process."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": _computer_settings_actions()},
                "description": {"type": "STRING", "description": "Natural language description of what to do"},
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Opens a website in the USER'S OWN browser, with their own logins and tabs — "
            "for when they want to look at something themselves. "
            "Do NOT use it to do work inside a page: for reading a page's controls, clicking, "
            "typing, filling forms, or any multi-step task on a site, use web_agency, which "
            "perceives the page properly and refuses irreversible actions. "
            "Simple open/search requests launch the user's own browser normally (their real profile "
            "and logged-in accounts); interactive actions (click, type, fill_form...) attach an "
            "automation browser. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously."
            "It opens the USER'S own browser and cannot read or click inside a page: for that use web_agency. For files on disk use file_controller."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | set_default (persist the user's preferred/default browser) | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action (e.g. Enter, Escape, F5)"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "youtube_api",
        "description": (
            "The user's OWN YouTube account, read directly through the Google "
            "account they already connected to Aethelark — no browser, no "
            "sign-in, roughly 20x faster than loading the page. "
            "USE THIS FIRST for: their liked videos, their playlists, their "
            "subscriptions. action='liked' answers 'what did I like recently' "
            "and 'play the song I liked last night'. "
            "It CANNOT read watch history — Google removed that API in 2016 — "
            "so for history use web_agency on youtube.com/feed/history. "
            "It does not search YouTube and does not play anything: use "
            "youtube_video to play a video once you know its name."),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["liked", "subscriptions", "playlists", "history"],
                    "description": "Which part of their account to read.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many to return (default 5, max 10). "
                                   "Keep it small — this is spoken aloud.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "web_agency",
        "description": (
            "Uses a website the way a person would, in the eagle's OWN browser: "
            "reads the page's real controls, then clicks and types them by name. "
            "For anything INSIDE a site. Runs in the background, never takes over "
            "the user's screen. Reads English, Romanian and Spanish pages. "
            "REFUSES irreversible actions (paying, ordering, deleting) and says why "
            "— relay that and let the user decide. Declines cookie walls itself. "
            "STOPS and ASKS for a verification code or a 'not a robot' check. "
            "If a site needs the user signed in, call action='sign_in' with that "
            "url: a window opens, they sign in ONCE, and it stays signed in. That "
            "is the only way in - never tell the user to open their own browser, "
            "and never claim you cannot reach their account."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "open | look | click | download | type | sign_in | close. Use download (not click) for anything that produces a FILE - it only reports success when a file is actually on disk."},
                "url":         {"type": "STRING", "description": "URL for the open action"},
                "description": {"type": "STRING", "description": "Which control, in plain words: 'the Sign in button', 'the Email field'. Use a name from the last look."},
                "text":        {"type": "STRING", "description": "Text to type, for the type action"},
                "want_pixels": {"type": "BOOLEAN", "description": "Force a screenshot on look, when the structural read is not enough"},
                "timeout":     {"type": "NUMBER", "description": "Seconds to wait for the user during sign_in (default 300)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "mission",
        "description": (
            "Run a goal that takes MORE THAN ONE action, as a sequence of "
            "small verified steps: 'download a laptop stand from makerworld', "
            "'download the form, fill it in and upload it', 'find X and send "
            "it to Y'. It plans the steps, does one per call, escalates "
            "through different ways of doing each one, and never repeats an "
            "approach that already failed. Use it INSTEAD of guessing a "
            "single tool when the request needs several actions in order. "
            "For one action, call that tool directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description":
                           "start | next | status | abandon. After start, keep "
                           "calling next until it says done or blocked."},
                "goal":   {"type": "STRING", "description":
                           "For start: what the user asked for, in their own words"},
                "steps":  {"type": "STRING", "description":
                           "For start: ALWAYS supply this, one step per line. "
                           "You are already thinking about the goal, so it is "
                           "free; leaving it out forces a slower extra call "
                           "that is often rate-limited.\n"
                           "EVERY STEP MUST BE A THING YOU CAN SEE AND DO ON "
                           "SCREEN. The test: could a stranger do it without "
                           "deciding anything? 'Click the Download button' "
                           "passes. 'Select a basic, highly-rated model' FAILS "
                           "— that is a judgement, not an action; break it "
                           "into what a person would actually do: 'Click the "
                           "Trending filter', then 'Click the first result'. "
                           "Same for 'find a good one', 'pick the best', "
                           "'prepare the file'.\n"
                           "Name controls by the words shown on screen ('the "
                           "search box', 'the Download button'). No CSS "
                           "selectors, no XPath, no coordinates. Include the "
                           "address on any step that opens a page. Prefer a "
                           "direct URL over navigating from a home page. "
                           "Typically 3-10 steps."},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        # `task` was not described at all, so the model chose this tool
        # believing it only set wallpapers — while that action generates and
        # runs Python. Say what it does; the model cannot weigh a capability
        # it has not been told about.
        "description": ("Controls the desktop: wallpaper, organize, clean, "
                        "list, stats. The 'task' action additionally writes "
                        "and RUNS Python for something the other actions do "
                        "not cover; it is confined to the user's home folder "
                        "and cannot delete, move or run system commands. "
                        "For reading or writing a named file or folder, "
                        "file_controller wins — it is contained the same way "
                        "and its deletions go to the trash with an undo."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files. ONE file at a time. For reading or writing files generally use file_controller; for a whole project built and verified use swarm_mode.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | optimize | screen_debug | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors. For plain file reads and writes use file_controller; for a full build with verification and review use swarm_mode.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "developer_mode",
        "description": "Single-agent coding session. Use ONLY for a small self-contained change, a one-off script, or a follow-up instruction to an ALREADY-RUNNING developer session. For a new project or product the user wants built ('build me a booking website', 'make a landing page for my shop'), do NOT use this — use swarm_mode with action='plan', which sizes the team and may still choose one agent. This is a MODE switch, not a way to browse: for anything on a website use web_agency.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "project_name": {"type": "STRING", "description": "Snake_case name of the project folder to create (e.g. 'spotify_clone')"},
                "prompt": {"type": "STRING", "description": "The coding task or prompt for the developer agent (e.g. 'Make a Spotify Clone')"},
                "directory": {"type": "STRING", "description": "Optional absolute path to the directory to start development in. If not specified, Aethelark will auto-detect or ask."}
            },
            "required": ["project_name", "prompt"]
        }
    },
    {
        "name": "swarm_mode",
        "description": "THE FRONT DOOR for building software. Use whenever the user asks for any project, product, app, website, or tool to be built — 'build a booking website for my dental clinic', 'make me a landing page', 'I need an inventory system'. The user does NOT need to mention agents, teams, or a swarm; they never say 'use two agents', they just describe what they want. Flow: action='plan' → a Chief Architect decomposes the mission, sizes the team (possibly to one agent) and returns a spoken plan summary; SPEAK it and ASK the user to approve; then action='execute' to spin up the team in isolated git worktrees. `directory` is OPTIONAL — omit it and a project folder is created automatically under ~/Projects; never ask the user for a file path. While agents work, action='inject' relays new ideas. Also: status | review (verify+merge) | stop | broadcast | launch. It BUILDS; it does not fetch. For one website use web_agency, for one file use file_controller.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "plan | execute | inject | status | review | stop | broadcast | launch | authorize | deny | escalations | kill_all | processes | open. Use 'open' when the user says 'show me it', 'open the site', 'let me see it' — it serves the finished project and opens it in the browser. Use 'authorize' the moment the user approves a held prompt ('yes', 'allow it', 'go ahead') and 'deny' if they refuse; 'escalations' when they ask what's blocked; 'kill_all' for 'stop everything', 'kill them all', 'shut it down' — an immediate hard stop of every agent; 'processes' for 'what have you got running'."},
                "escalation_id": {"type": "STRING", "description": "For authorize/deny: the specific held prompt id (e.g. 'esc3'). Omit to resolve the oldest one, which is what the user means when they just say 'allow it'."},
                "directory":   {"type": "STRING", "description": "OPTIONAL absolute path. Pass it ONLY when the user names an existing project. Otherwise omit it — a folder is derived from the goal under ~/Projects and reused for the rest of the mission. Never ask the user for a path."},
                "goal":        {"type": "STRING", "description": "For plan: the mission in one line, e.g. 'a Flappy Bird clone with an online leaderboard'"},
                "aesthetic":   {"type": "STRING", "description": "For plan on anything with a LOOK (website, landing page, app UI): the user's answer about the style they want, in their own words ('soft and pastel', 'dark and shiny', 'warm and rustic', 'like a 1970s record sleeve'). Ask ONE short question before planning if they haven't said — 'any look in mind: soft and pastel, dark and shiny, or clean and simple?' — and pass their reply here. If they say they don't mind, omit this and the architect picks."},
                "max_agents":  {"type": "INTEGER", "description": "For plan: cap on team size (default 2)"},
                "notes":       {"type": "STRING", "description": "For execute: extra requirements the user voiced WITH their approval ('yes, but make the UI beautiful'). Route by role with a JSON object like '{\"frontend\": \"make the UI beautiful\"}', or a plain string to apply to every agent."},
                "target":      {"type": "STRING", "description": "For inject: who hears the new request — a role ('frontend', 'the backend one'), an agent name, or 'all'"},
                "interrupt":   {"type": "BOOLEAN", "description": "For inject: true = hard redirect (stop current work); false (default) = chime in and keep working"},
                "deep":        {"type": "BOOLEAN", "description": "For review: also run an offloaded deep LLM code review (slower)"},
                "assignments": {"type": "STRING", "description": "For launch (manual, skips planning): JSON object mapping agent name to its task"},
                "agent":       {"type": "STRING", "description": "For broadcast: which agent (or 'eagle') the decision comes from"},
                "message":     {"type": "STRING", "description": "For inject/broadcast: the request/decision text to deliver"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "swarm_status",
        "description": "What the swarm is doing RIGHT NOW, in plain speakable language. Use whenever the user asks 'what are you doing', 'how's it going', 'what's happening', 'are they done yet', 'any progress', or asks about the project while agents are working. Instant and read-only — safe to call at any time, including while agents are mid-build. Returns who is on what, how far in, what finished, and anything waiting on the user. ALWAYS prefer this over swarm_mode action='status' when the user is just asking.",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "agent_interject",
        "description": "Interrupt a running coding agent mid-work (graceful Ctrl+C) and optionally redirect it with a new instruction. Use when the user says things like 'stop Claude' or 'tell the agent to use X instead of Y'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "agent":     {"type": "STRING", "description": "Which agent: antigravity_cli | claude_code | opencode | kimi"},
                "directory": {"type": "STRING", "description": "Project directory the agent is working in"},
                "message":   {"type": "STRING", "description": "Optional new instruction to inject after interrupting"},
            },
            "required": ["agent", "directory"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find elements on screen. It saves a screenshot as a FILE. To have the screen looked at and described, use screen_process.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": _computer_control_actions()},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "Installs, downloads, updates and lists Steam and Epic games, checks "
            "download status, and schedules updates — it drives the local game "
            "clients. It does NOT browse the storefronts: for a wishlist, a store "
            "page, prices, reviews or purchase history, use web_agency. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use browser_control or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "autostart",
        "description": (
            "Enable, disable, or check auto-start on boot — whether Aethelark "
            "launches automatically when the user logs into their computer. "
            "Use when the user says things like 'start yourself when my PC boots', "
            "'launch on startup', 'stop auto-starting', or asks if you start on boot. "
            "Works on Windows, macOS, and Linux."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "enable | disable | toggle | status (default: status)"}
            },
        }
    },
    {
        "name": "messages_brief",
        "description": (
            "Brief the user on their UNREAD messages across channels (Gmail + "
            "WhatsApp). Use when the user asks things like 'what did I miss', "
            "'any new messages', 'catch me up', 'read my unread', 'check my inbox', "
            "'brief me on my messages'. Returns a short spoken-ready summary of who "
            "messaged and how many are unread. Gmail requires Google connected in "
            "Settings; WhatsApp requires WhatsApp Web logged in."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "source": {"type": "STRING", "description": "gmail | whatsapp | all (default: all)"}
            },
        }
    },
    {
        "name": "mark_emails_read",
        "description": (
            "Mark Gmail messages as read (clears the unread flag). Use when the user "
            "says 'mark the marketing ones as read', 'clear the promotions', 'mark "
            "those as read', 'archive the junk from my unread'. Pass a Gmail search "
            "`query` (e.g. 'category:promotions is:unread', 'from:olx.ro is:unread', "
            "'is:unread -is:important') OR specific message `ids` from a prior brief. "
            "Confirm with the user before clearing anything that might include real "
            "people. Needs Google connected with modify permission."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Gmail search query selecting the mail to mark read"},
            },
        }
    },
    {
        "name": "shutdown_aethelark",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop Aethelark. "
            "The user can say this in ANY language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Alex, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

# --- Plugin system ---


class ToolSpec:
    def __init__(self, reads=None, writes=None, exclusive=False, priority=1, timeout_s=30.0):
        self.reads = set(reads or [])
        self.writes = set(writes or [])
        self.exclusive = exclusive
        self.priority = priority
        self.timeout_s = timeout_s

TOOL_SPECS = {
    "save_memory": ToolSpec(writes=["memory"], priority=2),
    "open_app": ToolSpec(writes=["desktop"], priority=1),
    "weather_report": ToolSpec(reads=["web"], priority=1),
    "browser_control": ToolSpec(writes=["desktop"], priority=1),
    # Reads the web in its own browser; touches neither the user's screen nor
    # their browser. Non-exclusive on purpose — this is the tool that can run
    # while the user is doing something else.
    "web_agency": ToolSpec(reads=["web"], priority=1, timeout_s=90.0),
    # One HTTPS call. Non-exclusive and short: it touches nothing the user can
    # see and has no reason to hold a slot.
    "youtube_api": ToolSpec(reads=["net"], priority=1, timeout_s=20.0),
    "file_controller": ToolSpec(writes=["file"], priority=1),
    "send_message": ToolSpec(writes=["desktop"], exclusive=True, priority=1, timeout_s=130.0),
    "reminder": ToolSpec(writes=["system"], priority=1),
    "youtube_video": ToolSpec(writes=["desktop"], priority=1),
    "screen_process": ToolSpec(reads=["camera", "desktop"], priority=1),
    "close_camera": ToolSpec(writes=["camera"], priority=2),
    "computer_settings": ToolSpec(writes=["system"], priority=1),
    "desktop_control": ToolSpec(writes=["desktop"], exclusive=True, priority=1),
    "code_helper": ToolSpec(reads=["memory"], writes=["file"], priority=1, timeout_s=45.0),
    "dev_agent": ToolSpec(reads=["memory"], writes=["file"], priority=1, timeout_s=45.0),
    "developer_mode": ToolSpec(reads=["memory"], writes=["file"], exclusive=True, priority=2, timeout_s=120.0),
    "swarm_mode": ToolSpec(reads=["memory"], writes=["file"], exclusive=True, priority=2, timeout_s=300.0),
    # Read-only and deliberately NOT exclusive: a question about progress must
    # never queue behind the work it is asking about. The eagle delegates, so
    # it stays free to talk while its agents build.
    "swarm_status": ToolSpec(reads=["memory"], priority=3, timeout_s=10.0),
    "agent_interject": ToolSpec(writes=["file"], priority=3, timeout_s=30.0),
    "web_search": ToolSpec(reads=["web"], priority=1),
    "file_processor": ToolSpec(reads=["file"], writes=["file"], priority=1),
    "computer_control": ToolSpec(writes=["desktop"], exclusive=True, priority=1),
    "game_updater": ToolSpec(reads=["web"], writes=["file"], priority=1),
    "flight_finder": ToolSpec(reads=["web"], priority=1),
    "system_status": ToolSpec(reads=["system"], priority=2),
    "autostart": ToolSpec(writes=["system"], priority=2),
    "messages_brief": ToolSpec(reads=["web", "desktop"], priority=1, timeout_s=100.0),
    "mark_emails_read": ToolSpec(writes=["web"], priority=1, timeout_s=40.0),
    "shutdown_aethelark": ToolSpec(writes=["system"], exclusive=True, priority=3),
}


class AethelarkLive:

    def __init__(self, ui: AethelarkUI):
        self.ui             = ui
        self._asst_name     = "Aethelark"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._play_stop_event     = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        # Refuses a repeat of the SAME question about the SAME surface. The
        # old 4s cooldown was cleared at every turn_complete, so a model
        # asking once per turn walked straight through it - five times in one
        # session, after it had already answered correctly.
        self._vision_guard         = VisionGuard()
        self._interrupted          = False   # True while draining audio after user interrupt
        self._interrupt_ts         = 0.0     # monotonic time the latch was set — see _discard_stragglers()
        # Straggler audio from a cancelled turn arrives within milliseconds;
        # this window is generous. It is a self-heal backstop, not a timer the
        # normal path relies on — turn_complete still clears the latch at once.
        self._turn_had_audio       = False   # did THIS turn produce speakable audio?
        self._last_server_activity = time.monotonic()  # watchdog: any server frame resets this
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance

        # ── Phase 1: Observability & Turn Identity ────────────────────────────
        self._session_id:  str = ""              # unique per live-session connection
        self._turn_epoch:  int = 0               # monotonic counter; incremented on barge-in and turn_complete
        self._shutdown_requested = False          # graceful shutdown flag

        # ── Phase 2: Audio Queue Bounds ───────────────────────────────────────
        self._mic_drops: int = 0                  # count of oldest-mic-frames dropped due to overflow

        # ── Phase 9: Session Resumption ───────────────────────────────────────
        # Gemini Live streams a rolling resumption token; on a dropped connection
        # (1011, GoAway, network blip) we reconnect WITH this handle so the server
        # restores full conversation context instead of a cold restart.
        self._resume_handle: str | None = None
        self._go_away_reconnect  = False          # set when server signals imminent GoAway

        # Reconnect pacing. Grows under failure, resets once a session has been
        # up long enough to prove the path is healthy — see ConnectionBackoff.
        self._backoff = ConnectionBackoff()

        # Tool batches currently running off the receive loop. Holds a strong
        # reference (asyncio only keeps a weak one) and tells the wedge
        # watchdog that silence is work, not death.
        self._inflight_tools: set[asyncio.Task] = set()

        # Serialises batches that write or claim exclusivity. Read-only batches
        # skip it entirely — see _batch_needs_exclusion.
        self._tool_batch_lock = asyncio.Lock()

        # Tools get their own bounded pool so a pile-up of slow ones cannot
        # starve asyncio.to_thread (memory loads, dashboard snapshots, news).
        self._tool_executor = _make_tool_executor()

        # ── Latency tracing (AETHELARK_TRACE=1) ───────────────────────────────
        # Off by default and inert when off: no VAD is constructed, and every
        # mark is an attribute read that returns immediately. On, it answers the
        # only question that matters here — where the time between "I stopped
        # talking" and "I heard something" actually goes.
        # Speculation: one prediction per turn, reset at turn_complete.
        self._speculated = False
        self._trace_on = _trace_enabled()
        self._trace: TurnTrace | None = None
        self._trace_log = TraceLog(emit=lambda line: print(f"[Aethelark] {line}"))
        self._vad = SpeechDetector(rate=SEND_SAMPLE_RATE,
                                   frame_samples=CHUNK_SIZE) if self._trace_on else None

    # ── Trace helpers ───────────────────────────────────────────────────────
    # Deliberately tiny and total: these are called from four different threads
    # (mic, receive loop, playback, tool dispatch) on the hot path of a live
    # conversation. Instrumentation that can throw would make the diagnostic
    # worse than the disease.

    def _trace_mark(self, name: str) -> None:
        t = self._trace
        if t is not None:
            t.mark(name)

    def _trace_begin(self) -> None:
        """Open a new turn. The previous one is dropped, not reported.

        A turn the user abandoned mid-sentence has no meaningful end, and
        publishing its half-filled numbers would drag the medians toward
        whatever the abandonment happened to cost."""
        self._trace = TurnTrace(turn=self._turn_epoch, enabled=True)

    def _trace_finish(self) -> None:
        t, self._trace = self._trace, None
        if t is not None:
            self._trace_log.finish(t)

    def _warm_browser(self) -> None:
        """Start the eagle's browser in the background. Read-only, no page."""
        def _run():
            try:
                from actions.grounding.web.browser import default_browser
                default_browser().start()
            except Exception:
                pass          # a warm-up that fails costs only the warm-up
        threading.Thread(target=_run, daemon=True).start()

    def _speculate(self, partial: str) -> None:
        """Begin the safe half of a request while the user is still speaking.

        The only moment early enough to matter: by the time the model emits a
        tool call, the 350ms silence window and the round trip are already
        spent. Browser cold start was measured at ~310ms, and the first `open`
        on a fresh profile at 7165ms.

        Strictly READ_ONLY, and strictly one thing for now - warming the
        browser. It has no side effects at all, and if `to_action` does not
        move on web requests then the whole idea is wrong and nothing else
        should be built on it.

        Never raises. It runs on the receive loop, and a speculative
        optimisation that can break a turn is worse than no optimisation.
        """
        if self._speculated:
            return                      # transcription streams in many chunks
        try:
            intent = _decode_intent(partial)
            if not any(c.id == "web.open" for c in intent.prewarm):
                return
            self._speculated = True
            self._trace_mark("speculated")
            print(diag.intent_line(intent))
            self._warm_browser()
        except Exception:
            pass

    def _trace_mic_frame(self, data) -> None:
        """Timestamp the user's speech from a single outgoing mic frame.

        A method rather than a closure inside `_listen_audio` so it can be
        driven by a test without a sound device: the marks it sets are the
        origin of every latency number, and an origin nothing can exercise is
        an origin nobody can trust.

        Fed the frames that are actually SENT, not every frame the device
        produces, so our estimate of "the user stopped" is made from the same
        audio the server's own VAD judged.
        """
        if self._vad is None:
            return
        event = self._vad.feed(data)
        if event == "start":
            self._trace_begin()
            self._trace_mark("speech_start")
        elif event == "end":
            self._trace_mark("speech_end")

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        
        def _update_gui():
            if value:
                self.ui.set_state("SPEAKING")
            elif not self.ui.muted:
                self.ui.set_state("LISTENING")
                
        if self._loop:
            self._loop.call_soon_threadsafe(_update_gui)
        else:
            _update_gui()

    def interrupt(self) -> None:
        """Stop Aethelark mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        self._interrupt_ts = time.monotonic()
        old_epoch = self._turn_epoch
        self._turn_epoch += 1  # advance epoch — stale results from this turn will be discarded
        new_epoch = self._turn_epoch
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    item = q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[Aethelark] ✋ Interrupted (epoch {old_epoch}→{new_epoch}) — {drained} playback frames discarded")
        self.set_speaking(False)
        if self._turn_done_event:
            self._turn_done_event.clear()
        # An interrupted turn never reached its own end. Reporting it would
        # mix "how long the eagle took" with "how long the user tolerated it".
        self._trace = None
        if self._vad is not None:
            self._vad.reset()
        self.ui.write_log("SYS: Interrupted — listening...")

    def _discard_stragglers(self) -> bool:
        """Should the frame just received be thrown away?

        After a barge-in the server may still be flushing the CANCELLED turn's
        audio. Those frames are tagged at RECEIVE time (post-interrupt), so they
        carry the NEW epoch and slip past the stale-frame filter in the playback
        loop — hence this second guard.

        It is time-bounded, and that matters more than it looks. This used to be
        a plain boolean cleared ONLY by a `turn_complete`. When a barge-in was
        never followed by one, the latch stuck True and every audio frame for
        the rest of the session was discarded one line before it reached the
        playback queue: the eagle kept listening, kept transcribing, kept
        generating, and never made another sound. A latch whose only exit is a
        message that may never arrive is a deadlock with extra steps.
        """
        if not self._interrupted:
            return False
        if time.monotonic() - self._interrupt_ts > _INTERRUPT_LATCH_S:
            self._interrupted = False       # self-heal: stragglers are long gone
            return False
        return True

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"There was an issue executing {tool_name}: {short}")

    def _build_config(self) -> types.LiveConnectConfig:
        from datetime import datetime

        # Load customization from config
        try:
            _cfg = json.loads(open(API_CONFIG_PATH, encoding="utf-8").read())
            self._asst_name = (_cfg.get("assistant_name") or "Aethelark").strip()
            _user_name = (_cfg.get("user_name") or "").strip()
        except Exception:
            _cfg = {}
            self._asst_name = "Aethelark"
            _user_name = ""

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        # Identity injection — overrides any hardcoded name in prompt.txt
        _addr = f"ADDRESS: Always call the user '{_user_name}'." if _user_name else ""
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        parts = [time_ctx, identity_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        _voice = (_cfg.get("voice_name") or "Puck").strip()

        # ── Latency: end-of-turn detection ───────────────────────────────────
        # The single biggest tunable delay in the whole product. After you stop
        # talking the server waits for silence before deciding your turn ended;
        # nothing can begin until it does, so this sits in front of EVERY reply.
        # Model inference is fixed cost — this is not.
        #
        # It trades against patience: too short and it cuts you off when you
        # pause mid-thought (exactly what happens while describing something,
        # a design idea). Tuned here for a conversational middle and
        # exposed in config so it can be dialled per-person.
        # ── TURN LATENCY BUDGET ──────────────────────────────────────────
        # The only latency knobs the eagle actually controls. Everything else
        # in a turn - network, model, how long the user talked - is not ours
        # to set. Both had shipped untuned since the beginning.
        #
        # `silence_duration_ms` is the big one, and it is pure waiting: the
        # server sits in SILENCE this long after the user stops before it will
        # admit the turn ended. Nothing is in flight, the model is not
        # thinking. It is paid on every turn, including "yes" - and at 550ms
        # it was most of a one-second target spent doing nothing.
        #
        # 350ms is chosen against the failure on the other side, which is
        # worse than waiting: below roughly 250ms an ordinary pause mid
        # sentence - drawing breath, hunting for a word - reads as the end of
        # the turn and the eagle talks over the user. Being cut off is far
        # more annoying than a beat of delay, so this stays clear of that
        # edge rather than chasing the smallest number that works in a quiet
        # room. Set `end_of_turn_silence_ms` in config to tune per machine.
        _silence_ms = int(_cfg.get("end_of_turn_silence_ms") or 350)
        # Audio kept from BEFORE speech onset, so a first syllable is not
        # clipped. Useful; pure latency beyond what it takes to do that job.
        _prefix_ms = int(_cfg.get("speech_prefix_padding_ms") or 150)

        # ── Latency + voice consistency: thinking ────────────────────────────
        # LIVE_MODEL is a *thinking* native-audio model and thinking defaults
        # on, so it reasons before it speaks — pure added delay on the simple
        # conversational turns that are most of a voice session. It is also
        # where stray `thought`/`text` parts come from, and a turn that returns
        # text instead of audio is a SILENT turn.
        #
        # include_thoughts=False stops the reasoning being streamed to us at
        # all, which keeps every reply in the model's own voice. Budget is
        # configurable: 0 is fastest, raise it if planning quality suffers.
        _think_budget = int(_cfg.get("thinking_budget") or 0)

        # ── Verbosity: the hard-law backstop ────────────────────────────────
        # `core/prompt.txt` asks for one sentence. Measured across 13 real
        # turns it was not obeyed: `spoken` (first_audio -> complete) had a
        # median of 4854ms and a worst case of 15373ms. Fifteen seconds of
        # speech the user cannot skim, skip or interrupt.
        #
        # Prompt text is soft law - the model may talk itself out of it. This
        # is the dispatch-layer version, and it is OFF by default on purpose:
        # a cap set too low truncates a reply mid-word, which is worse than a
        # long one. Set `max_reply_tokens` in config once a value has been
        # measured against real turns rather than guessed at.
        _max_tokens = _cfg.get("max_reply_tokens")
        _max_tokens = int(_max_tokens) if _max_tokens else None

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            **({"max_output_tokens": _max_tokens} if _max_tokens else {}),
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": TOOL_DECLARATIONS}],
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            thinking_config=types.ThinkingConfig(
                include_thoughts=False,
                thinking_budget=_think_budget,
            ),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    silence_duration_ms=_silence_ms,
                    prefix_padding_ms=_prefix_ms,
                ),
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=_voice
                    )
                )
            ),
        )

    async def _execute_tool(self, fc, call_epoch: int = -1) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        # Record the epoch at dispatch time so we can detect stale completions
        if call_epoch < 0:
            call_epoch = self._turn_epoch

        _t0 = time.monotonic()
        print(diag.tool_call(name, args, call_epoch))
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(self._tool_executor, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(self._tool_executor, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(self._tool_executor, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "youtube_api":
                r = await loop.run_in_executor(self._tool_executor, lambda: youtube_api(parameters=args))
                result = r or "Done."

            elif name == "web_agency":
                r = await loop.run_in_executor(self._tool_executor, lambda: web_agency(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(self._tool_executor, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(self._tool_executor, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(self._tool_executor, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(self._tool_executor, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                _angle_req = str(args.get("angle", "screen")).lower()
                _text_req = str(args.get("text", "What do you see?"))
                _refusal = self._vision_guard.allow(_angle_req, _text_req)
                if self._vision_busy or _refusal:
                    _why = _refusal or ("A capture is already in flight. Wait for "
                                        "the image and answer from it.")
                    print(f"[Vision] ⏳ refused a repeat look — {_why[:70]}")
                    result = ToolResult.failure(_why, guidance=_why)
                else:
                    self._vision_busy      = True
                    self._vision_last_time = _now
                    self._vision_guard.mark_in_flight()
                    angle     = args.get("angle", "screen").lower()
                    user_text = args.get("text", "What do you see?")
                    if angle == "camera":
                        img_b, mime_t = await loop.run_in_executor(self._tool_executor, _capture_camera)
                        self.ui.start_camera_stream()
                        self._vision_cam_active = True
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        img_b, mime_t = await loop.run_in_executor(self._tool_executor, _capture_screen)
                        print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        _stall = "screen"
                    self._pending_vision = (img_b, mime_t, user_text, angle)
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                        f"Immediately say ONE short natural sentence in the user's own language, "
                        f"telling them you are looking at their {_stall} right now. "
                        f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(self._tool_executor, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "mission":
                r = await loop.run_in_executor(self._tool_executor, lambda: mission(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(self._tool_executor, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(self._tool_executor, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(self._tool_executor, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "developer_mode":
                from actions.developer_mode import developer_mode
                self.ui.set_state("WORKING")
                # AWAIT the real launch result — agent.run() verifies the session
                # actually spawned and is alive (~1-2s), then returns. The old
                # fire-and-forget hardcoded "started" even when nothing launched
                # (the "hallucination"). Now the eagle reports the truth.
                r = await developer_mode(parameters=args, player=self.ui)
                result = r or "Developer session started."

            elif name == "swarm_status":
                from actions.swarm_orchestrator import swarm_narrate
                result = await loop.run_in_executor(self._tool_executor, swarm_narrate)

            elif name == "swarm_mode":
                from actions.swarm_orchestrator import swarm_orchestrate
                self.ui.set_state("WORKING")
                r = await swarm_orchestrate(parameters=args, player=self.ui)
                result = r or "Done."

            elif name == "agent_interject":
                from actions.agent_delegation import interject_agent
                r = await interject_agent(
                    agent_key=args.get("agent", ""),
                    directory=args.get("directory", ""),
                    message=args.get("message", ""),
                    player=self.ui)
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(self._tool_executor, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    self._tool_executor,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(self._tool_executor, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(self._tool_executor, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(self._tool_executor, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                r = await loop.run_in_executor(self._tool_executor, get_system_status)
                result = str(r)

            elif name == "autostart":
                r = await loop.run_in_executor(self._tool_executor, lambda: autostart(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "mark_emails_read":
                r = await loop.run_in_executor(self._tool_executor, lambda: gmail_mark_read(query=args.get("query", "")))
                result = r or "Done."

            elif name == "messages_brief":
                r = await loop.run_in_executor(self._tool_executor, lambda: messages_brief(parameters=args, player=self.ui))
                result = r or "Nothing to report."

            elif name == "shutdown_aethelark":
                self.ui.write_log("SYS: Shutdown requested — graceful shutdown in progress.")
                self._shutdown_requested = True
                self.speak("Goodbye.")
                # Schedule graceful shutdown: wait for goodbye audio, then clean exit
                async def _graceful_shutdown():
                    await asyncio.sleep(2.5)  # let goodbye audio play
                    print("[Aethelark] 🔴 Graceful shutdown: closing session...")
                    self.ui.write_log("SYS: Shutting down...")
                    # Stop audio streams
                    self.set_speaking(False)
                    # Close the eagle's own browser, if web_agency ever
                    # started one — see _shutdown_web_browser's docstring
                    # for why nothing else does this. Off the event loop
                    # thread: EagleBrowser.close() blocks synchronously for
                    # up to a few seconds waiting on its teardown jobs.
                    try:
                        await loop.run_in_executor(self._tool_executor,
                                                   _shutdown_web_browser)
                    except Exception as _e:
                        print(f"[main.py] Non-fatal error at line 1071: {_e}")
                    # Close dashboard
                    if self._dashboard:
                        try:
                            await self._dashboard.broadcast({"type": "status", "state": "offline"})
                        except Exception as _e:
                            print(f"[main.py] Non-fatal error at line 1065: {_e}")
                    # Signal the UI to close (runs on Qt thread)
                    try:
                        self.ui.request_shutdown()
                    except Exception as _e:
                        print(f"[main.py] Non-fatal error at line 1070: {_e}")
                    # Final fallback — if Qt doesn't exit cleanly within 3s
                    await asyncio.sleep(3)
                    print("[Aethelark] 🔴 Fallback exit.")
                    sys.exit(0)
                asyncio.create_task(_graceful_shutdown())

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = ToolResult.failure(
                f"Tool '{name}' failed: {e}",
                guidance="Tell the user this action failed; do not claim it worked.")
            traceback.print_exc()
            self.speak_error(name, str(e))

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        # Normalize ANY tool return (ToolResult or legacy string) into the
        # structured contract, so the model gets an explicit ok/guidance signal
        # instead of parsing prose. Legacy string tools are unaffected.
        tr = normalize(result)

        # A narrow tool failing is not the task being impossible. Attach the
        # general tool that covers the same ground, by mechanism rather than
        # by hoping the model remembers the prompt — this is the exact point
        # where "YouTube keeps that private" was returned about the user's own
        # liked videos, with web_agency never tried.
        tr = with_fallback_guidance(name, tr)
        # Nine tools call Gemini inside themselves and the brain runs on a free
        # tier. A rate limit surfacing as a tool failure sends the user hunting
        # a bug in something that works and will work again in a minute.
        tr = explain_quota(tr)

        _elapsed_ms = (time.monotonic() - _t0) * 1000
        # Check for stale result — epoch may have advanced during execution
        if call_epoch != self._turn_epoch:
            print(f"[Tool] ⚠ {name} STALE (epoch {call_epoch} → "
                  f"{self._turn_epoch}) — the turn moved on; returned anyway "
                  f"for protocol")
            print(diag.tool_result(name, tr, _elapsed_ms))
        else:
            print(diag.tool_result(name, tr, _elapsed_ms))
        return types.FunctionResponse(
            id=fc.id, name=name,
            response=tr.to_response()
        )

    async def _schedule_tool_calls(self, tool_calls, call_epoch: int) -> list[types.FunctionResponse]:
        """Scoreboard scheduler with RAW, WAW, WAR hazard detection.
        Runs disjoint, read-only/independent tools concurrently,
        while sequencing conflicting or exclusive tools.
        """
        running = {}  # task -> (tool_call, spec)
        results = {}  # tool_call_id -> FunctionResponse
        pending = list(tool_calls)
        
        # Sort pending by priority
        pending.sort(key=lambda fc: TOOL_SPECS.get(fc.name, ToolSpec()).priority, reverse=True)
        
        while pending or running:
            # 1. Identify which pending tools can be issued now
            issued = []
            for fc in list(pending):
                spec = TOOL_SPECS.get(fc.name, ToolSpec(exclusive=True))
                
                # Check for conflict with any currently running tools
                conflict = False
                for r_task, (r_fc, r_spec) in running.items():
                    if spec.exclusive or r_spec.exclusive:
                        conflict = True
                        break
                    # RAW: running writes what pending reads
                    if r_spec.writes & spec.reads:
                        conflict = True
                        break
                    # WAR: running reads what pending writes
                    if r_spec.reads & spec.writes:
                        conflict = True
                        break
                    # WAW: running writes what pending writes
                    if r_spec.writes & spec.writes:
                        conflict = True
                        break
                
                # Check conflict with other already-selected tools in the issued list
                for i_fc in issued:
                    i_spec = TOOL_SPECS.get(i_fc.name, ToolSpec(exclusive=True))
                    if spec.exclusive or i_spec.exclusive:
                        conflict = True
                        break
                    if i_spec.writes & spec.reads:
                        conflict = True
                        break
                    if i_spec.reads & spec.writes:
                        conflict = True
                        break
                    if i_spec.writes & spec.writes:
                        conflict = True
                        break
                
                if not conflict:
                    issued.append(fc)
                    pending.remove(fc)
            
            # 2. Start all issued tool calls
            for fc in issued:
                spec = TOOL_SPECS.get(fc.name, ToolSpec(exclusive=True))
                
                async def run_with_timeout(f_call=fc, f_spec=spec):
                    try:
                        return await asyncio.wait_for(
                            self._execute_tool(f_call, call_epoch=call_epoch),
                            timeout=f_spec.timeout_s
                        )
                    except asyncio.TimeoutError:
                        print(f"[Tool] ✗ {f_call.name} TIMED OUT after "
                          f"{f_spec.timeout_s}s — it was still running when the "
                          f"budget ran out; nothing it did is guaranteed to "
                          f"have finished")
                        return types.FunctionResponse(
                            id=f_call.id, name=f_call.name,
                            response=ToolResult.failure(
                                f"Tool timed out after {f_spec.timeout_s}s.",
                                guidance="It may still be running in the background; "
                                         "don't retry blindly — tell the user it timed out."
                            ).to_response())
                    except Exception as e:
                        return types.FunctionResponse(
                            id=f_call.id, name=f_call.name,
                            response=ToolResult.failure(
                                f"Tool execution failed: {e}",
                                guidance="Report the failure honestly; do not claim success."
                            ).to_response())
                
                task = asyncio.create_task(run_with_timeout())
                running[task] = (fc, spec)
            
            if not running:
                break
                
            # 3. Wait for at least one running task to complete
            done, _ = await asyncio.wait(running.keys(), return_when=asyncio.FIRST_COMPLETED)
            
            for task in done:
                fc, spec = running.pop(task)
                res = task.result()
                results[fc.id] = res

        # Reassemble results in the original order of tool_calls
        return [results[fc.id] for fc in tool_calls if fc.id in results]

    async def _send_realtime(self):
        out_queue = self.out_queue
        session = self.session
        if out_queue is None or session is None:
            return
        while True:
            msg = await out_queue.get()
            # msg is a dict {"data": bytes, "mime_type": str}
            await session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[Aethelark] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def _enqueue_mic_frame(msg):
            """Run on the event loop thread via call_soon_threadsafe.
            Implements drop-oldest overflow to keep mic latency bounded."""
            out_queue = self.out_queue
            if out_queue is None:
                return
            self._trace_mic_frame(msg.get("data"))
            try:
                out_queue.put_nowait(msg)
            except asyncio.QueueFull:
                try:
                    out_queue.get_nowait()  # discard oldest stale frame
                except asyncio.QueueEmpty:
                    pass
                try:
                    out_queue.put_nowait(msg)
                except asyncio.QueueFull:
                    pass
                self._mic_drops += 1

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                aethelark_speaking = self._is_speaking
            if not aethelark_speaking and not self.ui.muted and not self._phone_active:
                data = indata.tobytes()
                msg = {"data": data, "mime_type": "audio/pcm"}
                # Schedule on event loop thread — never block the PortAudio callback
                loop.call_soon_threadsafe(_enqueue_mic_frame, msg)

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[Aethelark] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[Aethelark] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[Aethelark] 👂 Recv started")
        out_buf, in_buf = [], []
        text_buf: list[str] = []      # text parts of a turn that produced no audio
        session = self.session
        if session is None:
            return

        try:
            while True:
                async for response in session.receive():

                    # ── Session resumption: capture the rolling handle ────────
                    # The server emits a fresh handle at safe checkpoints. Store
                    # the latest resumable one so a reconnect restores context.
                    sru = response.session_resumption_update
                    if sru is not None and sru.resumable and sru.new_handle:
                        self._resume_handle = sru.new_handle

                    # ── GoAway: server is about to close this connection ─────
                    # Reconnecting proactively (with the handle we already hold)
                    # avoids a hard 1011 mid-turn. Break out to trigger reconnect.
                    if response.go_away is not None:
                        _left = getattr(response.go_away, "time_left", None)
                        print(f"[Aethelark] 🔻 GoAway received (time_left={_left}) — reconnecting with resume handle")
                        self.ui.write_log("NET: Session refreshing — reconnecting…")
                        self._go_away_reconnect = True
                        raise _ReconnectSignal("go_away")

                    self._last_server_activity = time.monotonic()

                    # First byte of the ANSWER, not of the connection, and not
                    # of the user's own transcription.
                    #
                    # This used to accept ANY `server_content` — but that is
                    # also how `input_transcription` arrives, and that streams
                    # WHILE the user is still speaking. So `first_token` was
                    # being stamped mid-sentence, which made `response`
                    # (speech_end → first_token) come out NEGATIVE on three of
                    # thirteen measured turns, and quietly moved the model's
                    # real thinking time into `audio`, which is labelled "our
                    # own playback path". The one number the whole latency
                    # question turns on was attributing Google's time to us.
                    _sc = response.server_content
                    _answered = bool(
                        response.data                       # audio payload
                        or response.tool_call               # a tool call
                        or (_sc and (getattr(_sc, "model_turn", None)
                                     or getattr(_sc, "output_transcription", None)
                                     or getattr(_sc, "turn_complete", None))))
                    if _answered:
                        self._trace_mark("first_token")

                    if response.data:
                        if self._discard_stragglers():
                            pass  # tail of the cancelled turn — drop it
                        else:
                            self._turn_had_audio = True
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            # Tag each frame with the current turn_epoch for epoch-aware barge-in
                            _audio_data = response.data
                            _SLICE = 2400
                            _epoch = self._turn_epoch
                            for _i in range(0, len(_audio_data), _SLICE):
                                frame = (_epoch, _audio_data[_i : _i + _SLICE])
                                audio_q = self.audio_in_queue
                                if audio_q is not None:
                                    try:
                                        audio_q.put_nowait(frame)
                                    except queue.Full:
                                        # With 200-frame buffer this should be extremely rare.
                                        # Log instead of drop-oldest — dropping output frames
                                        # corrupts speech and causes R2D2 glitches.
                                        print(f"[Aethelark] ⚠️ Playback queue overflow — frame dropped (epoch={_epoch})")

                    if response.server_content:
                        sc = response.server_content

                        # Text parts of the model turn. The model is configured
                        # for AUDIO out, but a turn can still come back as text
                        # — and those turns are silent, because response.data is
                        # empty for them. Capture the text so turn_complete can
                        # voice it instead of the conversation just stopping.
                        #
                        # `thought` parts are the model's INTERNAL reasoning and
                        # must never be spoken or shown — reading its own private
                        # monologue aloud would be worse than saying nothing.
                        mt = getattr(sc, "model_turn", None)
                        if mt and getattr(mt, "parts", None):
                            for _p in mt.parts:
                                if getattr(_p, "thought", False):
                                    continue
                                if getattr(_p, "text", None):
                                    text_buf.append(_p.text)

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            # RAW, and joined with nothing. The transcription
                            # streams mid-word ("Sa" then "ve"), and cleaning
                            # each fragment stripped its spacing before a
                            # " ".join put a space back in the middle of the
                            # word. Live, "Say the word ready" was rendered
                            # "Sa ve word rea dy" — and that mangled string is
                            # what `_speculate` was matching against, so the
                            # intent layer that exists to buy a head start was
                            # being fed nonsense. The API's own chunks already
                            # carry their leading spaces; clean ONCE, at the end.
                            raw = sc.input_transcription.text
                            if raw:
                                in_buf.append(raw)
                                self._last_user_speech = time.monotonic()
                                # While they are STILL TALKING. This is the
                                # only point early enough for a prediction to
                                # buy anything.
                                self._speculate(_clean_transcript("".join(in_buf)))

                        if sc.turn_complete:
                            self._speculated = False
                            self._trace_mark("complete")
                            self._trace_finish()
                            self._turn_epoch += 1
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                text_buf = []
                                self._turn_had_audio = False
                                continue

                            # A turn that returns text instead of audio is silent.
                            # It should no longer happen — include_thoughts=False
                            # stops reasoning being streamed — so surface it
                            # loudly rather than papering over it.
                            #
                            # Deliberately NOT re-voiced through a second TTS
                            # engine: that swaps voice mid-conversation and adds
                            # a synthesis round-trip, which is worse than the
                            # problem. If this ever fires, the fix belongs in the
                            # session config, not in a substitute voice.
                            _textonly = " ".join(t.strip() for t in text_buf if t.strip()).strip()
                            text_buf = []
                            if _textonly and not self._turn_had_audio:
                                print(f"[Aethelark] ⚠️ TEXT-ONLY TURN (no audio): {_textonly[:200]}")
                                self.ui.write_log(f"{self.ui.assistant_name}: {_textonly}")
                                out_buf = []          # already surfaced; don't double-log
                            self._turn_had_audio = False

                            full_in = _collapse_repeats(
                                _clean_transcript("".join(in_buf)))
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = _collapse_repeats(" ".join(out_buf).strip())
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "aethelark",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session via send_realtime_input")
                                self._vision_guard.mark_delivered()
                                await session.send_realtime_input(
                                    media=types.Blob(data=img_b, mime_type=mime_t)
                                )
                                await session.send_client_content(
                                    turns={"parts": [{"text": question}]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until Aethelark finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        # Dispatch and RETURN TO THE WIRE. Awaiting the batch
                        # here made the session deaf for the tool's whole
                        # duration — no audio, no go_away, no barge-in, and a
                        # watchdog that counted the silence as a dead session.
                        self._dispatch_tool_calls(
                            response.tool_call.function_calls,
                            call_epoch=self._turn_epoch,
                            session=session,
                        )
        except _ReconnectSignal:
            raise  # intentional graceful reconnect — no error logging
        except Exception as e:
            # Known transient server drops are reported cleanly by the run-loop
            # handler; skip the redundant traceback to keep the console readable.
            _es = str(e)
            if "1011" in _es or "ConnectionClosed" in _es:
                print(f"[Aethelark] Recv: connection closed by server ({_es[:80]})")
            else:
                print(f"[Aethelark] ❌ Recv: {e}")
                traceback.print_exc()
            raise

    def _play_audio_loop(self):
        print("[Aethelark] 🔊 Playback thread started")
        # Play at the model's NATIVE 24 kHz so we don't resample every 50 ms
        # frame. The previous path opened at 48 kHz and upsampled each frame with
        # numpy (plus an RMS envelope the web pill discards) — ~20×/second of
        # GIL-holding CPU fighting the live-audio pipeline, which is exactly what
        # made voice feel sluggish. Fall back to 48 kHz + resample only if the
        # device refuses 24 kHz.
        native_rate = RECEIVE_SAMPLE_RATE  # 24000
        resample = False
        stream = None
        try:
            stream = sd.RawOutputStream(samplerate=native_rate, channels=CHANNELS,
                                        dtype="int16", blocksize=1200)  # 50ms @ 24kHz
            stream.start()
            playback_rate = native_rate
        except Exception as e_native:
            print(f"[Aethelark] ⚠️ 24kHz output unavailable ({e_native}); using 48kHz+resample")
            # Close the half-opened 24kHz stream (if the failure was in start())
            # so we don't leak a device handle before retrying.
            try:
                if stream is not None:
                    stream.close()
            except Exception as _e:
                print(f"[main.py] Non-fatal error at line 1432: {_e}")
            playback_rate = 48000
            resample = True
            stream = sd.RawOutputStream(samplerate=playback_rate, channels=CHANNELS,
                                        dtype="int16", blocksize=2400)
            stream.start()

        # Prime the ring buffer with a little silence so the first write doesn't
        # race the DAC read pointer (was causing first-word clicks / speed-up).
        try:
            stream.write(b'\x00' * (playback_rate * 2 * 80 // 1000))  # 80ms silence
        except Exception as _e:
            print(f"[main.py] Non-fatal error at line 1444: {_e}")

        audio_in_queue = self.audio_in_queue
        play_stop_event = self._play_stop_event
        loop = self._loop

        if audio_in_queue is None or play_stop_event is None or loop is None:
            return

        # Only compute the audio-level envelope when the UI actually renders it
        # (classic QPainter pill does; the web pill is CSS-animated → no-op).
        wants_level = getattr(self.ui, "consumes_audio_level", True)

        def _is_speaking_now() -> bool:
            with self._speaking_lock:
                return self._is_speaking

        try:
            while not play_stop_event.is_set() and not self._shutdown_requested:
                try:
                    item = audio_in_queue.get(timeout=0.05)
                except queue.Empty:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and audio_in_queue.empty()
                    ):
                        # End of turn: drop the SPEAKING state once (skips the GUI
                        # reschedule if we're already not speaking, e.g. after a
                        # barge-in interrupt already flipped it).
                        if _is_speaking_now():
                            self.set_speaking(False)
                        loop.call_soon_threadsafe(self._turn_done_event.clear)
                    continue

                frame_epoch, chunk = item
                if frame_epoch < self._turn_epoch:
                    continue

                # Edge-trigger SPEAKING off the SHARED flag, not a local bool, so
                # it correctly re-arms after interrupt() externally forced it False
                # (a local latch would stay stale-True and leave the mic ungated).
                if not _is_speaking_now():
                    self.set_speaking(True)

                out = chunk
                # Skip numpy entirely on the common path (native rate + web UI):
                # just hand the raw PCM straight to the device.
                if resample or wants_level:
                    try:
                        import numpy as np
                        samples = np.frombuffer(chunk, dtype=np.int16)
                        if resample:
                            out = np.repeat(samples, 2).tobytes()
                        if wants_level:
                            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                            self.ui.set_audio_level(min(rms / 9000.0, 1.0))
                    except Exception as re_err:
                        print(f"[Aethelark] Resampling error: {re_err}")
                        out = chunk

                # The user hears the speaker, not the queue. Marking here — at
                # the device write — is what makes `audio` our own playback
                # cost rather than a number we could blame on the network.
                self._trace_mark("first_audio")

                try:
                    stream.write(out)
                except Exception as e:
                    print(f"[Aethelark] ❌ Stream write: {e}")
                    break
        except Exception as e:
            print(f"[Aethelark] ❌ Playback thread error: {e}")
        finally:
            self.set_speaking(False)
            try:
                stream.stop()
                stream.close()
            except Exception as _e:
                print(f"[main.py] Non-fatal error at line 1518: {_e}")
            print("[Aethelark] 🔊 Playback thread stopped")

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Two-phase briefing optimized for speed:
          Phase 1 — instant greeting (no tools) → speech starts in <1s
          Phase 2 — news pre-fetched in a background thread while Phase 1 plays,
                    delivered as ready text (no Gemini tool-call round-trip) and
                    shown on the UI content panel. Waits for turn_complete event
                    instead of a fixed sleep so there is no unnecessary gap.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        # Start fetching news immediately — runs in parallel while phase 1 plays
        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(self._tool_executor, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Phase 1: instant greeting ─────────────────────────────────────────
        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""
        p1 = (
            f"Greet the user, mention it is {time_str}, and say you are fetching today's news now. "
            f"One short sentence only. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event so we can wait for Phase 1 to finish
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 1 (greeting) sent.")

        # ── Phase 2: fire as soon as Phase 1 audio is done ───────────────────
        async def _deliver_news():
            try:
                lang_str = f" Respond in {lang}." if lang else ""

                # Wait for news fetch (already running) and Phase 1 turn-complete
                # in parallel — whichever takes longer determines the wait time
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                # If turn_complete didn't fire (timeout), give a small buffer
                if not turn_waited:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=4.0)
                except Exception:
                    news_text = ""

                if not self.session:
                    return

                if news_text and len(news_text) > 60:
                    # Show on UI content panel immediately
                    self.ui.show_content("NEWS — top world news today", news_text)

                    p2 = (
                        f"[BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then say the full list "
                        f"is displayed on screen. Do not call any tools.{lang_str}"
                    )
                else:
                    p2 = (
                        "News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                await self.session.send_client_content(
                    turns={"parts": [{"text": p2}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                self.ui.write_log(f"SYS: Briefing phase 2 failed: {e}")

        asyncio.create_task(_deliver_news())

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: voice alerts when metrics exceed thresholds."""
        while True:
            await asyncio.sleep(10)
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if alert and self.session:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": alert}]},
                        turn_complete=True,
                    )
                except Exception as e:
                    print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: periodically checks if the user has been silent long enough,
        then hands time + memory context to Gemini so it can decide what (if anything)
        to say proactively. No hardcoded rules — Gemini makes the call.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()

            try:
                memory = await asyncio.to_thread(load_memory)
                prompt = self._proactive.build_prompt(memory)
                await self.session.send_client_content(
                    turns={"parts": [{"text": prompt}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Proactive check-in.")
            except Exception as e:
                print(f"[Proactive] ⚠️ {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        dashboard = self._dashboard
        if dashboard is None:
            return
        q = dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            out_queue = self.out_queue
            if not speaking and not self.ui.muted and out_queue is not None:
                try:
                    out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    try:
                        out_queue.get_nowait()  # Discard oldest stale frame
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        out_queue.put_nowait(chunk)
                    except asyncio.QueueFull:
                        pass
                    self._mic_drops += 1

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        dashboard = self._dashboard
        if dashboard is None:
            return
        while True:
            try:
                text = await asyncio.wait_for(
                    dashboard._command_queue.get(), timeout=0.5
                )
                if not text:
                    continue
                # Wait up to 8s for session to become ready after a wake
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                session = self.session
                if session:
                    await session.send_client_content(
                        turns={"parts": [{"text": text}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped command (no session): {text}")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.5)

    # ── Queue depth monitor ───────────────────────────────────────────────────

    def _session_is_wedged(self, now: float) -> bool:
        """Has the user asked something the server has simply never answered?

        Both halves matter. `silent_for` alone would fire on an idle session,
        where silence is the correct behaviour and reconnecting is pure churn.
        `user_waiting` alone would fire the instant anyone spoke. Together they
        describe the one state worth recovering from: a question was asked, and
        nothing at all has come back for longer than any real answer takes.

        Work in flight is proof of life. A tool that legitimately runs longer
        than the stall window — send_message budgets 130s, swarm_mode 300s — is
        not a wedge, and reconnecting on top of it kills the very work the user
        asked for. So an in-flight tool suppresses the watchdog for exactly as
        long as it runs, and not one tick longer.
        """
        if self._inflight_tools:
            return False
        user_waiting = self._last_user_speech > self._last_server_activity
        silent_for   = now - self._last_server_activity
        return user_waiting and silent_for > _TURN_STALL_S

    def _batch_needs_exclusion(self, function_calls) -> bool:
        """Must this batch be serialised against other batches?

        While tool batches were awaited inline they could never overlap, so the
        scoreboard in _schedule_tool_calls only ever had to resolve hazards
        WITHIN a batch. Concurrent dispatch removes that free guarantee: two
        batches could now drive the mouse, or write files, at the same time.

        Read-only batches are exempt on purpose. TOOL_SPECS marks swarm_status
        and system_status non-exclusive precisely so "what are you building?"
        can answer while the building happens — putting a blunt lock across all
        batches would break the exact interaction this change exists to enable.
        """
        for fc in function_calls:
            spec = TOOL_SPECS.get(fc.name, ToolSpec(exclusive=True))
            if spec.exclusive or spec.writes:
                return True
        return False

    async def _run_and_reply(self, function_calls, call_epoch: int, session) -> None:
        """Run a tool batch and send its response. Always awaited from a
        background task — never from the receive loop, which must stay on the
        wire so audio, go_away frames and barge-ins keep being seen."""
        try:
            guard = (self._tool_batch_lock
                     if self._batch_needs_exclusion(function_calls)
                     else contextlib.nullcontext())
            async with guard:
                # Marked after the lock is held, so `to_action` counts time
                # spent queued behind another batch. That wait is latency the
                # user experiences; marking at dispatch instead would make a
                # contended pipeline look identical to an idle one.
                self._trace_mark("first_tool")
                fn_responses = await self._schedule_tool_calls(function_calls, call_epoch)
            if fn_responses:
                await session.send_tool_response(function_responses=fn_responses)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # The batch scheduler already converts individual tool failures into
            # ToolResult.failure responses, so reaching here means the dispatch
            # itself broke (usually a session closed underneath us). Nothing to
            # send it to — log and let the reconnect path handle the session.
            print(f"[Aethelark] ❌ Tool dispatch failed: {type(e).__name__}: {e}")

    def _dispatch_tool_calls(self, function_calls, call_epoch: int, session):
        """Hand a tool batch to the event loop and return immediately.

        The task is held in `_inflight_tools` for two reasons: asyncio only
        keeps a weak reference to running tasks, so an untracked task can be
        garbage-collected mid-flight; and the wedge watchdog needs to know that
        work is happening before it decides silence means death.
        """
        task = asyncio.create_task(
            self._run_and_reply(function_calls, call_epoch, session)
        )
        self._inflight_tools.add(task)
        task.add_done_callback(self._inflight_tools.discard)
        return task

    async def _cancel_inflight_tools(self) -> None:
        """Drop any tool still running when its session ends.

        While dispatch was awaited inline, tearing down the TaskGroup cancelled
        the running tool as a side effect. Backgrounding the task removes that
        guarantee, and a tool that outlives its session is not harmless: it
        holds the batch lock, it keeps the wedge watchdog suppressed into the
        NEXT session, and it eventually answers a socket that is already gone.
        """
        tasks = list(self._inflight_tools)
        if not tasks:
            return
        print(f"[Aethelark] Cancelling {len(tasks)} in-flight tool(s) — session ended.")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._inflight_tools.clear()

    async def _monitor_queue_depth(self) -> None:
        """Watches session health fast, prints telemetry slowly.

        These were one loop on a 30s sleep, which meant a 25s stall threshold
        took up to 55s to act on — the log cadence was silently deciding how
        long the user sat in front of a mute eagle. They are separate jobs with
        separate natural rates, so the loop now ticks fast for health and
        counts ticks for telemetry: same console noise, ~2x faster recovery.
        """
        tick = 0
        while True:
            await asyncio.sleep(_WATCHDOG_TICK_S)
            tick += 1
            out_queue = self.out_queue
            audio_in_queue = self.audio_in_queue
            if out_queue is None or audio_in_queue is None:
                continue

            if tick % _TELEMETRY_EVERY_N_TICKS == 0:
                mic_q = out_queue.qsize()
                play_q = audio_in_queue.qsize()
                mic_age_ms = mic_q * 64  # each frame ≈ 64ms at 16kHz/1024 samples
                play_age_ms = play_q * 50  # each frame ≈ 50ms at 24kHz/2400 bytes
                drops = self._mic_drops
                print(
                    f"[Telemetry] sid={self._session_id} epoch={self._turn_epoch} "
                    f"mic_q={mic_q}/{out_queue.maxsize}(~{mic_age_ms}ms) "
                    f"play_q={play_q}/{audio_in_queue.maxsize}(~{play_age_ms}ms) "
                    f"mic_drops={drops}"
                )

            # ── Wedged-session watchdog ──────────────────────────────────────
            # Telemetry used to only PRINT. When a session went quiet there was
            # full observability of the freeze and zero recovery from it: the
            # user watched healthy-looking counters while the eagle sat mute.
            if self._session_is_wedged(time.monotonic()):
                silent_for = time.monotonic() - self._last_server_activity
                print(f"[Aethelark] ⏳ No server response for {silent_for:.0f}s "
                      f"with the user waiting — session wedged, reconnecting.")
                self.ui.write_log("NET: No response — reconnecting…")
                self._last_server_activity = time.monotonic()  # arm once, not every tick
                self._interrupted = False                      # never carry a latch across
                sess = self.session
                if sess is not None:
                    try:
                        await sess.close()   # drops _receive_audio into the reconnect path
                    except Exception as e:
                        print(f"[Aethelark] watchdog close failed: {e}")

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._dashboard.serve())
            # Runs for the whole lifetime, not just inside an active session
            asyncio.create_task(self._process_dashboard_commands())
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            connected_ok = False   # True once a live session is actually established
            try:
                _resuming = self._resume_handle is not None
                print(f"[Aethelark] Connecting...{' (resuming session)' if _resuming else ''}")
                self.ui.set_state("THINKING")
                config = self._build_config()

                # Fresh client on every reconnect — avoids stale HTTP session state
                client = genai.Client(
                    api_key=_get_api_key(),
                    http_options={"api_version": "v1beta"}
                )

                if self._play_stop_event:
                    self._play_stop_event.set()
                self._play_stop_event = threading.Event()

                try:
                    async with (
                        client.aio.live.connect(model=LIVE_MODEL, config=config) as session,
                        asyncio.TaskGroup() as tg,
                    ):
                        self.session          = session
                        self.audio_in_queue   = queue.Queue(maxsize=2000)  # ~100s at 50ms/frame — burst-safe buffer
                        self.out_queue        = asyncio.Queue(maxsize=10)   # ~640ms at 64ms/frame
                        self._mic_drops       = 0
                        self._turn_done_event = asyncio.Event()

                        # Reset transient state that must not carry over from a previous session
                        self._pending_vision       = None
                        self._vision_cam_active    = False
                        self._vision_close_pending = False
                        self._vision_busy          = False
                        self._vision_last_time     = 0.0
                        self._interrupted          = False
                        self._interrupt_ts         = 0.0
                        self._turn_had_audio       = False
                        self._last_server_activity = time.monotonic()
                        self._session_id           = str(uuid.uuid4())[:8]
                        self._turn_epoch           = 0

                        connected_ok = True
                        self._backoff.on_connected()   # starts the health timer
                        print(f"[Aethelark] Connected. (session={self._session_id})")
                        self.ui.set_state("LISTENING")
                        self.ui.write_log(f"SYS: Aethelark online. (sid={self._session_id})")

                        if self._dashboard:
                            await self._dashboard.broadcast({"type": "status", "state": "active"})

                        # Start playback thread
                        play_thread = threading.Thread(
                            target=self._play_audio_loop,
                            name="AethelarkPlaybackThread",
                            daemon=True
                        )
                        play_thread.start()

                        tg.create_task(self._send_realtime())
                        tg.create_task(self._listen_audio())
                        tg.create_task(self._receive_audio())
                        tg.create_task(self._run_system_monitor())
                        tg.create_task(self._run_proactive_mode())
                        tg.create_task(self._monitor_queue_depth())
                        if self._dashboard:
                            tg.create_task(self._relay_phone_audio())

                        # Morning briefing — fires once per process launch (if enabled)
                        if not self._briefing_sent and get_brief_enabled():
                            self._briefing_sent = True
                            tg.create_task(self._send_startup_briefing())
                finally:
                    if self._play_stop_event:
                        self._play_stop_event.set()
                    # Tool tasks live outside the TaskGroup, so nothing else
                    # will stop them when this session goes away.
                    await self._cancel_inflight_tools()

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                # Unwrap the group so the true cause (1011 APIError, GoAway signal,
                # bad key) is visible for classification.
                all_excs = _flatten_exc(e)
                err_blob = " | ".join(s for s in (str(x) for x in all_excs) if s)

                graceful = self._go_away_reconnect or any(
                    isinstance(x, _ReconnectSignal) for x in all_excs
                )
                self._go_away_reconnect = False

                # A session that lived long enough to prove the network is fine
                # hands the next reconnect a clean base delay, so an outage that
                # ended hours ago can no longer cost a full minute of silence.
                self._backoff.on_failure()

                if graceful:
                    # Server asked us to migrate connections (GoAway). Expected —
                    # resume immediately with the handle we already hold.
                    print("[Aethelark] Graceful reconnect — resuming session with handle.")
                    self._backoff.set(1)

                elif "API key not valid" in err_blob or "API_KEY_INVALID" in err_blob:
                    # Genuinely invalid key — stop hammering the API, prompt re-config.
                    # NOTE: a WebSocket 1007 is NOT an API-key signal (it's a generic
                    # "invalid frame payload" close — e.g. the audio-content-type
                    # hiccup below), so it must never land here or a transient blip
                    # tears the whole app down demanding a relaunch.
                    print(f"[Aethelark] Error: invalid API key — {err_blob[:200]}")
                    self.ui.write_log("ERR: API key invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui.reconfig_complete():
                        await asyncio.sleep(1)
                    print("[Aethelark] New API key saved — reconnecting...")
                    self._backoff.set(ConnectionBackoff.BASE)
                    self.session = None
                    continue

                elif "CONTENT_TYPE_AUDIO" in err_blob or "audio content type" in err_blob:
                    # The native-audio model sometimes rejects audio right after a
                    # RESUMED reconnect (server returns 1007). It is not a key or a
                    # fatal error — the resume handle is the trigger. Drop it and do
                    # a clean COLD reconnect; a fresh session accepts audio again.
                    print(f"[Aethelark] Audio content-type rejected on resume — cold reconnecting. {err_blob[:160]}")
                    self.ui.write_log("NET: Refreshing the audio session…")
                    self._resume_handle = None
                    self._backoff.set(2)

                elif "1011" in err_blob or "1007" in err_blob or "ConnectionClosedError" in err_blob:
                    # Server-side internal error / connection drop — transient.
                    # Reconnect quickly WITH the resume handle to restore context.
                    print(f"[Aethelark] Gemini connection dropped (1011/1007/closed) — resuming. {err_blob[:160]}")
                    self.ui.write_log("NET: Gemini dropped the connection — resuming…")
                    self._backoff.set(2)

                else:
                    is_net_err = any(k in err_blob for k in (
                        "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                        "ConnectionRefusedError", "OSError", "Cannot connect",
                    ))
                    if is_net_err:
                        self._backoff.grow()
                        _delay = self._backoff.delay
                        print(f"[Aethelark] Network error — retrying in {_delay:.0f}s. {err_blob[:160]}")
                        self.ui.write_log(
                            f"NET: Can't reach the network — retrying in {_delay:.0f}s. "
                            "(a VPN may be required)"
                        )
                    else:
                        # Genuinely unexpected — keep the full traceback for debugging.
                        print(f"[Aethelark] Error ({type(e).__name__}): {e}")
                        traceback.print_exc()
                        self._backoff.set(ConnectionBackoff.BASE)

                # Stale-handle recovery: if we were resuming but never reached a live
                # session, the handle is likely expired/rejected — drop it so the next
                # attempt is a clean cold start rather than looping on a dead handle.
                if self._resume_handle is not None and not connected_ok:
                    print("[Aethelark] Resume handle unusable — clearing for cold reconnect.")
                    self._resume_handle = None
            finally:
                self.session = None

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = self._backoff.delay
            print(f"[Aethelark] Reconnecting in {delay:.0f}s...")
            await asyncio.sleep(delay)

def _run_core(ui, start, *, sleep=time.sleep, max_restarts: int = 5) -> None:
    """Run the assistant core, and do not let it die quietly.

    The core used to run bare on a daemon thread. Anything raised by the
    AethelarkLive constructor, or any non-KeyboardInterrupt escape from run(),
    killed that thread with no supervision and no output — leaving the window
    open, the pill animating, and nothing working, with no indication that
    anything was wrong. A beautiful dead window is the worst failure mode
    available, because it looks fine.

    Restarts are bounded on purpose: endlessly restarting something that is
    permanently broken buries the cause under identical errors. Better to stop
    and say so.
    """
    attempt = 0
    while True:
        try:
            start()
            return                                   # finished on purpose
        except (KeyboardInterrupt, SystemExit):
            print("\n🔴 Shutting down...")
            return
        except BaseException as e:                   # noqa: BLE001
            attempt += 1
            label = f"{type(e).__name__}: {e}"
            traceback.print_exc()
            if attempt > max_restarts:
                print(f"[Aethelark] Core failed {attempt}x — giving up. {label}")
                _safe_log(ui, f"ERR: Core offline — gave up after {attempt} attempts.")
                _safe_state(ui, "SLEEPING")
                return
            delay = min(2 ** attempt, 30)
            print(f"[Aethelark] Core died ({label}) — restarting in {delay}s "
                  f"[{attempt}/{max_restarts}]")
            _safe_log(ui, f"ERR: Core offline ({type(e).__name__}) — restarting in {delay}s…")
            _safe_state(ui, "SLEEPING")
            sleep(delay)


def _safe_log(ui, text: str) -> None:
    """The supervisor must survive a UI that is itself broken."""
    try:
        ui.write_log(text)
    except Exception:
        pass


def _safe_state(ui, state: str) -> None:
    try:
        ui.set_state(state)
    except Exception:
        pass


def main():
    ui = AethelarkUI("face.png")

    # Closing the window must stop the agents we started, not orphan them.
    proc_registry.install_exit_handlers()

    def runner():
        ui.wait_for_api_key()

        def _start():
            core = AethelarkLive(ui)          # constructing it can fail too
            try:
                asyncio.run(core.run())
            finally:
                _shutdown_tool_executor(core._tool_executor)
                _shutdown_web_browser()

        _run_core(ui, _start)

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()