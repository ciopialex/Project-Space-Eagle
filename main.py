import platform as _platform
import subprocess as _subprocess

# Windows: start every subprocess with CREATE_NO_WINDOW.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            creation_flags = getattr(_subprocess, "CREATE_NO_WINDOW", 0x08000000)
            kw["creationflags"] = kw.get("creationflags", 0) | creation_flags
            kw.pop("startupinfo", None)
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen

import asyncio
import contextlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import re
import threading
import time
import json
import sys

# Line-buffered, so a crash does not lose the end of a redirected log.
from core import logsetup  # noqa: E402,F401
import traceback
import uuid
from datetime import datetime
from pathlib import Path

import sounddevice as sd
import queue
from google import genai
from google.genai import types
from core.confirm import note_user_turn
from core.ui_contract import AethelarkUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
)
from actions.mission import mission
from core.tool_result import ToolResult, normalize
from core.tool_fallback import with_fallback_guidance
from core import diag
from core.vision_guard import VisionGuard
from core.quota import explain_quota, looks_like_quota
from core import proc_registry
from core.turn_trace import TraceLog, TurnTrace, _enabled_by_default as _trace_enabled
from core.mic_vad import SpeechDetector, _rms
from core.barge_in import BargeInDetector
from core.intent import decode as _decode_intent

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen
from core.screen_grab import ScreenshotUnavailable
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
from core import user_paths, prefs
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
LABS_PROMPT_PATH = BASE_DIR / "core" / "prompt_labs.txt"
CODING_PROMPT_PATH = BASE_DIR / "core" / "prompt_coding.txt"
from core.models import VOICE as LIVE_MODEL  # noqa: E402


class _ReconnectSignal(Exception):
    """Collapses the live-session TaskGroup for a reconnect with the resume handle.
    Not an error.
    """


def _flatten_exc(exc: BaseException) -> list[BaseException]:
    """The exception plus its ExceptionGroup members and its cause/context chain."""
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
# Mic frames that may wait for the event loop: 32 x 64 ms, about 2 s of audio. A device
# that delivers faster than real time loses its oldest frames instead of filling memory.
MIC_HANDOFF_MAX     = 32

# How long a deliberate stop keeps discarding the answer it stopped, if turn_complete
# never arrives.
_STOP_LATCH_S = 20.0

# Waits after the free-tier quota runs out, one per consecutive refusal.
_QUOTA_WAITS_S = (30.0, 60.0, 120.0, 300.0)


def _spoken_wait(seconds: float) -> str:
    return (f"{seconds:.0f} seconds" if seconds < 60
            else "1 minute" if seconds < 120 else f"{seconds / 60:.0f} minutes")


# No server frame for this long during a turn means the session is wedged: reconnect.
_TURN_STALL_S = 25.0

# Health-loop interval, well under _TURN_STALL_S.
_WATCHDOG_TICK_S = 2.0

# Telemetry prints every Nth tick.
_TELEMETRY_EVERY_N_TICKS = 15

_TOOL_WORKERS = 8


def _make_tool_executor() -> "ThreadPoolExecutor":
    """The tool thread pool, named so a stuck thread shows up in a stack dump."""
    return ThreadPoolExecutor(
        max_workers=_TOOL_WORKERS, thread_name_prefix="aethelark-tool"
    )


def _shutdown_tool_executor(executor: "ThreadPoolExecutor") -> None:
    """Stop taking new work and abandon the backlog without waiting for running
    tools.
    """
    executor.shutdown(wait=False, cancel_futures=True)


def _shutdown_web_browser() -> None:
    """Close the eagle's own browser if web_agency started one: its daemon thread
    cannot clean up at exit.
    """
    try:
        from actions.grounding.web.browser import default_browser
        default_browser().close()
    except Exception as e:
        print(f"[main.py] Non-fatal error closing the eagle's browser: {e}")


