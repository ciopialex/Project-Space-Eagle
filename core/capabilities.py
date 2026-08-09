"""What Aethelark can do, as data rather than prose.

Capability used to live only inside 30 tool descriptions — roughly 210 actions,
exactly one of which was a machine-readable enum. That is fine for a language
model, which reads prose, and useless for everything else. Nothing could answer
mechanically: what can this software do, which of those are safe to begin on a
guess, what does each one need before it will work.

Two failures this week came straight from that gap. 59 working actions in
`computer_settings` were invisible to the model because a hand-written list had
drifted from the implementation. `wait_for_element` and `scroll_into_view` were
dispatchable and undocumented. Neither survives capability being data with a
test behind it.

This is the thing an intent decoder decodes against (see
`docs/Aethelark_Intent_Layer.md`). It is deliberately NOT a router: it says what
exists and what each thing costs, and leaves the choosing to the model.

Split of responsibilities, on purpose:

  * hand-written, because no machine can infer it — what a person might SAY,
    and whether an action can be taken back.
  * checked against the code, because hand-written lists drift — that the tool
    exists, that the action is really dispatched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Can this be undone?
#:
#: The distinction the whole safety story hangs on. READ_ONLY work may be
#: started on a guess: a wrong prediction costs a page load nobody sees.
#: IRREVERSIBLE work may never be, however confident the guess — that is the
#: line `actions/grounding/web/consent.py` already enforces for clicks, stated
#: here for everything.
READ_ONLY = "read_only"        # looking. Costs time, changes nothing.
REVERSIBLE = "reversible"      # changes something the user can undo.
IRREVERSIBLE = "irreversible"  # spends money, deletes data, sends to a person.
EFFECTS = (READ_ONLY, REVERSIBLE, IRREVERSIBLE)


@dataclass(frozen=True)
class Capability:
    """One thing the eagle can do, and everything needed to decide about it."""

    id: str
    tool: str
    action: str
    #: Fragments a person might actually say. Matched as substrings against a
    #: normalised utterance — deliberately dumb, because a fast wrong-ish
    #: hypothesis that only ever pre-warms is cheaper than an inference call.
    says: tuple[str, ...]
    effect: str
    #: Parameters the tool cannot work without. The decoder uses these to know
    #: whether it has enough to act, or only enough to prepare.
    slots: tuple[str, ...] = ()
    #: Preconditions in plain words — "the browser", "a signed-in session".
    #: Not enforced here; this is what a diagnostic reads to explain a refusal.
    needs: tuple[str, ...] = ()
    #: May the intent layer begin this before the model has committed?
    #: Only ever true for READ_ONLY, asserted by the test suite.
    speculative: bool = False


CATALOGUE: tuple[Capability, ...] = (
    # ── The user's own accounts ────────────────────────────────────────────
    Capability(
        id="youtube.liked", tool="youtube_api", action="liked",
        says=("liked video", "liked videos", "videos i liked", "song i liked",
              "what did i like"),
        effect=READ_ONLY, needs=("a connected Google account",),
        speculative=True),
    Capability(
        id="youtube.playlists", tool="youtube_api", action="playlists",
        says=("my playlists", "playlist"),
        effect=READ_ONLY, needs=("a connected Google account",),
        speculative=True),
    Capability(
        id="youtube.subscriptions", tool="youtube_api", action="subscriptions",
        says=("subscriptions", "subscribed to", "channels i follow"),
        effect=READ_ONLY, needs=("a connected Google account",),
        speculative=True),

    # ── Inside a website ───────────────────────────────────────────────────
    Capability(
        id="web.open", tool="web_agency", action="open",
        says=("go to", "open the site", "open the website", ".com", ".ro",
              "on the website", "browse to", "pull up"),
        effect=READ_ONLY, slots=("url",), needs=("the browser",),
        speculative=True),
    Capability(
        id="web.look", tool="web_agency", action="look",
        says=("what is on the page", "what does the page say", "read the page"),
        effect=READ_ONLY, needs=("an open page",), speculative=True),
    Capability(
        id="web.type", tool="web_agency", action="type",
        says=("search for", "type into", "fill in", "enter my"),
        effect=REVERSIBLE, slots=("description", "text"), needs=("an open page",)),
    Capability(
        id="web.click", tool="web_agency", action="click",
        says=("click", "press the button", "tap"),
        effect=REVERSIBLE, slots=("description",), needs=("an open page",)),
    Capability(
        id="web.sign_in", tool="web_agency", action="sign_in",
        says=("sign me in", "log me in", "sign in to"),
        effect=REVERSIBLE, slots=("url",), needs=("the browser", "the user")),

    # ── The machine itself ─────────────────────────────────────────────────
    Capability(
        id="screen.read", tool="screen_process", action="screen",
        says=("what is on my screen", "look at my screen", "what do you see",
              "read my screen", "on screen"),
        effect=READ_ONLY, speculative=True),
    Capability(
        id="settings.volume", tool="computer_settings", action="volume_set",
        says=("volume", "louder", "quieter", "turn it up", "turn it down",
              "mute", "unmute"),
        effect=REVERSIBLE, slots=("value",)),
    Capability(
        id="settings.brightness", tool="computer_settings", action="brightness_up",
        says=("brightness", "dim the screen", "brighter"),
        effect=REVERSIBLE),
    Capability(
        id="control.type", tool="computer_control", action="type",
        says=("type this", "write this", "type out"),
        effect=REVERSIBLE, slots=("text",)),
    Capability(
        id="control.screenshot", tool="computer_control", action="screenshot",
        says=("take a screenshot", "capture the screen"),
        effect=READ_ONLY, speculative=True),

    # ── Files ──────────────────────────────────────────────────────────────
    Capability(
        id="file.read", tool="file_controller", action="read",
        says=("read the file", "open the file", "what is in the file",
              "show me the file"),
        effect=READ_ONLY, slots=("path",), speculative=True),
    Capability(
        id="file.write", tool="file_controller", action="write",
        says=("save to a file", "write to the file", "create a file"),
        effect=REVERSIBLE, slots=("path",)),
    Capability(
        id="file.summarise", tool="file_processor", action="summarize",
        says=("summarise this file", "summarize this document", "what does this pdf say"),
        effect=READ_ONLY, slots=("file_path",), speculative=True),

    # ── Reaching other people, and building ────────────────────────────────
    Capability(
        id="message.send", tool="send_message", action="send",
        says=("send a message", "text ", "message ", "whatsapp"),
        effect=IRREVERSIBLE, slots=("contact", "message"),
        needs=("a linked messaging account",)),
    Capability(
        id="build.project", tool="swarm_mode", action="plan",
        says=("build me", "make me a", "create an app", "build a website"),
        effect=REVERSIBLE, slots=("mission",)),
    Capability(
        id="memory.save", tool="save_memory", action="save",
        says=("remember that", "keep in mind", "note that i"),
        effect=REVERSIBLE, slots=("key", "value")),
    # ── Asking the world ───────────────────────────────────────────────────
    Capability(
        id="search.web", tool="web_search", action="search",
        says=("search the web", "look up", "google", "what is the", "who is",
              "how much does", "what does it cost", "find out"),
        effect=READ_ONLY, slots=("query",), speculative=True),
    Capability(
        id="weather.report", tool="weather_report", action="report",
        says=("weather", "is it going to rain", "how hot", "how cold",
              "forecast", "temperature outside"),
        effect=READ_ONLY, speculative=True),
    Capability(
        id="flights.find", tool="flight_finder", action="search",
        says=("flight", "fly to", "plane ticket", "cheapest flight"),
        effect=READ_ONLY, speculative=True),

    # ── The machine's own state ────────────────────────────────────────────
    Capability(
        id="system.status", tool="system_status", action="status",
        says=("cpu", "ram", "memory usage", "how hot is", "system status",
              "how is my computer", "temperature", "uptime"),
        effect=READ_ONLY, speculative=True),
    Capability(
        id="app.open", tool="open_app", action="open",
        says=("open ", "launch ", "start up ", "fire up"),
        effect=REVERSIBLE, slots=("app_name",)),
    Capability(
        id="desktop.manage", tool="desktop_control", action="organize",
        says=("wallpaper", "tidy my desktop", "organise my desktop",
              "organize my desktop", "clean my desktop"),
        effect=REVERSIBLE),
    Capability(
        id="autostart.set", tool="autostart", action="status",
        says=("start on boot", "launch on startup", "start automatically",
              "auto start"),
        effect=REVERSIBLE),

    # ── Messages ───────────────────────────────────────────────────────────
    Capability(
        id="messages.brief", tool="messages_brief", action="brief",
        says=("what did i miss", "any new messages", "catch me up",
              "unread", "check my email", "anything important", "my inbox"),
        effect=READ_ONLY, speculative=True),
    Capability(
        id="messages.mark_read", tool="mark_emails_read", action="mark",
        says=("mark them read", "mark as read", "clear my unread"),
        effect=REVERSIBLE),

    # ── YouTube, the public half ───────────────────────────────────────────
    Capability(
        id="youtube.play", tool="youtube_video", action="play",
        says=("play the song", "play a video", "put on ", "play me"),
        effect=REVERSIBLE, slots=("query",)),
    Capability(
        id="youtube.history", tool="web_agency", action="open",
        says=("watch history", "my history", "what was i watching"),
        effect=READ_ONLY, needs=("a signed-in browser",), speculative=True),

    # ── The user's own browser ─────────────────────────────────────────────
    Capability(
        id="browser.open_for_user", tool="browser_control", action="go_to",
        says=("open it in my browser", "in my own browser", "open chrome"),
        effect=REVERSIBLE, slots=("url",)),

    # ── Building, in its three sizes ───────────────────────────────────────
    Capability(
        id="code.one_file", tool="code_helper", action="edit",
        says=("fix this script", "edit this file", "explain this code",
              "run this script"),
        effect=REVERSIBLE, slots=("path",)),
    Capability(
        id="code.small_change", tool="developer_mode", action="start",
        says=("small change", "quick script", "one off script"),
        effect=REVERSIBLE),
    Capability(
        id="build.multi_file", tool="dev_agent", action="build",
        says=("build a project", "scaffold", "set up a project"),
        effect=REVERSIBLE, slots=("description",)),
    Capability(
        id="swarm.status", tool="swarm_status", action="status",
        says=("what are you building", "how is it going", "swarm status",
              "what are the agents doing", "are you done"),
        effect=READ_ONLY, speculative=True),
    Capability(
        id="swarm.interject", tool="agent_interject", action="interject",
        says=("stop claude", "tell it to", "interrupt the agent",
              "change the plan"),
        effect=REVERSIBLE, slots=("message",)),

    # ── Games ──────────────────────────────────────────────────────────────
    Capability(
        id="games.manage", tool="game_updater", action="list",
        says=("my games", "update my games", "steam", "epic games",
              "download status"),
        effect=REVERSIBLE),

    # ── Session ────────────────────────────────────────────────────────────
    Capability(
        id="camera.close", tool="close_camera", action="close",
        says=("close the camera", "stop the camera", "turn off the camera"),
        effect=REVERSIBLE),
    Capability(
        id="session.reminder", tool="reminder", action="set",
        says=("remind me", "set a reminder", "wake me", "in an hour"),
        effect=REVERSIBLE, slots=("date", "time")),
    Capability(
        id="session.shutdown", tool="shutdown_aethelark", action="shutdown",
        says=("shut down aethelark", "goodbye for now", "that is all for today",
              "power down", "go to sleep"),
        effect=IRREVERSIBLE),
)


def speculatable() -> tuple[Capability, ...]:
    """Capabilities the intent layer may begin before the model commits.

    Never anything that changes the world. A wrong guess here costs a page
    load nobody sees; a wrong guess on the other side spends money.
    """
    return tuple(c for c in CATALOGUE if c.speculative)


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


def find_by_phrase(utterance: str) -> Capability | None:
    """The most specific capability an utterance mentions, or None.

    None is a real answer and the common one: it means "let the model decide",
    which is today's behaviour. A decoder that always answers is a decoder that
    is often wrong, and this one only ever gets to pre-warm on its guess.

    Longest phrase wins, so "liked videos" beats a bare "video".
    """
    text = _normalise(utterance)
    if not text.strip():
        return None
    best: tuple[int, Capability] | None = None
    for cap in CATALOGUE:
        for phrase in cap.says:
            needle = _normalise(phrase).strip()
            if needle and needle in text:
                if best is None or len(needle) > best[0]:
                    best = (len(needle), cap)
    return best[1] if best else None


def prewarm_for(utterance: str) -> tuple[Capability, ...]:
    """Every safe piece of work an utterance implies, not just the best match.

    `find_by_phrase` answers "what is this person asking for" and returns one
    thing. Speculation needs a different question: "what read-only work is
    already implied here", because the commonest request shape is navigate
    THEN act — "go to emag.ro and search for headphones" resolves to a type,
    which is reversible and may not be started on a guess, while the opening
    of the site is read-only and obviously wanted.

    Asking only the first question meant the commonest shape never pre-warmed
    at all. Found by running it on real sentences rather than reading it.

    Returns only READ_ONLY, speculative capabilities. The filter is here rather
    than at the call site so a future caller cannot forget it.
    """
    text = _normalise(utterance)
    if not text.strip():
        return ()
    hits = []
    for cap in CATALOGUE:
        if not cap.speculative or cap.effect != READ_ONLY:
            continue
        if any(_normalise(p).strip() and _normalise(p).strip() in text
               for p in cap.says):
            hits.append(cap)
    return tuple(hits)


def coverage() -> float:
    """Share of declared tools that have at least one catalogued capability.

    Stated so nobody mistakes this for finished. It should rise deliberately,
    entry by entry, rather than be discovered as a gap later — which is exactly
    how 59 actions went missing.
    """
    try:
        import main
        declared = {d["name"] for d in main.TOOL_DECLARATIONS if isinstance(d, dict)}
    except Exception:
        return 0.0
    if not declared:
        return 0.0
    return len({c.tool for c in CATALOGUE} & declared) / len(declared)