class ConnectionBackoff:
    """Reconnect delay that grows under failure and resets once a session has stayed
    up long enough to prove the path healthy.
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
    """core/prompt.txt, or a stub loud enough to notice in the log."""
    try:
        text = PROMPT_PATH.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[Aethelark] ⚠️  NO SYSTEM PROMPT at {PROMPT_PATH} ({e}). "
              f"Running on the fallback stub: no conversation rules. This is "
              f"a packaging fault, not a setting.",
              file=sys.stderr)
        text = ("You are a voice assistant on the user's computer. Answer in "
                "one short sentence. Never claim a tool worked when its result "
                "says ok is false.")
    extra = []
    if coding_enabled():
        extra.append(CODING_PROMPT_PATH)
    else:
        text += ("\n\nBuilding software, or handing work to a coding agent such "
                 "as Claude Code or Gemini CLI, needs CODING mode: call eagle_mode "
                 "with mode coding first.")
    if prefs.enabled("labs_tools_enabled"):
        extra.append(LABS_PROMPT_PATH)
    for path in extra:
        try:
            part = path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"[Aethelark] {path.name} unreadable: {e}", file=sys.stderr)
            continue
        if "{AGENTS}" in part:
            from core.agent_catalog import spoken_list
            part = part.replace("{AGENTS}", spoken_list())
        text += "\n\n" + part
    return text


def _language_line() -> str:
    """One sentence on which language to answer in: the user's setting, else the
    language they speak.
    """
    lang = str(prefs.get("reply_language") or "").strip()
    if lang:
        return f"Always reply in {lang}, whatever language the user speaks."
    return ("Speak English. Switch to another language only when the user's "
            "latest words are clearly in it, and go back to English as soon as "
            "they speak English again.")

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")

def _clean_transcript(text: str) -> str:
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()


def _collapse_repeats(text: str) -> str:
    """Collapse a transcript that repeats itself ('X. X.') into one copy."""
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
    """Actions read from computer_control._ACTIONS, so the declaration cannot fall
    behind the code.
    """
    try:
        from actions.computer_control import _ACTIONS
        return " | ".join(_ACTIONS)
    except Exception:
        return "type | click | hotkey | press | scroll | screenshot"


def _computer_settings_actions() -> str:
    """Actions read from computer_settings itself, so the declaration cannot fall
    behind the code.
    """
    try:
        from actions.computer_settings import ACTION_MAP
        names = sorted(ACTION_MAP)
    except Exception:
        names = []
    extra = ["volume_set", "brightness_set", "brightness_max", "brightness_min",
             "type_text", "press_key", "reload_n"]
    return ("One of: " + ", ".join(names + extra) + ". "
            "volume_set/brightness_set/type_text/press_key/reload_n take `value`; "
            "brightness_set takes 0-100.")


# Tools that only read: allowed on a turn nobody asked for. mission is absent because
# `next` runs a step.
_READ_ONLY_TOOLS = frozenset({
    "system_status", "swarm_status", "save_memory", "messages_brief",
    "weather_report",
})

# Built-in tools offered only when Labs is on: powerful, account-dependent or less
# exercised.
CODING_TOOLS = frozenset({
    "swarm_mode", "swarm_status", "developer_mode", "dev_agent", "code_helper",
    "agent_interject",
})

LABS_TOOLS = CODING_TOOLS | frozenset({
    "mission", "computer_control", "desktop_control",
    "file_processor", "send_message", "messages_brief", "mark_emails_read",
    "youtube_api", "flight_finder", "island_picks",
})


def coding_enabled() -> bool:
    return prefs.enabled("coding_mode") or prefs.enabled("labs_tools_enabled")


def tool_enabled(name: str) -> bool:
    if name in CODING_TOOLS:
        return coding_enabled()
    if name in LABS_TOOLS:
        return prefs.enabled("labs_tools_enabled")
    return True


# Harness tools for the island's model row and printers, offered only with the module
# tool they drive.
# The module tool the island's print drives.
_PRINT_TOOL = "a3d_print_batch"

MODULE_BACKED_TOOLS = {
    "print_picked": _PRINT_TOOL,
    "island_picks": _PRINT_TOOL,
    "island_set_printer": _PRINT_TOOL,
    "island_deck_move": _PRINT_TOOL,
}


def _missing_tool_result(name: str) -> ToolResult:
    """A tool that does not exist here, usually one from a module the user has not
    installed.
    """
    from core.module_bus import installer
    module = None
    for key, entry in installer.catalog().items():
        prefixes = {key.lower(), *[str(a).lower() for a in entry.get("aliases", [])]}
        if any(name.lower().startswith(p + "_") for p in prefixes):
            module = str(entry.get("title") or key)
            break
    if module:
        return ToolResult.failure(
            f"{module} is not installed on this computer, so that action is not available.",
            guidance=f"Tell the user {module} is not installed and that they can add it "
                     "in Settings. Do not try that action again.")
    return ToolResult.failure(
        f"There is no tool called '{name}'.",
        guidance="Use only the tools you were given. Do not call that name again.")


def _module_tool_available(name: str) -> bool:
    try:
        return any(t.qualified_name == name
                   for m in MODULE_BUS.available() for t in m.tools)
    except Exception:
        return False


def declared_tools(labs: bool | None = None) -> list[dict]:
    """The built-in declarations offered this session."""
    if labs is None:
        enabled = tool_enabled
    else:
        enabled = lambda n: labs or (n not in LABS_TOOLS)
    return [d for d in TOOL_DECLARATIONS
            if enabled(d["name"])
            and (d["name"] not in MODULE_BACKED_TOOLS
                 or _module_tool_available(MODULE_BACKED_TOOLS[d["name"]]))]

# Modules found on this machine. Discovery runs nothing, so it is safe at import.
from core.module_bus import ModuleBus as _ModuleBus
from core.module_bus.manifest import CONFIRM_PARAM, PERMANENT

MODULE_BUS = _ModuleBus(log=lambda m: print(f"[ModuleBus] {m}"))
try:
    MODULE_BUS.discover()
except Exception as _e:                       # never block boot on a module
    print(f"[ModuleBus] disabled: {_e}")
    MODULE_BUS = _ModuleBus(log=lambda m: print(f"[ModuleBus] {m}"))

from core.card_assembly import CardStore

# What the island shows now, so a second tool about the same company extends that card.
CARD_STORE = CardStore()


# Words the model may use for a flip. An unknown direction is 0, never a guess.
_DECK_DELTAS = {"next": 1, "forward": 1, "right": 1,
                "previous": -1, "prev": -1, "back": -1, "left": -1}


def _drawable_here_or_in_deck(card: dict, fields, asked: set) -> bool:
    """True if the card carries a drawn field at the top level or inside its deck of
    candidates.
    """
    def has(obj) -> bool:
        return isinstance(obj, dict) and any(
            obj.get(f) is not None for f in fields if f not in asked)
    if has(card):
        return True
    for key in ("card", "_card"):
        if has(card.get(key)):
            return True
    for key in ("candidates", "deck", "picks"):
        entries = card.get(key)
        if isinstance(entries, list) and any(has(e) for e in entries):
            return True
    return False


def deck_delta(direction) -> int:
    return _DECK_DELTAS.get(str(direction or "").strip().lower(), 0)


# Words for an absolute position in the deck.
_DECK_ORDINALS = {
    "first": 1, "one": 1, "1st": 1, "second": 2, "two": 2, "2nd": 2,
    "third": 3, "three": 3, "3rd": 3, "fourth": 4, "four": 4, "4th": 4,
    "fifth": 5, "five": 5, "5th": 5, "sixth": 6, "six": 6, "6th": 6,
}


def deck_position(word) -> int:
    """A 1-based position, or 0 when the word does not name one."""
    w = str(word or "").strip().lower()
    for junk in ("the ", "number ", "no. ", "#"):
        if w.startswith(junk):
            w = w[len(junk):]
    w = w.replace(" one", "").strip() or str(word or "").strip().lower()
    if w.isdigit():
        return int(w)
    return _DECK_ORDINALS.get(w, 0)


_ISLAND_STAGES = {"summary": "glance", "details": "expanded", "close": "idle"}


def island_view(ui, view, target=None) -> ToolResult:
    """Open the card on screen, or a running activity, at another size. Refuses what
    cannot be shown.
    """
    stage = _ISLAND_STAGES.get(str(view or "").strip().lower())
    if stage is None:
        return ToolResult.failure(
            f"'{view}' is not a view.",
            guidance="Call it again with view set to summary, details or close.")
    opener = getattr(ui, "island_view", None)
    if opener is None:
        return ToolResult.failure("There is no island on this screen.")
    shown = opener(stage, str(target or "").strip())
    if stage == "idle":
        return ToolResult.success("Closed.")
    if shown is True:
        return ToolResult.success("Showing it.")
    names = shown if isinstance(shown, list) else []
    return ToolResult.failure(
        f"Nothing called '{target}' is on the island." if target and names
        else "Nothing is on the island to open.",
        guidance=(f"What is running: {', '.join(names)}. Ask which one they meant."
                  if names else "Tell the user nothing is running right now."))


def island_deck_move(ui, direction) -> ToolResult:
    """Flip the deck on the island, if there is one. The session's tool list is fixed
    at connect time, so the gate lives here.
    """
    if not ui.deck_is_open():
        return ToolResult.failure(
            "There is nothing on screen to flip through.",
            guidance="Search for models first, then the user can flip between "
                     "them.")
    delta = deck_delta(direction)
    if delta:
        ui.island_deck_move(delta)
        return ToolResult.success("Moved.")
    position = deck_position(direction)
    if position:
        if not ui.island_deck_show(position - 1):
            return ToolResult.failure(
                f"There is no number {position} on screen.",
                guidance="Say a position within the row that is showing.")
        return ToolResult.success(f"Showing number {position}.")
    return ToolResult.failure(
        f"I do not know which way {direction!r} is.",
        guidance="Use 'next', 'previous', or a position like 'the second one'.")


def print_picked(ui, bus, confirm_token: str = "") -> ToolResult:
    """Print what is picked on screen. The first call returns a question and a token
    bound to these exact jobs; a second call with the token prints. Changing the
    picks voids the token.
    """
    import json as _json

    if not ui.deck_is_open():
        return ToolResult.failure(
            "There is nothing on screen to print.",
            guidance="Find some models first, then let the user pick one.")
    try:
        picks = _json.loads(ui.island_selection() or "[]")
    except (ValueError, TypeError):
        picks = []
    if not picks:
        return ToolResult.failure(
            "Nothing is picked yet.",
            guidance="Ask which model they want and on which printer, set it, "
                     "then try again.")

    jobs = [{"model_id": str(p.get("model_id")), "printer": str(p.get("printer")),
             "title": str(p.get("title") or "")}
            for p in picks if p.get("model_id") and p.get("printer")]
    if not jobs:
        return ToolResult.failure(
            "The picks have no printer set, so there is nowhere to send them.",
            guidance="Ask which printer each one should go to.")

    args = {"jobs": _json.dumps(jobs)}
    if confirm_token:
        args[CONFIRM_PARAM] = confirm_token
    result = bus.invoke(_PRINT_TOOL, args,
                        timeout_s=TOOL_SPECS[_PRINT_TOOL].timeout_s)

    # Say what is being printed, in words the user can check.
    if result.data.get("needs_confirmation") and result.data.get("confirm_token"):
        named = ", ".join(f"{j['title'] or j['model_id']} on {j['printer']}"
                          for j in jobs)
        return ToolResult.failure(
            f"About to print {named}. Ask the user to confirm, then call this "
            f"again with confirm_token={result.data['confirm_token']!r}.",
            guidance="Say that out loud and wait for a yes. Do not call again "
                     "without one.",
            **result.data)
    return result


def island_picks(ui) -> ToolResult:
    """What the user has picked and where each one goes, read from the screen."""
    import json as _json
    if not ui.deck_is_open():
        return ToolResult.failure(
            "There is nothing on screen to print.",
            guidance="Search for models first, then let the user pick.")
    try:
        picks = _json.loads(ui.island_selection() or "[]")
    except ValueError:
        picks = []
    if not picks:
        return ToolResult.failure(
            "Nothing is picked yet.",
            guidance="Ask the user which one they want and on which printer, "
                     "then set it before printing.")
    named = ", ".join(
        f"{p.get('title') or p['model_id']} on {p['printer']}" for p in picks)
    return ToolResult.success(f"Picked: {named}.", picks=picks,
                              jobs=_json.dumps(picks))


def island_set_printer(ui, printer) -> ToolResult:
    """Send the candidate on screen to a named printer. The name reaches the page as
    JavaScript, so only a printer key is accepted.
    """
    if not ui.deck_is_open():
        return ToolResult.failure(
            "There is nothing on screen to assign to a printer.",
            guidance="Search for models first.")
    name = str(printer or "").strip()
    # Rejected, not sanitised: cleaning bad input can turn it into a different request.
    if not name or not all(c.isalnum() or c in "_-" for c in name):
        return ToolResult.failure(
            f"{printer!r} is not a printer name.",
            guidance="Use the printer's name exactly as it was set up.")
    # Where the fleet is known, the printer must be in it.
    known = []
    try:
        import json as _json
        known = [str(k).upper() for k in _json.loads(ui.island_fleet() or "[]")]
    except (ValueError, TypeError, AttributeError):
        known = []
    if known and name.upper() not in known:
        return ToolResult.failure(
            f"There is no printer called {name!r}.",
            guidance=f"The printers set up here are: {', '.join(known)}.")
    ui.island_set_printer(name.upper())
    return ToolResult.success(f"Set to {name.upper()}.")


TOOL_DECLARATIONS = [
    {
        "name": "island_view",
        "description": (
            "Change how much the island shows for the card on screen or for "
            "something that is running. summary opens the medium card, details "
            "the large one, close returns to the small resting pill (the user "
            "may call it ambient). Use it when the user asks to see, open, "
            "expand, close or hide what is on the island. It never starts or "
            "finds anything, and it never changes the mode."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "view": {"type": "STRING", "enum": ["summary", "details", "close"],
                         "description": "summary, details or close."},
                "target": {"type": "STRING",
                           "description": ("Which running thing, by the name the "
                                           "user said, when more than one is "
                                           "running. Leave empty otherwise.")},
            },
            "required": ["view"]
        }
    },
    {
        "name": "island_deck_move",
        "description": (
            "Move to another model in the row of models currently on screen. "
            "Use when several models are being shown and the user says 'next', "
            "'go back', 'the one before that', 'show me the other one'. Does "
            "nothing if no models are on screen."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "direction": {
                    "type": "STRING",
                    "description": ("next, previous, forward, back — or a "
                                    "position like 'the second one', 'third', "
                                    "'number 2'")
                }
            },
            "required": ["direction"]
        }
    },
    {
        "name": "print_picked",
        "description": (
            "Print the models the user has picked on screen, each on the "
            "printer shown on its card. Use when they say 'print that', "
            "'print those', 'go ahead', 'yes print it'. Takes no model list — "
            "it reads what is actually picked. It prints models already on "
            "the island; it never finds or downloads one — that is a3d_browse. "
            "It asks for confirmation "
            "first: the first call returns a question to say out loud and a "
            "token; only call it again, passing that token, once the user has "
            "actually said yes."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "confirm_token": {
                    "type": "STRING",
                    "description": ("Leave EMPTY the first time. Pass back the "
                                    "token the first call returned, and only "
                                    "after the user agreed out loud.")
                }
            }
        }
    },
    {
        "name": "island_picks",
        "description": (
            "Read which models the user has picked on screen and which printer "
            "each one is going to. Use it to answer 'what did I pick'. It "
            "starts nothing: to print the picks use print_picked, and to find "
            "or download a model use a3d_browse."
        ),
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "island_set_printer",
        "description": (
            "Choose which printer the model currently on screen should go to, "
            "and mark it as picked. Use when the user says 'that one on the "
            "second printer', 'send this one to the Centauri'. This does NOT start "
            "a print, and it does NOT find or download a model — it only "
            "records which printer a model already on the island goes to. To "
            "fetch a model in the first place, use a3d_browse."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "printer": {
                    "type": "STRING",
                    "description": "The printer's name as it was set up"
                }
            },
            "required": ["printer"]
        }
    },
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
            "Looks something up on the web. TWO reasons to call it, and no "
            "others: (1) the user asked you to look it up, search, check or "
            "find out; (2) answering needs a fact you cannot have — what "
            "happened in the world, a live price or number, a specific "
            "company, product or document, something that changes. "
            "DO NOT call it to hold a conversation. When the user is telling "
            "you about their day, explaining a situation, working through a "
            "problem, upset about something, asking your opinion, gossiping, "
            "joking, greeting you, thanking you, or asking about you — talk "
            "back. That is the whole answer and no tool improves it. "
            "A company, a price or an event MENTIONED INSIDE something the "
            "user is telling you is not a request to look it up. Someone "
            "saying their boss cut their hours and rent went up wants to be "
            "heard, not handed search results. Wait until you are asked. "
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
        "description": (
            "Current weather and today's high and low for a city, as a short "
            "sentence to say out loud. Use for 'what's the weather', 'is it "
            "going to rain', 'how cold is it in Paris'."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": (
                    "City name. If the user did not say one, use the city "
                    "you remember they live in.")}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": (
            "Sends a WhatsApp or Telegram message from the user's account. Say the person the way "
            "the user said it; the tool finds the saved contact. If it asks 'did you mean', ask the "
            "user and call again with confirm_token. If it lists several people, ask which one. "
            "Any other app: do it through its website with web_agency."),
        "parameters": {"type": "OBJECT", "properties": {
            "receiver": {"type": "STRING", "description": "Who to message, as the user said it"},
            "message_text": {"type": "STRING", "description": "The message, exactly as it should arrive"},
            "platform": {"type": "STRING", "description": "whatsapp | telegram"},
            "confirm_token": {"type": "STRING", "description": "Only when calling again after the user said yes"},
        }, "required": ["receiver", "message_text", "platform"]},
    },
    {
        "name": "reminder",
        "description": ("Sets, lists or cancels reminders. A reminder shows as "
                        "a notification on this computer when it is due, even "
                        "if Aethelark is closed. Use action 'list' for 'what "
                        "reminders do I have' and 'cancel' for 'cancel the "
                        "dentist reminder'."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "enum": ["set", "list", "cancel"],
                            "description": ("set (the default) makes a new one; "
                                            "list says what is set; cancel removes one.")},
                "date":    {"type": "STRING", "description": "For set: date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "For set: time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": (
                    "For set: what to remind them of. For cancel: words "
                    "from the reminder to cancel, or 'all'.")}
            },
            "required": []
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
            "The camera takes one picture; it does not stay on."
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
                "value":       {"type": "STRING", "description": "Optional value: volume level, text to type, etc."},
                "confirm_token": {"type": "STRING", "description": (
                    "Only for shutdown and restart. Leave EMPTY the first "
                    "time; pass back the token the first call returned, and "
                    "only after the user said yes out loud.")}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Opens a website in the USER'S OWN browser, with their own logins and tabs — "
            "for when they want to look at something themselves. Its click/type/look actions "
            "CAN read and act inside a page already open there — the same exact DOM lookup the "
            "mission tool uses, not pixels — for a single command aimed at something the user can "
            "already see. "
            "For a multi-step task on a site, or a page not already open in the user's own browser, "
            "use web_agency instead, which perceives the page properly and refuses irreversible actions. "
            "Simple open/search requests launch the user's own browser normally (their real profile "
            "and logged-in accounts); interactive actions (click, type, fill_form...) attach an "
            "automation browser. "
            "Always pass the 'browser' parameter when the user specifies a browser (e.g. 'open in Edge', "
            "'use Firefox', 'open Chrome'). Multiple browsers can run simultaneously. "
            "For files on disk use file_controller."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | set_default (persist the user's preferred/default browser) | list_browsers | close | close_all | look. click/type/look now use the same exact DOM lookup the mission tool uses — prefer these over computer_control's screen_click/type for anything inside a browser window."},
                "browser":     {"type": "STRING", "description": "Target browser: chrome | edge | firefox | opera | operagx | brave | vivaldi | safari. Omit to use the currently active browser."},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query for search action"},
                "engine":      {"type": "STRING", "description": "Search engine: google | bing | duckduckgo | yandex (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type — fallback ONLY when 'description' is not given; prefer 'description', it is DOM-exact and survives page structure changing."},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Plain-language description of the control, for click/type/smart_click/smart_type — preferred over 'selector' for click/type. Not used by look, which reads the whole page."},
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
            "If a site needs the user signed in, ask which they prefer: use the "
            "sign-ins already in their Chrome (action='use_chrome', set up once), "
            "or sign in once in a window the eagle opens (action='sign_in' with "
            "that url). Never tell the user to open their own browser, and never "
            "claim you cannot reach their account."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "open | look | click | download | upload | type | sign_in | use_chrome | close. Use download (not click) for anything that produces a FILE - it only reports success when a file is actually on disk. Use upload (not click) to hand a local file to a file input - it only reports success when the control actually holds the file."},
                "url":         {"type": "STRING", "description": "URL for the open action"},
                "description": {"type": "STRING", "description": "Which control, in plain words: 'the Sign in button', 'the Email field'. Use a name from the last look."},
                "text":        {"type": "STRING", "description": "Text to type, for the type action"},
                "path":        {"type": "STRING", "description": "Absolute path of the local file to hand over, for the upload action"},
                "want_pixels": {"type": "BOOLEAN", "description": "Force a screenshot on look, when the structural read is not enough"},
                "timeout":     {"type": "NUMBER", "description": "Seconds to wait for the user during sign_in (default 300)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage -- and undo, which reverses its own last change.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info | undo (reverses the last change this tool made: puts back an overwritten or deleted file)"},
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
            "Run a goal that takes MORE THAN ONE action across DIFFERENT "
            "applications, as a sequence of small verified steps: 'download "
            "the form, fill it in and upload it', 'find X in my email and put "
            "it in a spreadsheet'. It plans the steps, does one per call, "
            "escalates through different ways of doing each one, and never "
            "repeats an approach that already failed.\n"
            "NOT for anything a single tool already does end to end. Check "
            "the tool list first; only if no tool covers the goal is it a "
            "mission."
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
        "description": "Hands one task to ONE coding agent on this computer and types it into that agent's chat. Use when the user names an agent ('have Claude Code add dark mode to my blog', 'ask Gemini to fix the tests in my api project'), for a small change in an existing project, or for a follow-up to an agent already working there (it goes into the same conversation). For a new app or website built from scratch use swarm_mode with action='plan'. Never for websites: that is web_agency.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "prompt": {"type": "STRING", "description": "The task, in the user's words plus anything already known that the agent needs."},
                "project_name": {"type": "STRING", "description": "The project folder's name as the user said it ('my blog', 'api'). Found under ~/Projects and similar; a new one is created if it does not exist."},
                "agent": {"type": "STRING", "description": "Which coding agent, only if the user named one. Omit to use the one already working in that project, or the best installed one."},
                "directory": {"type": "STRING", "description": "Absolute path, only if the user gave one."}
            },
            "required": ["prompt", "project_name"]
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
        "name": "eagle_mode",
        "description": "Switches the eagle between CASUAL and CODING. CODING lets it run the coding agents on this computer (Claude Code, Gemini CLI and others) and build software. Use when the user asks to switch modes, or asks to build software or use a coding agent while in CASUAL. Once in CODING, building goes to swarm_mode or developer_mode, never here. It never changes what the island shows: 'back to ambient', 'small pill' or 'hide it' is island_view close.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "mode": {"type": "STRING", "description": "casual | coding"}
            },
            "required": ["mode"]
        }
    },
    {
        "name": "swarm_status",
        "description": "What the swarm is doing RIGHT NOW, in plain speakable language. Use whenever the user asks 'what are you doing', 'how's it going', 'what's happening', 'are they done yet', 'any progress', or asks about the project while agents are working. Instant and read-only — safe to call at any time, including while agents are mid-build. Returns who is on what, how far in, what finished, and anything waiting on the user. ALWAYS prefer this over swarm_mode action='status' when the user is just asking.",
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "agent_interject",
        "description": "Stop a running coding agent mid-work and optionally redirect it with a new instruction. Use when the user says things like 'stop Claude' or 'tell the agent to use X instead of Y'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "agent":     {"type": "STRING", "description": "The agent as the user named it ('Claude', 'Gemini'). Omit to mean the one most recently started."},
                "directory": {"type": "STRING", "description": "Only if the user named a project."},
                "message":   {"type": "STRING", "description": "Optional new instruction to give it after stopping."},
            },
            "required": []
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
                "direction":   {"type": "STRING", "description": "up | down | left | right (for scroll or move)"},
                "amount":      {"type": "INTEGER", "description": "Distance/scroll amount: pixels for move (default: 100), scroll clicks for scroll (default: 3)"},
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
        "name": "flight_finder",
        "description": ("Finds flights on Google Flights and says the cheapest three. Takes cities "
                        "or airport codes and dates as the user said them ('next Friday', '15 November'). "
                        "If it asks which city, ask the user and call again with the airport code."),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "From: city or airport code, as the user said it"},
                "destination": {"type": "STRING",  "description": "To: city or airport code, as the user said it"},
                "date":        {"type": "STRING",  "description": "Departure date as the user said it"},
                "return_date": {"type": "STRING",  "description": "Return date, only for a round trip"},
                "passengers":  {"type": "INTEGER", "description": "Adults, default 1"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Also save the list to Documents"},
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
            "Quits the Aethelark app completely. ONLY when the user explicitly "
            "asks to quit, close or shut down Aethelark itself ('quit', 'close "
            "yourself', 'shut down Aethelark'), in any language. Saying goodbye, "
            "thanks, 'that's all' or 'see you later' ends the conversation, not "
            "the app: just say bye and call nothing."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes a file the user sent from their phone (the remote dashboard's upload), "
        "or any file given by its full path. "
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
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to English', 'find all email addresses'"
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


class ToolSpec:
    def __init__(self, reads=None, writes=None, exclusive=False, priority=1,
                 timeout_s=30.0, one_at_a_time=False):
        self.reads = set(reads or [])
        self.writes = set(writes or [])
        self.exclusive = exclusive
        self.priority = priority
        self.timeout_s = timeout_s
        # Two calls with the same arguments must not overlap: keyed on arguments, so two
        # prints on two printers can run together.
        self.one_at_a_time = one_at_a_time

TOOL_SPECS = {
    "save_memory": ToolSpec(writes=["memory"], priority=2),
    "open_app": ToolSpec(writes=["desktop"], priority=1),
    "weather_report": ToolSpec(reads=["web"], priority=1),
    "browser_control": ToolSpec(writes=["desktop"], priority=1),
    # Reads the web in its own browser, so it can run while the user does something
    # else.
    "web_agency": ToolSpec(reads=["web"], priority=1, timeout_s=90.0),
    # mission drives real applications a step at a time, so it is exclusive; its status
    # action queues behind the step.
    "mission": ToolSpec(reads=["memory"], writes=["desktop", "file"],
                        exclusive=True, priority=1),
    "island_deck_move":  ToolSpec(writes=["island"], priority=3, timeout_s=5.0),
    "island_view":       ToolSpec(writes=["island"], priority=3, timeout_s=5.0),
    "island_set_printer": ToolSpec(writes=["island"], priority=3, timeout_s=5.0),
    "island_picks":       ToolSpec(reads=["island"], priority=3, timeout_s=5.0),
    "print_picked":       ToolSpec(writes=["printer"], exclusive=True,
                                   priority=2, timeout_s=600.0),
    # One HTTPS call: short and non-exclusive.
    "youtube_api": ToolSpec(reads=["net"], priority=1, timeout_s=20.0),
    "file_controller": ToolSpec(writes=["file"], priority=1),
    "send_message": ToolSpec(writes=["desktop"], exclusive=True, priority=1, timeout_s=90.0),
    "reminder": ToolSpec(writes=["system"], priority=1),
    "youtube_video": ToolSpec(writes=["desktop"], priority=1),
    "screen_process": ToolSpec(reads=["camera", "desktop"], priority=1),
    "computer_settings": ToolSpec(writes=["system"], priority=1),
    "desktop_control": ToolSpec(writes=["desktop"], exclusive=True, priority=1),
    "code_helper": ToolSpec(reads=["memory"], writes=["file"], priority=1, timeout_s=45.0),
    "dev_agent": ToolSpec(reads=["memory"], writes=["file"], priority=1, timeout_s=45.0),
    "developer_mode": ToolSpec(reads=["memory"], writes=["file"], exclusive=True, priority=2, timeout_s=120.0),
    "swarm_mode": ToolSpec(reads=["memory"], writes=["file"], exclusive=True, priority=2, timeout_s=300.0),
    # Read-only and not exclusive: a question about progress never queues behind the
    # work.
    "eagle_mode": ToolSpec(writes=["settings"], exclusive=True, priority=3, timeout_s=10.0),
    "swarm_status": ToolSpec(reads=["memory"], priority=3, timeout_s=10.0),
    "agent_interject": ToolSpec(writes=["file"], priority=3, timeout_s=30.0),
    "web_search": ToolSpec(reads=["web"], priority=1),
    "file_processor": ToolSpec(reads=["file"], writes=["file"], priority=1),
    "computer_control": ToolSpec(writes=["desktop"], exclusive=True, priority=1),
    "flight_finder": ToolSpec(reads=["web"], priority=1, timeout_s=75.0),
    "system_status": ToolSpec(reads=["system"], priority=2),
    "autostart": ToolSpec(writes=["system"], priority=2),
    "messages_brief": ToolSpec(reads=["web", "desktop"], priority=1, timeout_s=100.0),
    "mark_emails_read": ToolSpec(writes=["web"], priority=1, timeout_s=40.0),
    "shutdown_aethelark": ToolSpec(writes=["system"], exclusive=True, priority=3),
}

# Module tools get a spec too, or the scheduler's 30s default cuts off network and
# printer calls. They never touch the eagle's mouse, files or browser, so none is
# exclusive.
# Names in TOOL_SPECS that came from a manifest, so a rediscovery can replace them.
_MODULE_SPEC_NAMES: set[str] = set()


def register_module_specs() -> None:
    """Scheduling specs for every available module tool, from its manifest. Hand-set
    entries win; entries added earlier are replaced.
    """
    for name in list(_MODULE_SPEC_NAMES):
        TOOL_SPECS.pop(name, None)
    _MODULE_SPEC_NAMES.clear()
    for mod in MODULE_BUS.available():
        for tool in mod.tools:
            if tool.qualified_name in TOOL_SPECS:
                continue
            fallback = 600.0 if getattr(tool, "confirm", False) else 90.0
            TOOL_SPECS[tool.qualified_name] = ToolSpec(
                reads=tool.reads or (() if tool.writes else ("net",)),
                writes=tool.writes,
                exclusive=tool.exclusive,
                priority=1,
                one_at_a_time=bool(tool.one_at_a_time),
                timeout_s=tool.seconds if tool.seconds else fallback)
            _MODULE_SPEC_NAMES.add(tool.qualified_name)


register_module_specs()


def reload_modules() -> tuple[set[str], set[str]]:
    """Look for installed modules again. Returns (added, removed) keys. Runs nothing,
    so it is cheap.
    """
    before = {m.key for m in getattr(MODULE_BUS, "available", lambda: [])()}
    try:
        MODULE_BUS.discover()
    except Exception as e:
        print(f"[ModuleBus] rediscovery failed: {e}")
        return set(), set()
    register_module_specs()
    after = {m.key for m in MODULE_BUS.available()}
    return after - before, before - after


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
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        # Refuses a repeat of the same question about the same surface.
        self._vision_guard         = VisionGuard()
        self._interrupted          = False   # True while draining audio after user interrupt
        self._interrupt_ts         = 0.0
        self._interrupt_until      = 0.0     # when the discard latch lets go on its own
        # Whether the server is still working on the current answer: set by its audio
        # and tool calls, cleared by turn_complete or interrupted.
        self._model_turn_active    = False
        # Set by a stop: the receive loop closes the user's line at the next message.
        self._close_heard          = False
        # Talking over the eagle. _play_peak is the speaker level now (playback thread
        # writes, event loop reads). _barge_reply is True while the detector follows the
        # current reply; _barge_on is the Settings toggle when it began.
        self._barge = BargeInDetector(frame_ms=1000.0 * CHUNK_SIZE / SEND_SAMPLE_RATE)
        self._barge_reply          = False
        self._barge_on             = True
        self._play_peak            = 0.0
        # Backstop for straggler audio from a cancelled turn; turn_complete normally
        # clears the latch.
        self._turn_had_audio       = False   # did THIS turn produce speakable audio?
        self._last_server_activity = time.monotonic()  # watchdog: any server frame resets this
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_island_subject = self._on_island_subject
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.stop_speaking
        self.ui.on_stop           = self.stop_speaking
        self.ui.on_modules_changed = self.reload_modules
        self.ui.on_coding_mode = self.set_coding_mode
        self.ui.on_card_action = self.card_action
        self.ui.on_session_refresh = self.refresh_session
        self.ui.on_refine_pick = self.refine_pick
        from core import escalations
        escalations.add_listener(self._on_escalation)
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._proactive        = ProactiveEngine()
        self._last_user_speech = time.monotonic()  # updated on every user utterance

        self._session_id:  str = ""              # unique per live-session connection
        self._turn_epoch:  int = 0               # monotonic counter; incremented on barge-in and turn_complete
        self._shutdown_requested = False          # graceful shutdown flag

        self._mic_drops: int = 0                  # count of oldest-mic-frames dropped due to overflow
        # Audio thread -> event loop. One wake-up is pending at a time, however fast
        # the device delivers.
        self._mic_handoff: deque = deque(maxlen=MIC_HANDOFF_MAX)
        self._mic_handoff_drops: int = 0          # written by the audio thread only
        self._mic_drain_due = False

        # Gemini Live's rolling resumption token: a reconnect with it keeps the
        # conversation.
        self._resume_handle: str | None = None
        self._go_away_reconnect  = False          # set when server signals imminent GoAway

        # Reconnect pacing (ConnectionBackoff).
        self._backoff = ConnectionBackoff()
        #: Consecutive sessions ended by an exhausted quota. Cleared by the
        #: first turn that completes, which proves the allowance is back.
        self._quota_strikes = 0
        # Typed while there was no session: (monotonic time, text). Sent when the
        # session is back, if still fresh.
        self._pending_text: list[tuple[float, str]] = []
        self._refresh_lock = threading.Lock()

        # Tool batches running off the receive loop. A strong reference (asyncio keeps
        # only a weak one); tells the wedge watchdog that silence is work.
        self._inflight_tools: set[asyncio.Task] = set()
        # "<tool>|<json args>" -> the future already running that call, so a duplicate
        # does not start a second copy.
        self._inflight_by_args: dict[str, "asyncio.Future"] = {}

        # Serialises batches that write or claim exclusivity. Read-only batches skip it.
        self._tool_batch_lock = asyncio.Lock()

        # Tools get their own bounded pool so slow ones cannot starve asyncio.to_thread.
        self._tool_executor = _make_tool_executor()

        # Latency tracing (AETHELARK_TRACE=1). Inert when off.
        self._speculated = False
        self._trace_on = _trace_enabled()
        self._trace: TurnTrace | None = None
        self._trace_log = TraceLog(emit=lambda line: print(f"[Aethelark] {line}"))
        self._vad = SpeechDetector(rate=SEND_SAMPLE_RATE,
                                   frame_samples=CHUNK_SIZE) if self._trace_on else None
        # Always-on detector so the island shows when the user is talking.
        self._hearing_vad = SpeechDetector(rate=SEND_SAMPLE_RATE,
                                           frame_samples=CHUNK_SIZE,
                                           hangover_ms=450)
        self._hearing = False
        self._mic_shield_on = prefs.enabled("mic_shield_enabled")
        # When the eagle's own voice has left the room; stamped by set_speaking(False).
        self._echo_clear_at = 0.0
        self._echo_reset_due = False
        #: Which deck is on the island. A new deck bumps it, and a background
        #: refinement of an older one stops at its next step.
        self._deck_gen = 0
        from core.module_bus import accounts as _accounts
        _accounts.subscribe(self._on_site_signed_in)
        from actions.grounding.web import chrome_link as _chrome_link
        _chrome_link.notify = getattr(ui, "show_notice", None)
        _chrome_link.request_setup = getattr(ui, "request_chrome_setup", None)
        # The playback stream's own latency, read when it opens.
        self._out_latency_s = 0.05

    # Trace helpers run on four threads in the hot path, so they never raise.

    def _trace_mark_at(self, name: str, when: float) -> None:
        t = self._trace
        if t is not None:
            t.mark_at(name, when)

    def _trace_mark(self, name: str) -> None:
        t = self._trace
        if t is not None:
            t.mark(name)

    def _trace_begin(self) -> None:
        """Open a new turn. The previous one is dropped, not reported."""
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
        """Warm the browser while the user is still speaking, if the request looks
        like web work. Read-only. Never raises.
        """
        if self._speculated:
            return                      # transcription streams in many chunks
        try:
            intent = _decode_intent(partial)
            if not any(c.id == "web.open" for c in intent.prewarm):
                return
            # Match whole intents, not verbs: "click" in "why can't you click" is not a
            # web request.
            from core.intent import worth_warming
            if not worth_warming(partial):
                return
            self._speculated = True
            self._trace_mark("speculated")
            print(diag.intent_line(intent))
            self._warm_browser()
        except Exception:
            pass

    def _trace_mic_frame(self, data) -> None:
        """Timestamp the user's speech from the mic frames actually sent, so the
        estimate matches the audio the server judged.
        """
        if self._vad is None:
            return
        event = self._vad.feed(data)
        if event == "start":
            self._trace_begin()
            self._trace_mark("speech_start")
        elif event == "end":
            # The detector says "end" hangover_ms after speech stops, so back-date the
            # mark.
            self._trace_mark_at("speech_end",
                                time.monotonic() - (self._vad.hangover_ms / 1000.0))

    def _feed_hearing(self, data) -> str | None:
        """Two events per utterance: the user started talking, and stopped."""
        # The mic reopens when the reply is written to the device, not heard: skip the
        # tail so the eagle's own voice does not count as the user. The frames still go
        # to Gemini.
        if time.monotonic() < self._echo_clear_at:
            self._echo_reset_due = True
            return None
        if self._echo_reset_due:
            # Here, not in set_speaking: the detector is only touched from the event
            # loop.
            self._echo_reset_due = False
            self._hearing_vad.reset()
        event = self._hearing_vad.feed(data)
        if self._hearing:
            level = _rms(data)
            if level is not None:
                self.ui.set_audio_level(min(level / 3000.0, 1.0))
        if event == "start":
            # The user spoke, so the next turn is theirs and may act.
            self._unprompted_turn = False
        if event == "start" and not self._hearing:
            self._set_hearing(True)
        elif event == "end" and self._hearing:
            self._set_hearing(False)
        return event

    def _set_hearing(self, on: bool) -> None:
        # Logged because it freezes the island's clock: a card does not decay while it
        # is on.
        print(f"[hearing] {'on' if on else 'off'}")
        self._hearing = bool(on)
        fn = getattr(self.ui, "set_hearing", None)
        if fn is None:
            return
        try:
            fn(self._hearing)
        except Exception:
            pass

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Remote control is off. Turn it on in Settings.")
            return None
        key    = self._dashboard.new_key()
        url    = self._dashboard.get_url()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    # How long a typed request stays worth sending after a reconnect.
    _PENDING_TEXT_S = 60.0

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            self._pending_text = (self._pending_text + [(time.monotonic(), text)])[-3:]
            self.ui.write_log("SYS: Reconnecting — I'll send that the moment I'm back.")
            return
        # Typed input is a request, same as speech, so it clears the background gate.
        self._unprompted_turn = False
        note_user_turn(text, typed=True)
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    async def _flush_pending_text(self, session) -> None:
        """Send what was typed while there was no session, if still fresh."""
        pending, self._pending_text = self._pending_text, []
        now = time.monotonic()
        for at, text in pending:
            if now - at > self._PENDING_TEXT_S:
                self.ui.write_log(f"SYS: Not sent — too long ago: {text[:60]}")
                continue
            self._unprompted_turn = False
            note_user_turn(text, typed=True)
            await session.send_client_content(
                turns={"parts": [{"text": text}]}, turn_complete=True)

    def _on_island_subject(self, line: str) -> None:
        """What the island shows, sent with turn_complete=False so it does not
        provoke a reply but is there when the user next speaks. [ISLAND] is
        explained in core/prompt.txt. Fails silently: no session means the model
        just does not learn it.
        """
        if not self._loop or not self.session or not line:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self.session.send_client_content(
                    turns={"parts": [{"text": line}]},
                    turn_complete=False,
                ),
                self._loop,
            )
        except Exception as e:
            print(f"[Island] could not report subject: {e}")

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            was = self._is_speaking
            self._is_speaking = value
        if was and not value:
            # Output buffer plus one mic frame plus 0.1 s of room decay.
            self._echo_clear_at = (time.monotonic() + self._out_latency_s
                                   + CHUNK_SIZE / SEND_SAMPLE_RATE + 0.1)
        if value and self._hearing:
            # The mic does not send while the eagle talks, so close the utterance here.
            self._hearing_vad.reset()
            self._set_hearing(False)
        
        def _update_gui():
            if value:
                self.ui.set_state("SPEAKING")
            elif not self.ui.muted:
                self.ui.set_state("LISTENING")
                
        if self._loop:
            self._loop.call_soon_threadsafe(_update_gui)
        else:
            _update_gui()

    def stop_speaking(self) -> None:
        """The user tapped the island or pressed Esc: stop."""
        self.interrupt()

    def interrupt(self) -> None:
        """Stop the eagle mid-speech: drain queued audio and open the mic. Frames
        still arriving for the stopped answer are discarded until the server ends
        it, but only if it is still sending one. One stop for a tap, Esc or
        talking over it.
        """
        streaming = self._model_turn_active
        self._interrupted = streaming
        self._close_heard = True
        self._interrupt_ts = time.monotonic()
        self._interrupt_until = (self._interrupt_ts + _STOP_LATCH_S
                                 if streaming else 0.0)
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
        # An interrupted turn has no real end, so it is not reported.
        self._trace = None
        if self._vad is not None:
            self._vad.reset()
        self.ui.write_log("SYS: Interrupted — listening...")

    def _log_heard(self, in_buf: list[str]) -> None:
        """Log one line of what the user said, from its streamed fragments."""
        full_in = _collapse_repeats(_clean_transcript("".join(in_buf)))
        if not full_in:
            return
        self.ui.write_log(f"You: {full_in}")
        if self._dashboard:
            asyncio.create_task(self._dashboard.broadcast({
                "type": "log", "speaker": "user",
                "text": full_in,
                "ts": datetime.now().isoformat(),
            }))

    def _drop_queued_audio(self) -> None:
        """Silence what is queued for the speakers. For when the server ended the
        answer, so nothing more of it will arrive.
        """
        self._turn_epoch += 1
        q = self.audio_in_queue
        if q:
            while True:
                try:
                    q.get_nowait()
                except Exception:
                    break
        self.set_speaking(False)

    def _discard_stragglers(self) -> bool:
        """Should the frame just received be thrown away? After a barge-in the server
        may still flush the cancelled turn's audio under the new epoch. Time-
        bounded, so a missing turn_complete cannot mute the session.
        """
        if not self._interrupted:
            return False
        if time.monotonic() > self._interrupt_until:
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

        # Identity from config overrides any name in prompt.txt.
        _addr = f"ADDRESS: Always call the user '{_user_name}'." if _user_name else ""
        identity_ctx = (
            f"[IDENTITY]\n"
            f"Your name is {self._asst_name}. "
            f"Always refer to yourself as {self._asst_name}.\n"
            f"{_addr}\n\n"
        )

        parts = [time_ctx, identity_ctx, _language_line() + "\n"]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)
        # Only installed modules add instructions, so the prompt never names a tool the
        # model lacks.
        try:
            module_notes = MODULE_BUS.instructions()
        except Exception:
            module_notes = ""
        if module_notes:
            parts.append("\n" + module_notes)

        _voice = (_cfg.get("voice_name") or "Puck").strip()

        # Silence after speech before the server ends the turn: paid on every reply.
        # Below about 250 ms a pause mid-sentence ends the turn. Tune with
        # end_of_turn_silence_ms in config.
        _silence_ms = int(_cfg.get("end_of_turn_silence_ms") or 350)
        # Audio kept from before speech onset, so a first syllable is not clipped.
        _prefix_ms = int(_cfg.get("speech_prefix_padding_ms") or 150)

        # Thinking off by default: it delays simple turns, and streamed thoughts can
        # return text instead of audio (a silent turn). Budget is configurable.
        _think_budget = int(_cfg.get("thinking_budget") or 0)

        # Reply length cap, off by default: too low cuts a reply mid-word. Set
        # max_reply_tokens in config.
        _max_tokens = _cfg.get("max_reply_tokens")
        _max_tokens = int(_max_tokens) if _max_tokens else None

        builtin = declared_tools()
        self._session_tools = frozenset(d["name"] for d in builtin)
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            **({"max_output_tokens": _max_tokens} if _max_tokens else {}),
            output_audio_transcription={},
            input_audio_transcription={},
            system_instruction="\n".join(parts),
            tools=[{"function_declarations": builtin
                                     + MODULE_BUS.tool_declarations()}],
            session_resumption=types.SessionResumptionConfig(
                handle=self._resume_handle
            ),
            # A sliding window, so the server does not end the session when its context
            # fills (about fifteen minutes of audio).
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
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

    async def _dispatch_one_tool(self, fc, call_epoch: int = -1) -> types.FunctionResponse:
        """Run a tool, unless an identical call is already running; the duplicate
        gets the first one's answer. Keyed on arguments: the same print twice is a
        machine started twice.
        """
        # Applies to every tool: a slow tool re-issued by the model would answer twice.
        spec = TOOL_SPECS.get(fc.name)

        try:
            key = f"{fc.name}|{json.dumps(dict(fc.args or {}), sort_keys=True, default=str)}"
        except (TypeError, ValueError):
            # Arguments that will not serialise cannot be compared: refuse for
            # one_at_a_time tools, run otherwise.
            if spec and spec.one_at_a_time:
                print(f"[Tool] ! {fc.name} refused - arguments could not be "
                      f"compared against the call already running")
                return types.FunctionResponse(
                    id=fc.id, name=fc.name,
                    response=ToolResult.failure(
                        f"{fc.name} was not run: its arguments could not be "
                        f"checked against a copy that may already be running.",
                        guidance="Ask the user to say the request again, "
                                 "simply.").to_response())
            return await self._execute_tool(fc, call_epoch)

        running = self._inflight_by_args.get(key)
        if running is not None:
            print(f"[Tool] ⇉ {fc.name} already running with the same arguments "
                  f"— waiting for it instead of starting a second")
            try:
                first = await asyncio.shield(running)
            except Exception as e:
                return types.FunctionResponse(
                    id=fc.id, name=fc.name,
                    response=ToolResult.failure(
                        f"{fc.name} failed: {e}",
                        guidance="Report the failure honestly; do not claim it "
                                 "worked.").to_response())
            # One response per call: same answer, this call's id.
            return types.FunctionResponse(id=fc.id, name=fc.name,
                                          response=first.response)

        future: "asyncio.Future" = asyncio.get_event_loop().create_future()
        self._inflight_by_args[key] = future
        try:
            result = await self._execute_tool(fc, call_epoch)
        except BaseException as e:
            if not future.done():
                future.set_exception(e)
            raise
        else:
            if not future.done():
                future.set_result(result)
            return result
        finally:
            self._inflight_by_args.pop(key, None)
            # Retrieve the exception so garbage collection does not log it.
            if future.done() and not future.cancelled():
                future.exception()

    async def _execute_tool(self, fc, call_epoch: int = -1) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})
        if call_epoch < 0:
            call_epoch = self._turn_epoch

        _t0 = time.monotonic()
        print(diag.tool_call(name, args, call_epoch))
        self.ui.set_state("THINKING")

        # A turn the user did not ask for may look but not touch: background wake-ups
        # must not act.
        if getattr(self, "_unprompted_turn", False) and name not in _READ_ONLY_TOOLS:
            print(f"[Tool] ⛔ {name} refused — nobody asked for this turn")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response=ToolResult.failure(
                    f"'{name}' was not run: this turn was a background check, "
                    f"not a request from the user.",
                    guidance="Say something brief if it is genuinely useful, or "
                             "nothing at all. Do not take actions the user did "
                             "not ask for.").to_response())

        # A tool the session did not declare is refused.
        if not tool_enabled(name):
            print(f"[Tool] ⛔ {name} refused — switched off")
            guidance = ("Tell the user to switch the eagle to CODING mode, the "
                        "CASUAL / CODING switch at the top of the dashboard. "
                        "Do not try to do it another way."
                        if name in CODING_TOOLS else
                        "Tell the user you cannot do that yet. Do not try to "
                        "do it another way.")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response=ToolResult.failure(
                    f"'{name}' is not available.",
                    guidance=guidance).to_response())

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
                result = await loop.run_in_executor(
                    self._tool_executor, lambda: send_message(parameters=args, player=self.ui))

            elif name == "reminder":
                result = await loop.run_in_executor(self._tool_executor, lambda: reminder(parameters=args, response=None, player=self.ui))

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
                        print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                        _stall = "camera"
                    else:
                        _stall = "screen"
                        try:
                            img_b, mime_t = await loop.run_in_executor(self._tool_executor, _capture_screen)
                            print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                        except ScreenshotUnavailable as _e:
                            # Said to the user, and vision is free again: left busy,
                            # every later look was refused as "already on its way".
                            img_b, _unavailable = None, str(_e)
                            self._vision_busy = False
                            self._vision_guard.mark_delivered()
                            print(f"[Vision] screen unavailable — {_unavailable[:90]}")
                    if img_b is None:
                        result = ToolResult.failure(_unavailable, guidance=_unavailable)
                    else:
                        self._pending_vision = (img_b, mime_t, user_text, angle)
                        # A ToolResult, with the byte count: it tells a real frame from
                        # a black one.
                        result = ToolResult.success(
                            f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                            f"Immediately say ONE short natural sentence in the user's own language, "
                            f"telling them you are looking at their {_stall} right now. "
                            f"Do NOT describe or guess content — the actual image arrives in the NEXT message.",
                            angle=_stall, captured_bytes=len(img_b), mime=mime_t)

            elif name == "island_view":
                result = island_view(self.ui, args.get("view"), args.get("target"))

            elif name == "island_deck_move":
                result = island_deck_move(self.ui, args.get("direction"))

            elif name == "island_set_printer":
                result = island_set_printer(self.ui, args.get("printer"))

            elif name == "island_picks":
                result = island_picks(self.ui)

            elif name == "print_picked":
                result = await loop.run_in_executor(
                    self._tool_executor,
                    lambda: print_picked(self.ui, MODULE_BUS,
                                         args.get("confirm_token", "")))

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
                # Await the launch: agent.run() confirms the session is alive.
                r = await developer_mode(parameters=args, player=self.ui)
                result = r or "Developer session started."

            elif name == "eagle_mode":
                result = self._switch_mode(str(args.get("mode") or ""))

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
                # Mirror results to the on-screen content panel.
                _mode = args.get("mode", "search")
                _ok = r.ok if isinstance(r, ToolResult) else bool(
                    r and not str(r).startswith(("No results", "Search failed")))
                _text = r.message if isinstance(r, ToolResult) else r
                if _ok and _text:
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, _text)
            elif name == "file_processor":
                if not args.get("file_path"):
                    uploaded = getattr(self, "_last_upload", None) or self.ui.current_file
                    if uploaded:
                        args["file_path"] = uploaded
                r = await loop.run_in_executor(
                    self._tool_executor,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(self._tool_executor, lambda: computer_control(parameters=args, player=self.ui))
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

            elif name == "shutdown_aethelark" and self._shutdown_requested:
                result = "Already closing."

            elif name == "shutdown_aethelark":
                self.ui.write_log("SYS: Shutdown requested — graceful shutdown in progress.")
                self._shutdown_requested = True
                # No self.speak("Goodbye."): speak() sends a user turn, and the model
                # would call this tool again. Wait for the model's own goodbye audio,
                # then exit.
                async def _graceful_shutdown():
                    await asyncio.sleep(2.5)  # let goodbye audio play
                    print("[Aethelark] 🔴 Graceful shutdown: closing session...")
                    self.ui.write_log("SYS: Shutting down...")
                    self.set_speaking(False)
                    # Close the eagle's own browser, off the event loop: close() blocks
                    # for a few seconds.
                    try:
                        await loop.run_in_executor(self._tool_executor,
                                                   _shutdown_web_browser)
                    except Exception as _e:
                        print(f"[main.py] Non-fatal error at line 1071: {_e}")
                    if self._dashboard:
                        try:
                            await self._dashboard.broadcast({"type": "status", "state": "offline"})
                        except Exception as _e:
                            print(f"[main.py] Non-fatal error at line 1065: {_e}")
                    # Close the UI on the Qt thread.
                    try:
                        self.ui.request_shutdown()
                    except Exception as _e:
                        print(f"[main.py] Non-fatal error at line 1070: {_e}")
                    # Fallback if Qt has not exited within 3 s.
                    await asyncio.sleep(3)
                    print("[Aethelark] 🔴 Fallback exit.")
                    sys.exit(0)
                asyncio.create_task(_graceful_shutdown())

            elif MODULE_BUS.owns(name):
                # A module tool runs in its own process with its own timeout; invoke
                # never raises.
                spec = TOOL_SPECS.get(name, ToolSpec())
                # Internal module tools take a payload the harness builds and are never
                # offered to the model; a call naming one is refused.
                if MODULE_BUS.is_internal(name):
                    result = ToolResult.failure(
                        f"'{name}' is not a tool you can call.",
                        guidance="Use the tools you were given; to print what "
                                 "is picked on screen, use print_picked.")
                else:
                    result = await loop.run_in_executor(
                        self._tool_executor,
                        lambda: self._invoke_module(name, args, spec.timeout_s))

            else:
                result = _missing_tool_result(name)

        except Exception as e:
            result = ToolResult.failure(
                f"Tool '{name}' failed: {e}",
                guidance="Tell the user this action failed; do not claim it worked.")
            traceback.print_exc()
            self.speak_error(name, str(e))

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        # Normalise any tool return into the ToolResult contract.
        tr = normalize(result)

        # A narrow tool failing does not make the task impossible: attach the general
        # tool that covers it.
        tr = with_fallback_guidance(name, tr)
        # A rate limit is a wait, not a tool failure.
        tr = explain_quota(tr)

        _elapsed_ms = (time.monotonic() - _t0) * 1000
        if call_epoch != self._turn_epoch:
            print(f"[Tool] ⚠ {name} STALE (epoch {call_epoch} → "
                  f"{self._turn_epoch}) — the turn moved on; returned anyway "
                  f"for protocol")
            print(diag.tool_result(name, tr, _elapsed_ms))
        else:
            print(diag.tool_result(name, tr, _elapsed_ms))

        try:
            import json as _j
            parsed_data: dict = {}
            if hasattr(tr, "data") and isinstance(tr.data, dict) and "result" in tr.data:
                res_val = tr.data["result"]
                if isinstance(res_val, dict):
                    parsed_data.update(res_val)
            if not parsed_data and hasattr(tr, "message") and isinstance(tr.message, str):
                msg_str = tr.message.strip()
                if msg_str.startswith("{") and msg_str.endswith("}"):
                    try:
                        parsed_data.update(_j.loads(msg_str))
                    except Exception:
                        pass
            if not parsed_data and isinstance(result, dict):
                parsed_data.update(result)
            elif not parsed_data and isinstance(result, str) and result.strip().startswith("{"):
                try:
                    parsed_data.update(_j.loads(result.strip()))
                except Exception:
                    pass

            # Forward payloads to the island only for real module tools.
            mod_key = MODULE_BUS.module_of(name)
            if mod_key:
                payload = dict(parsed_data)
                for k, v in args.items():
                    # The spent token and the echoed jobs string are not rendered.
                    if k in ("confirm_token", "jobs"):
                        continue
                    if k not in payload:
                        payload[k] = v
                # One card per company: results fold into a card keyed by ticker, so a
                # later view keeps what an earlier one fetched.
                card = CARD_STORE.absorb(name, payload, args)
                # Not every answer is a card: it earns the screen when it carries a
                # field the module's cards draw beyond its own identity (manifest
                # `shows` and `about`).
                if self._card_is_drawable(name, card, args):
                    # 0 lets the island's dwell model time it: 3 s, extended by
                    # interaction up to 22.5 s.
                    self.ui.set_pill_context(mod_key, card, ttl_s=0)
                    self._refine_deck(name, card)
                else:
                    print(f"[main.py] {name}: no card — the payload carries "
                          f"nothing this module's cards draw")
        except Exception as _ctx_e:
            print(f"[main.py] Pill dispatch error: {_ctx_e}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response=tr.to_response()
        )

    def card_action(self, module: str, action: str, args: dict) -> tuple[bool, str]:
        tool = next((t for m in MODULE_BUS.available() if m.key == module
                     for t in m.tools if t.name == action), None)
        if tool is None:
            return False, "That control does nothing here."
        if tool.confirm or tool.danger == PERMANENT or tool.internal:
            return False, "That one needs a spoken yes. Say it instead."
        allowed = {p.name for p in tool.params}
        clean = {k: str(v) for k, v in (args or {}).items() if k in allowed}
        spec = TOOL_SPECS.get(tool.qualified_name, ToolSpec())
        print(f"[Island] card control: {tool.qualified_name} {clean}")
        result = self._invoke_module(tool.qualified_name, clean, spec.timeout_s)
        return result.ok, ("" if result.ok else result.message)

    def _invoke_module(self, name: str, args: dict, timeout_s: float) -> ToolResult:
        """Run a module tool; if it answers needs_account, lend it the sign-in and
        run it once more. Never re-run a permanent or confirmed tool: the model is
        told to call again.
        """
        tr = MODULE_BUS.invoke(name, args, timeout_s=timeout_s)
        site = "" if tr.ok else str((tr.data or {}).get("needs_account") or "")
        if not site:
            return tr
        from core.module_bus import accounts
        manifest = MODULE_BUS.manifest_of(name)
        fix = accounts.repair(manifest, site, MODULE_BUS.which)
        print(f"[accounts] {name} needs {site}: {fix['detail']}")
        tool = manifest.tool(name) if manifest else None
        if fix["ok"]:
            if tool and tool.danger != PERMANENT and not tool.confirm:
                return MODULE_BUS.invoke(name, args, timeout_s=timeout_s)
            return ToolResult.failure(
                tr.message,
                guidance=(f"The {site} sign-in was just handed to the module. "
                          f"Call this again now."),
                **(tr.data or {}))
        return ToolResult.failure(
            tr.message,
            guidance=(f"This needs the user's {site} account, and the eagle's "
                      f"browser is not signed in to it. Tell the user, and "
                      f"offer to open the sign-in window: call web_agency "
                      f"with action='sign_in' and url='https://{site}'. They "
                      f"sign in once there; then call this again."),
            **(tr.data or {}))

    def _on_site_signed_in(self, url: str) -> None:
        """A sign-in in the eagle's browser completed: lend it to modules that
        declared that account. Off-thread.
        """
        from core.module_bus import accounts

        def work():
            accounts.after_sign_in(url, MODULE_BUS.available(), MODULE_BUS.which)
            refresh = getattr(self.ui, "refresh_module_shop", None)
            if refresh:
                refresh()
        threading.Thread(target=work, name="accounts", daemon=True).start()

    def _refine_deck(self, tool_name: str, card: dict) -> None:
        """Record how this deck's cards are refined. Nothing is fetched until the
        user picks a card.
        """
        self._deck_gen += 1
        cands = card.get("candidates") if isinstance(card, dict) else None
        try:
            found = MODULE_BUS.deck_refiner(tool_name) if cands else None
        except Exception:
            found = None
        self._deck_refiner = found
        self._deck_inflight = set()

    def refine_pick(self, value: str) -> None:
        """Refine the one card the user picked, once, and merge the answer in."""
        found = getattr(self, "_deck_refiner", None)
        if not found or not value or value in self._deck_inflight:
            return
        tool, deck = found
        gen = self._deck_gen
        self._deck_inflight.add(value)
        spec = TOOL_SPECS.get(tool)
        timeout = spec.timeout_s if spec else 120.0

        def work():
            t0 = time.monotonic()
            try:
                tr = self._invoke_module(tool, {deck.param: str(value)}, timeout)
            except Exception as e:
                print(f"[deck] {tool} {value}: {e}")
                return
            finally:
                self._deck_inflight.discard(value)
            res = (tr.data or {}).get("result")
            print(f"[deck] picked {value}: {'ready' if tr.ok else 'failed'} "
                  f"({(time.monotonic() - t0) * 1000:.0f}ms)")
            if gen == self._deck_gen and tr.ok and isinstance(res, dict):
                self.ui.island_refine(deck.key, value, res)

        threading.Thread(target=work, name="deck-pick", daemon=True).start()

    @staticmethod
    def _card_is_drawable(tool_name: str, card: dict, args: dict | None = None) -> bool:
        """Whether a module's cards could draw anything in this answer, from its
        manifest: `about` identifies the card, `shows` is what it draws. A module
        with no [island] face sends every answer to the screen.
        """
        # An empty payload, including a top-level JSON array, is never a card.
        if not card:
            return False
        try:
            face = MODULE_BUS.island_face(tool_name)
        except Exception:
            return True
        if face is None or not face.cards:
            # A module with a card but no face: the answer must fill a field of its
            # template beyond the question's own.
            fields = MODULE_BUS.island_fields(tool_name)
            if not fields:
                return True
            asked = set(args or {})
            return _drawable_here_or_in_deck(card, fields, asked)
        # Ask about the card that opens, not any card the module owns.
        opens = face.card(face.first) or face.cards[0]
        drawn = set(opens.shows)
        drawn.discard(face.about)
        return _drawable_here_or_in_deck(card, drawn, set())

    async def _schedule_tool_calls(self, tool_calls, call_epoch: int) -> list[types.FunctionResponse]:
        """Scoreboard scheduler with RAW, WAW and WAR hazard detection: independent
        tools run together, conflicting or exclusive ones in sequence.
        """
        running = {}  # task -> (tool_call, spec)
        results = {}  # tool_call_id -> FunctionResponse

        # The same call twice in one batch runs once; the copy gets the first one's
        # answer.
        twins = {}    # copy's id -> the call that actually runs
        first = {}    # identity -> that call
        unique = []
        for fc in tool_calls:
            try:
                ident = f"{fc.name}|{json.dumps(dict(fc.args or {}), sort_keys=True, default=str)}"
            except (TypeError, ValueError):
                unique.append(fc)
                continue
            if ident in first:
                twins[fc.id] = first[ident]
            else:
                first[ident] = fc
                unique.append(fc)
        pending = list(unique)
        
        pending.sort(key=lambda fc: TOOL_SPECS.get(fc.name, ToolSpec()).priority, reverse=True)
        
        while pending or running:
            issued = []
            for fc in list(pending):
                spec = TOOL_SPECS.get(fc.name, ToolSpec(exclusive=True))
                
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
            
            for fc in issued:
                spec = TOOL_SPECS.get(fc.name, ToolSpec(exclusive=True))
                
                async def run_with_timeout(f_call=fc, f_spec=spec):
                    try:
                        return await asyncio.wait_for(
                            self._dispatch_one_tool(f_call, call_epoch=call_epoch),
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
                
            done, _ = await asyncio.wait(running.keys(), return_when=asyncio.FIRST_COMPLETED)
            
            for task in done:
                fc, spec = running.pop(task)
                res = task.result()
                results[fc.id] = res

        for copy_id, original in twins.items():
            done = results.get(original.id)
            if done is not None:
                results[copy_id] = types.FunctionResponse(
                    id=copy_id, name=original.name, response=done.response)

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

    def _push_mic_queue(self, msg) -> None:
        out_queue = self.out_queue
        if out_queue is None:
            return
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

    def _enqueue_mic(self, msg) -> None:
        """Send one mic frame to Gemini. Event-loop thread only. Drops the oldest on
        overflow. With mic_shield_enabled, non-speech is zeroed while idle and
        pre-roll is sent at speech onset.
        """
        data = msg.get("data")
        if data is None:
            self._push_mic_queue(msg)
            return

        event = self._feed_hearing(data)

        if not self._mic_shield_on:
            self._trace_mic_frame(data)
            self._push_mic_queue(msg)
            return

        if self._hearing:
            if event == "start":
                preroll = self._hearing_vad.take_preroll()
                mime = msg.get("mime_type", "audio/pcm")
                for pre in preroll:
                    self._trace_mic_frame(pre)
                    self._push_mic_queue({"data": pre, "mime_type": mime})
            self._trace_mic_frame(data)
            self._push_mic_queue(msg)
        else:
            # Shield: send zeroed frames, which keeps the audio clock running.
            mime = msg.get("mime_type", "audio/pcm")
            self._push_mic_queue({"data": b"\x00" * len(data), "mime_type": mime})

    def _mic_callback(self, loop, data: bytes) -> None:
        """Audio thread. Hands one frame to the event loop. At most MIC_HANDOFF_MAX
        frames wait and at most one wake-up is scheduled, so memory and the loop's
        queue stay flat even when the device runs faster than real time.
        """
        handoff = self._mic_handoff
        if len(handoff) == handoff.maxlen:
            self._mic_handoff_drops += 1
        handoff.append(data)
        if not self._mic_drain_due:
            self._mic_drain_due = True
            loop.call_soon_threadsafe(self._drain_mic)

    def _drain_mic(self) -> None:
        """Event loop. Feeds the frames waiting now, oldest first. Frames that arrive
        meanwhile wait for the next wake-up, so other work on the loop keeps its turn.
        """
        self._mic_drain_due = False
        handoff = self._mic_handoff
        for _ in range(len(handoff)):
            try:
                data = handoff.popleft()
            except IndexError:
                return
            self._on_mic_frame(data)

    def _on_mic_frame(self, data: bytes) -> None:
        """Every mic frame, on the event loop. While the eagle speaks nothing is
        sent, because Gemini cannot tell the user from the eagle's own voice; the
        frame goes to the barge-in detector instead.
        """
        with self._speaking_lock:
            speaking = self._is_speaking
        if not speaking:
            self._barge_reply = False
            self._enqueue_mic({"data": data, "mime_type": "audio/pcm"})
            return
        if not self._barge_reply:
            # A reply just started: reset the detector here, on the event loop. The
            # setting is read once per reply.
            self._barge_reply = True
            self._barge_on = prefs.enabled("barge_in_enabled")
            self._mic_shield_on = prefs.enabled("mic_shield_enabled")
            self._barge.start_utterance()
        if self._barge_on and self._barge.feed(
                data, self._play_peak, self._hearing_vad.noise_floor):
            self._barge_in()

    def _barge_in(self) -> None:
        """The user is talking over the eagle: stop, and listen to all of it."""
        heard = self._barge.take_preroll()
        print(f"[Aethelark] 🗣 Barge-in — {self._barge.describe()}")
        self.interrupt()
        # What the user said while the detector was deciding, so the server hears their
        # first word.
        for frame in heard:
            self._enqueue_mic({"data": frame, "mime_type": "audio/pcm"})

    async def _listen_audio(self):
        print("[Aethelark] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            if self.ui.muted:
                return
            # Never block the PortAudio callback.
            self._mic_callback(loop, indata.tobytes())

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

                    # After a stop, what the user says next starts a new line.
                    if self._close_heard:
                        self._close_heard = False
                        self._log_heard(in_buf)
                        in_buf = []

                    # Store the latest resumption handle so a reconnect restores
                    # context.
                    sru = response.session_resumption_update
                    if sru is not None and sru.resumable and sru.new_handle:
                        self._resume_handle = sru.new_handle

                    # GoAway: reconnect now with the handle, before a hard 1011.
                    if response.go_away is not None:
                        _left = getattr(response.go_away, "time_left", None)
                        print(f"[Aethelark] 🔻 GoAway received (time_left={_left}) — reconnecting with resume handle")
                        self.ui.write_log("NET: Session refreshing — reconnecting…")
                        self._go_away_reconnect = True
                        raise _ReconnectSignal("go_away")

                    self._last_server_activity = time.monotonic()

                    # First byte of the answer, not of the connection or the user's
                    # transcription.
                    _sc = response.server_content
                    _answered = bool(
                        response.data                       # audio payload
                        or response.tool_call               # a tool call
                        or (_sc and (getattr(_sc, "model_turn", None)
                                     or getattr(_sc, "output_transcription", None)
                                     or getattr(_sc, "turn_complete", None))))
                    # Not while a stopped answer is still being discarded.
                    if _answered and not self._interrupted:
                        self._trace_mark("first_token")

                    if response.data:
                        self._model_turn_active = True
                        if self._discard_stragglers():
                            pass  # tail of the cancelled turn — drop it
                        else:
                            self._turn_had_audio = True
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # About 50 ms chunks (2400 bytes at 24 kHz, 16-bit) so
                            # interrupt() stops audio within 50 ms; each tagged with the
                            # turn epoch.
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
                                        # Log rather than drop output frames: dropping
                                        # corrupts speech.
                                        print(f"[Aethelark] ⚠️ Playback queue overflow — frame dropped (epoch={_epoch})")

                    if response.server_content:
                        sc = response.server_content

                        # Text parts of an audio turn: kept so turn_complete can surface
                        # a silent turn. `thought` parts are never spoken or shown.
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
                            # Raw, joined with nothing: the transcription streams mid-
                            # word and carries its own spaces. Clean once, at the end.
                            raw = sc.input_transcription.text
                            if raw:
                                in_buf.append(raw)
                                self._last_user_speech = time.monotonic()
                                note_user_turn(raw)
                                # Heard by the server: the turn is the user's.
                                self._unprompted_turn = False
                                # While they are still talking, the only point early
                                # enough to predict.
                                self._speculate(_clean_transcript("".join(in_buf)))

                        # The server stopped the answer it was sending: drop what is
                        # queued for it.
                        if getattr(sc, "interrupted", False):
                            self._model_turn_active = False
                            if self._interrupted:
                                self._interrupt_until = time.monotonic()
                            else:
                                self._drop_queued_audio()

                        if sc.turn_complete:
                            self._model_turn_active = False
                            self._quota_strikes = 0
                            self._speculated = False
                            # The end of a stopped answer is not the end of the turn
                            # being timed.
                            if not self._interrupted:
                                self._trace_mark("complete")
                                self._trace_finish()
                            self._turn_epoch += 1
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # A turn_complete ending an interrupted response: clear the
                            # flag and skip the rest. in_buf is the next turn.
                            if self._interrupted:
                                self._interrupted = False
                                out_buf = []
                                text_buf = []
                                self._turn_had_audio = False
                                continue

                            # A turn that returns text instead of audio is silent: log
                            # it loudly. Not re-voiced through another TTS engine.
                            _textonly = " ".join(t.strip() for t in text_buf if t.strip()).strip()
                            text_buf = []
                            if _textonly and not self._turn_had_audio:
                                print(f"[Aethelark] ⚠️ TEXT-ONLY TURN (no audio): {_textonly[:200]}")
                                self.ui.write_log(f"{self.ui.assistant_name}: {_textonly}")
                                out_buf = []          # already surfaced; don't double-log
                            self._turn_had_audio = False

                            self._log_heard(in_buf)
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

                            # Vision: the tool-response turn finished, so send the image
                            # now.
                            if self._pending_vision and session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session via send_realtime_input")
                                self._vision_guard.mark_delivered()
                                # The picture and the question in one turn. Sent as a
                                # realtime frame with the question as a separate turn,
                                # the model answered without it: 0 of 3 saw the image,
                                # against 3 of 3 this way (same image and question).
                                await session.send_client_content(
                                    turns=types.Content(role="user", parts=[
                                        types.Part(inline_data=types.Blob(data=img_b, mime_type=mime_t)),
                                        types.Part(text=question),
                                    ]),
                                    turn_complete=True,
                                )
                                # The picture is in; the next capture may go.
                                self._vision_busy = False

                    if response.tool_call:
                        self._model_turn_active = True
                        # Dispatch and return to the wire: awaiting the batch here made
                        # the session deaf for the tool's duration.
                        self._dispatch_tool_calls(
                            response.tool_call.function_calls,
                            call_epoch=self._turn_epoch,
                            session=session,
                        )
        except _ReconnectSignal:
            raise  # intentional graceful reconnect — no error logging
        except Exception as e:
            # Known transient server drops are reported by the run loop; no traceback.
            _es = str(e)
            if self._go_away_reconnect and _es.startswith("1000"):
                print("[Aethelark] Recv: session closed to refresh its tools")
            elif "1011" in _es or "ConnectionClosed" in _es:
                print(f"[Aethelark] Recv: connection closed by server ({_es[:80]})")
            else:
                print(f"[Aethelark] ❌ Recv: {e}")
                traceback.print_exc()
            raise

    def _play_audio_loop(self):
        print("[Aethelark] 🔊 Playback thread started")
        # Play at the model's native 24 kHz; fall back to 48 kHz with resampling only if
        # the device refuses.
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
            # Close the half-opened stream before retrying, so no device handle leaks.
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

        try:
            self._out_latency_s = float(stream.latency or 0.0)
        except Exception:
            pass

        # Prime the buffer with silence so the first write does not race the DAC.
        try:
            stream.write(b'\x00' * (playback_rate * 2 * 80 // 1000))  # 80ms silence
        except Exception as _e:
            print(f"[main.py] Non-fatal error at line 1444: {_e}")

        audio_in_queue = self.audio_in_queue
        play_stop_event = self._play_stop_event
        loop = self._loop

        if audio_in_queue is None or play_stop_event is None or loop is None:
            return

        # Only compute the level envelope when the UI renders it.
        wants_level = getattr(self.ui, "consumes_audio_level", True)

        def _is_speaking_now() -> bool:
            with self._speaking_lock:
                return self._is_speaking

        try:
            while not play_stop_event.is_set() and not self._shutdown_requested:
                try:
                    item = audio_in_queue.get(timeout=0.05)
                except queue.Empty:
                    # Nothing to play: lower the barge-in threshold during a pause mid-
                    # answer.
                    self._play_peak *= 0.6
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and audio_in_queue.empty()
                    ):
                        # End of turn: drop SPEAKING once.
                        if _is_speaking_now():
                            self.set_speaking(False)
                        loop.call_soon_threadsafe(self._turn_done_event.clear)
                    continue

                frame_epoch, chunk = item
                if frame_epoch < self._turn_epoch:
                    continue

                # Edge-trigger on the shared flag so it re-arms after interrupt() forced
                # it False.
                if not _is_speaking_now():
                    self.set_speaking(True)

                # Playback level, for telling the user from their echo
                # (core/barge_in.py). Fast up, slow down.
                self._play_peak = max(_rms(chunk) or 0.0, self._play_peak * 0.6)

                out = chunk
                # Native rate and web UI: hand raw PCM to the device, no numpy.
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

                # Marked at the device write, so `audio` is our own playback cost.
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


    async def _send_startup_briefing(self) -> None:
        """Two-phase briefing: an instant greeting, then news fetched in parallel and
        sent as text once the greeting's turn completes.
        """
        memory   = load_memory()
        identity = memory.get("identity", {})

        def _val(k: str) -> str:
            e = identity.get(k, {})
            return (e.get("value", "") if isinstance(e, dict) else str(e)).strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        loop = asyncio.get_event_loop()
        news_future = loop.run_in_executor(self._tool_executor, _fetch_news_sync, "top world news today")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""
        # Tagged [STARTUP_BRIEFING] and marked unprompted, so the dispatch gate keeps it
        # from acting.
        p1 = (
            f"[STARTUP_BRIEFING] Greet the user, mention it is {time_str}, and say you are fetching today's news now. "
            f"One short sentence only. Do not call any tools.{lang_clause}{name_clause}"
        )

        if self._turn_done_event:
            self._turn_done_event.clear()

        self._unprompted_turn = True
        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Briefing phase 1 (greeting) sent.")

        async def _deliver_news():
            try:
                lang_str = f" Respond in {lang}." if lang else ""

                # Wait for the news fetch and the greeting's turn_complete together.
                news_done   = asyncio.wrap_future(news_future)
                turn_waited = False
                if self._turn_done_event:
                    try:
                        await asyncio.wait_for(self._turn_done_event.wait(), timeout=6.0)
                        turn_waited = True
                    except asyncio.TimeoutError:
                        pass

                if not turn_waited:
                    await asyncio.sleep(1.0)

                try:
                    news_text = await asyncio.wait_for(news_done, timeout=4.0)
                except Exception:
                    news_text = ""

                if not self.session:
                    return

                if news_text and len(news_text) > 60:
                    self.ui.show_content("NEWS — top world news today", news_text)

                    # The dashboard log is usually closed, so the model offers to read
                    # more instead of pointing at it.
                    p2 = (
                        f"[STARTUP_BRIEFING] Here are today's top news headlines:\n{news_text}\n\n"
                        "Pick ONE headline, summarise it in one sentence, then offer to "
                        f"read more of them. Do not call any tools.{lang_str}"
                    )
                else:
                    p2 = (
                        "[STARTUP_BRIEFING] News headlines could not be fetched right now. "
                        f"Let the user know briefly.{lang_str}"
                    )

                if self._unprompted_turn is False:
                    # The user spoke while the greeting played: their turn wins.
                    return
                await self.session.send_client_content(
                    turns={"parts": [{"text": p2}]},
                    turn_complete=True,
                )
                self.ui.write_log("SYS: Briefing phase 2 (news) sent.")
            except Exception as e:
                print(f"[Briefing] Phase 2 error: {e}")
                self.ui.write_log(f"SYS: Briefing phase 2 failed: {e}")

        asyncio.create_task(_deliver_news())


    async def _run_system_monitor(self) -> None:
        """Voice alerts when metrics cross thresholds. Off unless switched on;
        checked every cycle so the switch needs no restart.
        """
        while True:
            await asyncio.sleep(10)
            if not prefs.enabled("system_alerts_enabled"):
                continue
            alert = await asyncio.to_thread(self._sys_monitor.check)
            if alert and self.session:
                try:
                    self._unprompted_turn = True      # a monitor alert, not a request
                    await self.session.send_client_content(
                        turns={"parts": [{"text": alert}]},
                        turn_complete=True,
                    )
                except Exception as e:
                    print(f"[Monitor] ⚠️ Could not send alert: {e}")


    async def _run_proactive_mode(self) -> None:
        """After a long silence, hand time and memory to Gemini to decide whether to
        say anything.
        """
        while True:
            await asyncio.sleep(60)   # evaluate once per minute

            if not self.session or not prefs.enabled("proactive_enabled"):
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            if not self._proactive.should_trigger(self._last_user_speech):
                continue

            self._proactive.mark_triggered()
            # An unprompted turn: nothing it decides may act. Cleared when the user next
            # speaks.
            self._unprompted_turn = True

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

    def _on_phone_upload(self, path) -> None:
        """A phone sent a file: it is what "this file" means from now on."""
        self._last_upload = str(path)
        self.ui.write_log(f"SYS: Got {Path(path).name} from your phone.")

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()


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
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.1)
                session = self.session
                if session:
                    self._unprompted_turn = False   # typed by the user
                    note_user_turn(text, typed=True)
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


    def _session_is_wedged(self, now: float) -> bool:
        """Has the user asked something the server never answered? Needs both a
        waiting user and silence; a tool in flight counts as life.
        """
        if self._inflight_tools:
            return False
        user_waiting = self._last_user_speech > self._last_server_activity
        silent_for   = now - self._last_server_activity
        return user_waiting and silent_for > _TURN_STALL_S

    def _batch_needs_exclusion(self, function_calls) -> bool:
        """Must this batch be serialised against other batches? Read-only batches are
        exempt so a progress question can answer while work runs.
        """
        for fc in function_calls:
            spec = TOOL_SPECS.get(fc.name, ToolSpec(exclusive=True))
            if spec.exclusive or spec.writes:
                return True
        return False

    async def _run_and_reply(self, function_calls, call_epoch: int, session) -> None:
        """Run a tool batch and send its response, from a background task so the
        receive loop stays on the wire.
        """
        try:
            guard = (self._tool_batch_lock
                     if self._batch_needs_exclusion(function_calls)
                     else contextlib.nullcontext())
            async with guard:
                # Marked after the lock is held, so to_action counts time queued behind
                # another batch.
                self._trace_mark("first_tool")
                fn_responses = await self._schedule_tool_calls(function_calls, call_epoch)
            if fn_responses:
                await session.send_tool_response(function_responses=fn_responses)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Tool failures are already ToolResults, so this is a broken dispatch
            # (usually a closed session): log it and let the reconnect path handle it.
            print(f"[Aethelark] ❌ Tool dispatch failed: {type(e).__name__}: {e}")

    def _dispatch_tool_calls(self, function_calls, call_epoch: int, session):
        """Hand a tool batch to the event loop and return. Held in _inflight_tools:
        asyncio keeps only a weak reference, and the wedge watchdog needs to see
        work.
        """
        task = asyncio.create_task(
            self._run_and_reply(function_calls, call_epoch, session)
        )
        self._inflight_tools.add(task)
        task.add_done_callback(self._inflight_tools.discard)
        return task

    async def _cancel_inflight_tools(self) -> None:
        """Drop any tool still running when its session ends: it would hold the batch
        lock, suppress the next session's watchdog and answer a dead socket.
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
        """Watches session health fast and prints telemetry every Nth tick."""
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
                handoff_drops = self._mic_handoff_drops
                print(
                    f"[Telemetry] sid={self._session_id} epoch={self._turn_epoch} "
                    f"mic_q={mic_q}/{out_queue.maxsize}(~{mic_age_ms}ms) "
                    f"play_q={play_q}/{audio_in_queue.maxsize}(~{play_age_ms}ms) "
                    f"mic_drops={drops} mic_handoff_drops={handoff_drops}"
                )

            # Wedged-session watchdog.
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


    async def _start_dashboard(self) -> bool:
        """Serve the phone remote on the local network. Off by default: it binds
        every interface.
        """
        if self._dashboard is not None:
            return True
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            self._dashboard.set_upload_callback(self._on_phone_upload)
            asyncio.create_task(self._dashboard.serve())
            asyncio.create_task(self._process_dashboard_commands())
            return True
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None
            return False

    def reload_modules(self) -> None:
        """Pick up a module installed or removed while the eagle runs. A session's
        tool list is fixed at connect, so it reconnects with the resume handle
        after any turn in flight.
        """
        added, removed = reload_modules()
        if not (added or removed):
            return
        names = ", ".join(sorted(added | removed))
        print(f"[ModuleBus] modules changed ({names}) — refreshing the session")
        self.ui.write_log(f"SYS: Modules changed: {names}.")
        self.refresh_session()

    def _on_escalation(self, esc) -> None:
        loop, session = self._loop, self.session
        if loop is None or session is None:
            return
        if getattr(esc, "kind", "") == "resolved":
            if getattr(esc, "by", "") == "tap":
                said = "allowed it" if esc.reason == "allow" else "refused it"
                asyncio.run_coroutine_threadsafe(session.send_client_content(
                    turns={"parts": [{"text": f"[ISLAND] The user {said} on the island for "
                                              f"{esc.agent}. Do not ask about it again."}]},
                    turn_complete=False), loop)
            return
        self._unprompted_turn = True
        if getattr(esc, "kind", "") == "done":
            text = (f"[DONE] {esc.agent} {esc.reason} Tell the user in one or "
                    f"two short sentences what it did.")
        elif getattr(esc, "informational", False):
            text = (f"[NOTE] {esc.agent} {esc.reason}; it carried on without it. "
                    f"Tell the user in one short sentence only if it stops the work.")
        else:
            text = (f"[HELD] {esc.agent} is waiting for the user's OK. Why: "
                    f"{esc.reason}. It asks: {esc.excerpt}. Tell the user in one "
                    f"short sentence and ask whether to allow it. If they say yes, "
                    f"call swarm_mode with action authorize; if no, action deny. "
                    f"The island also shows Allow and Don't allow buttons.")
        asyncio.run_coroutine_threadsafe(
            session.send_client_content(turns={"parts": [{"text": text}]},
                                        turn_complete=True), loop)

    def _switch_mode(self, mode: str) -> str:
        on = mode.strip().lower() == "coding"
        self.ui.show_mode("coding" if on else "casual")
        tools_ready = coding_enabled() == on
        self.set_coding_mode(on)
        if tools_ready:
            return (f"The eagle is in {'CODING' if on else 'CASUAL'} mode"
                    + (" and the coding tools are ready: carry on with the request now."
                       if on else "."))
        if on:
            self._pending_text.append(
                (time.monotonic(), "CODING mode is on now. Carry on with what I asked."))
        return (f"Switching to {'CODING' if on else 'CASUAL'} mode; it takes a few "
                f"seconds. Tell the user in one short sentence.")

    def set_coding_mode(self, on: bool) -> None:
        if prefs.enabled("coding_mode") == bool(on):
            return
        prefs.save({"coding_mode": bool(on)})
        self._tools_changed()

    _REFRESH_SETTLE_S = 1.5

    def _tools_changed(self) -> None:
        """Reconnect once the choice has settled, and only if the tools differ."""
        with self._refresh_lock:
            self._refresh_gen = getattr(self, "_refresh_gen", 0) + 1
            gen = self._refresh_gen

        def _later():
            time.sleep(self._REFRESH_SETTLE_S)
            if gen != self._refresh_gen:
                return
            names = frozenset(d["name"] for d in declared_tools())
            if names == getattr(self, "_session_tools", names):
                print("[Aethelark] mode changed; tools unchanged — no reconnect")
                return
            print("[Aethelark] tools changed — refreshing the session")
            self.refresh_session()
        threading.Thread(target=_later, daemon=True).start()

    def refresh_session(self) -> None:
        loop, session = self._loop, self.session
        if loop is None or session is None:
            return

        async def _refresh():
            # Never cut a reply or a tool off mid-flight to add a tool.
            for _ in range(120):
                if not self._inflight_tools and not self._is_speaking:
                    break
                await asyncio.sleep(0.5)
            self._go_away_reconnect = True
            try:
                await session.close()
            except Exception as e:
                print(f"[ModuleBus] session refresh failed: {e}")

        asyncio.run_coroutine_threadsafe(_refresh(), loop)

    async def run(self):
        self._loop = asyncio.get_event_loop()

        if prefs.enabled("remote_dashboard_enabled"):
            await self._start_dashboard()

        while True:
            connected_ok = False   # True once a live session is actually established
            notice = ""            # said on the island while waiting to reconnect
            try:
                _resuming = self._resume_handle is not None
                print(f"[Aethelark] Connecting...{' (resuming session)' if _resuming else ''}")
                if not _resuming:
                    self.ui.set_state("THINKING")
                config = self._build_config()

                # Fresh client on every reconnect.
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
                        self._mic_handoff.clear()
                        self._turn_done_event = asyncio.Event()

                        # Reset transient state from the previous session.
                        self._pending_vision       = None
                        self._vision_busy          = False
                        self._vision_last_time     = 0.0
                        self._interrupted          = False
                        self._interrupt_ts         = 0.0
                        self._interrupt_until      = 0.0
                        self._model_turn_active    = False
                        self._close_heard          = False
                        self._play_peak            = 0.0
                        self._turn_had_audio       = False
                        self._last_server_activity = time.monotonic()
                        self._session_id           = str(uuid.uuid4())[:8]
                        self._turn_epoch           = 0

                        connected_ok = True
                        self._backoff.on_connected()   # starts the health timer
                        print(f"[Aethelark] Connected. (session={self._session_id})")
                        try:
                            mods = ", ".join(sorted(m.key for m in MODULE_BUS.available())) or "none"
                            print(f"[session] {'CODING' if coding_enabled() else 'CASUAL'} · "
                                  f"{len(getattr(self, '_session_tools', ()))} built-in tools · "
                                  f"modules: {mods} · labs "
                                  f"{'on' if prefs.enabled('labs_tools_enabled') else 'off'} · "
                                  f"{'resumed' if _resuming else 'new conversation'}")
                        except Exception as _e:
                            print(f"[session] summary unavailable: {_e}")
                        self.ui.set_state("LISTENING")
                        self.ui.write_log(f"SYS: Aethelark online. (sid={self._session_id})")

                        if self._dashboard:
                            await self._dashboard.broadcast({"type": "status", "state": "active"})

                        play_thread = threading.Thread(
                            target=self._play_audio_loop,
                            name="AethelarkPlaybackThread",
                            daemon=True
                        )
                        play_thread.start()

                        if self._pending_text:
                            tg.create_task(self._flush_pending_text(session))
                        tg.create_task(self._send_realtime())
                        tg.create_task(self._listen_audio())
                        tg.create_task(self._receive_audio())
                        tg.create_task(self._run_system_monitor())
                        tg.create_task(self._run_proactive_mode())
                        tg.create_task(self._monitor_queue_depth())

                        if not self._briefing_sent and get_brief_enabled():
                            self._briefing_sent = True
                            tg.create_task(self._send_startup_briefing())
                finally:
                    if self._play_stop_event:
                        self._play_stop_event.set()
                    # Tool tasks live outside the TaskGroup, so stop them here.
                    await self._cancel_inflight_tools()

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catch BaseExceptionGroup too (TaskGroup raises it on cancellation) and
                # unwrap it so the real cause can be classified.
                all_excs = _flatten_exc(e)
                err_blob = " | ".join(s for s in (str(x) for x in all_excs) if s)

                graceful = self._go_away_reconnect or any(
                    isinstance(x, _ReconnectSignal) for x in all_excs
                )
                self._go_away_reconnect = False

                # A session that lived long enough resets the reconnect delay.
                self._backoff.on_failure()

                # The loop's default executor is shut down, so every DNS lookup and
                # reconnect fails: raise and let _run_core start a new loop (bounded to
                # five).
                if "cannot schedule new futures" in err_blob:
                    self.ui.write_log(
                        "ERR: the event loop died — restarting the core.")
                    print("[Aethelark] Event loop executor is gone — this loop "
                          "cannot recover. Handing back to the supervisor.")
                    raise RuntimeError(
                        "event loop default executor was shut down; "
                        "a fresh loop is required") from e

                if graceful:
                    # GoAway: resume at once with the handle.
                    print("[Aethelark] Graceful reconnect — resuming session with handle.")
                    self._backoff.set(1)

                elif ("API key not valid" in err_blob or "API_KEY_INVALID" in err_blob
                      or "API key expired" in err_blob):
                    # Invalid key: stop and ask for a new one. A WebSocket 1007 is not a
                    # key error.
                    print(f"[Aethelark] Error: invalid API key — {err_blob[:200]}")
                    self.ui.write_log("ERR: Google refused the Gemini key. Enter a new one.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui.reconfig_complete():
                        await asyncio.sleep(1)
                    print("[Aethelark] New API key saved — reconnecting...")
                    self._backoff.set(ConnectionBackoff.BASE)
                    self.session = None
                    continue

                elif "CONTENT_TYPE_AUDIO" in err_blob or "audio content type" in err_blob:
                    # A 1007 right after a resumed reconnect: drop the handle and
                    # reconnect cold.
                    print(f"[Aethelark] Audio content-type rejected on resume — cold reconnecting. {err_blob[:160]}")
                    self.ui.write_log("NET: Refreshing the audio session…")
                    self._resume_handle = None
                    self._backoff.set(2)

                elif looks_like_quota(err_blob):
                    # Free-tier quota exhausted (a 1011 saying "Resource has been
                    # exhausted"): wait, do not reconnect every two seconds.
                    self._quota_strikes += 1
                    wait = _QUOTA_WAITS_S[min(self._quota_strikes,
                                              len(_QUOTA_WAITS_S)) - 1]
                    self._backoff.set(wait)
                    notice = (f"Gemini's free limit is used up · "
                              f"trying again in {_spoken_wait(wait)}")
                    print(f"[Aethelark] Gemini quota exhausted — waiting "
                          f"{wait:.0f}s. {err_blob[:160]}")
                    self.ui.write_log(f"NET: {notice}.")

                elif "1011" in err_blob or "1007" in err_blob or "ConnectionClosedError" in err_blob:
                    # Transient server error or drop: reconnect quickly with the resume
                    # handle.
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
                        # Unexpected: keep the full traceback.
                        print(f"[Aethelark] Error ({type(e).__name__}): {e}")
                        traceback.print_exc()
                        self._backoff.set(ConnectionBackoff.BASE)

                # Resuming never reached a live session: the handle is likely dead, so
                # drop it.
                if self._resume_handle is not None and not connected_ok:
                    print("[Aethelark] Resume handle unusable — clearing for cold reconnect.")
                    self._resume_handle = None
            finally:
                self.session = None

            self.set_speaking(False)
            self.ui.set_state("SLEEPING")
            if notice:
                # After sleeping: every state change repaints the island, so a notice
                # shown first would vanish.
                show = getattr(self.ui, "show_notice", None)
                if show is not None:
                    show(notice)

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = self._backoff.delay
            print(f"[Aethelark] Reconnecting in {delay:.0f}s...")
            await asyncio.sleep(delay)

def _run_core(ui, start, *, sleep=time.sleep, max_restarts: int = 5) -> None:
    """Run the assistant core under supervision, so a crash is reported instead of
    leaving a live-looking dead window. Restarts are bounded.
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
                try:
                    ui.show_notice("Voice stopped. Reopen Aethelark to retry.")
                except Exception:
                    pass
                return
            delay = min(2 ** attempt, 30)
            print(f"[Aethelark] Core died ({label}) — restarting in {delay}s "
                  f"[{attempt}/{max_restarts}]")
            _safe_log(ui, f"ERR: Core offline ({type(e).__name__}) — restarting in {delay}s…")
            _safe_state(ui, "SLEEPING")
            sleep(delay)


def _safe_log(ui, text: str) -> None:
    try:
        ui.write_log(text)
    except Exception:
        pass


def _safe_state(ui, state: str) -> None:
    try:
        ui.set_state(state)
    except Exception:
        pass


