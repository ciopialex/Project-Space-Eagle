"""The rungs, bound to real tools.

Each runner is `(step) -> (ok, detail)`. `ok` must mean the world changed, not
that a call returned — that is the whole basis for having no approval prompts
between steps.

Every one of these goes through a tool that already returns a `ToolResult`, so
"did it work" is read off `ok` rather than guessed from prose. Where a tool
still returns a bare string, `normalize` withholds `ok` and the runner treats
that as a failure: an unmigrated tool cannot be trusted to have done anything,
and a mission is exactly the place where that matters most.
"""
from __future__ import annotations

import re

from pathlib import Path
from typing import Callable

from core.mission import Step
from core.tool_result import ToolResult, normalize


def _verdict(raw) -> tuple[bool, str]:
    """A tool's return, as (ok, detail).

    A result with NO `ok` on the wire is treated as a failure, not a success.
    That is stricter than the rest of the codebase on purpose: elsewhere the
    model reads the prose and decides; here a wrong reading advances a mission
    past a step that never happened.
    """
    tr: ToolResult = normalize(raw)
    resp = tr.to_response()
    if "ok" not in resp:
        return False, f"tool gave no verdict: {tr.message[:120]}"
    return bool(resp["ok"]), tr.message[:200]


def _verdict_data(raw) -> tuple[bool, str, dict]:
    """Same as `_verdict`, plus the tool's `.data` — where a download's saved
    path and an upload's confirmation live. `to_response()` never surfaces
    `.data` to the model (see `web_agency`'s module docstring); it is only for
    this codebase's own callers, which a mission runner is."""
    tr: ToolResult = normalize(raw)
    resp = tr.to_response()
    if "ok" not in resp:
        return False, f"tool gave no verdict: {tr.message[:120]}", {}
    return bool(resp["ok"]), tr.message[:200], tr.data


def _web(action: str, **extra) -> Callable[[Step], tuple[bool, str]]:
    def run(step: Step) -> tuple[bool, str]:
        from actions.web_agency import web_agency
        if action == "open":
            why = _needs_url(step)
            if why:
                return False, why
        params = {"action": action}
        if step.target or step.intent:
            params["description"] = step.target or step.intent
        if step.url:
            params["url"] = step.url
        if step.text:
            params["text"] = step.text
        if step.authorized:
            # NOT a param the model's own tool declaration advertises — see
            # `main.py`'s web_agency entry. If it were, the model could pass
            # `confirmed=True` on any ordinary click and the whole consent
            # guard would be theatre. This path only exists because a HUMAN
            # already said yes to this exact mission, once, up front — see
            # `Mission.authorized` — and reaches web_agency only through
            # this runner, never through anything the model writes itself.
            params["confirmed"] = True
        params.update(extra)
        return _verdict(web_agency(parameters=params))
    return run


def _computer(action: str) -> Callable[[Step], tuple[bool, str]]:
    def run(step: Step) -> tuple[bool, str]:
        from actions.computer_control import computer_control
        params = {"action": action}
        if step.target or step.intent:
            params["description"] = step.target or step.intent
        if step.text:
            params["text"] = step.text
        return _verdict(computer_control(parameters=params))
    return run



def _focused_text_field() -> str | None:
    """The name of the text field that currently has keyboard focus, or None.

    None means "I do not know where typing would land" — and that is the only
    honest answer available when the accessibility layer cannot see a focused
    editable control. It is NOT the same as "there is no field", and it must
    never be read as permission.

    This exists because a mission typed "motherboard" into the user's terminal
    while Claude Code was running in it, and reported success. The same defect
    is documented at the top of actions/whatsapp_web.py from months earlier:
    blind-typing put "Go to sleep" into a terminal instead of a chat.
    """
    try:
        from actions.grounding.resolver import structural_grounder
        g = structural_grounder()
        if g is None:
            return None
        for node in (g.nodes() if hasattr(g, "nodes") else []):
            states = getattr(node, "states", frozenset())
            if "FOCUSED" in states and "EDITABLE" in states:
                return str(getattr(node, "name", "") or "a text field")
    except Exception:
        return None
    return None


def _refuse_blind(step: Step) -> tuple[bool, str]:
    return False, (
        "refusing to type: nothing identifiable has keyboard focus, so the "
        "text could land anywhere — a terminal, a chat, someone's half-filled "
        "form. Click the field first, then type.")


def _runners_screen_type(step: Step) -> tuple[bool, str]:
    """Type through the OS keyboard, but only once focus is known."""
    if not step.text:
        return False, "nothing to type"
    try:
        where = _focused_text_field()
    except Exception:
        where = None                       # fail closed: unknown is not yes
    if not where:
        return _refuse_blind(step)
    from actions.computer_control import computer_control
    ok, detail = _verdict(computer_control(
        parameters={"action": "type", "text": step.text}))
    return ok, (f"{detail} (into {where!r})" if ok else detail)


def _press_keys(step: Step) -> tuple[bool, str]:
    """The last resort, and the one the user had to drive by voice.

    He pressed l, a, p, t, o, p, space, s, t, a, n, d — twelve tool calls and
    twelve model round trips — because `type` was refused and nothing
    escalated. It belongs in the ladder so nobody has to do that again.
    """
    from actions.computer_control import computer_control
    text = step.text or ""
    if not text:
        return False, "nothing to type"
    try:
        where = _focused_text_field()
    except Exception:
        where = None
    if not where:
        return _refuse_blind(step)
    for ch in text:
        key = "space" if ch == " " else ch
        ok, detail = _verdict(computer_control(
            parameters={"action": "press", "key": key}))
        if not ok:
            return False, f"key {key!r} failed: {detail}"
    return True, f"typed {len(text)} characters one key at a time"


def _needs_url(step: Step) -> str:
    """Why an open step cannot run, or "".

    "No URL to open" from the browser is a true statement about a false
    situation: the browser is fine, the STEP is malformed. Naming it here
    means the mission reports a planning defect instead of blaming the tool.
    """
    return "" if step.url else (
        f"the step {step.intent!r} names no address to open — it needs a url")


def _browser_open(step: Step) -> tuple[bool, str]:
    """Opens the USER's browser — which `web_agency` cannot see into.

    Last rung on purpose. The MakerWorld run hit a bot wall in the eagle's own
    browser, fell back to this, and thereby lost every structural tool it had:
    it could see the page on screen and had no way to act on it. Reaching this
    rung means the next steps will be screen-and-vision only.
    """
    from actions.browser_control import browser_control
    why = _needs_url(step)
    if why:
        return False, why
    # Same guard as _user_open: if a window is already showing this page,
    # opening it natively again is another tab and another page load.
    port, _ = _user_window()
    if port is not None and _same_page(port.url(), step.url):
        return True, f"already on {step.url}"
    return _verdict(browser_control(
        parameters={"action": "go_to", "url": step.url}))




def _user_window(create: bool = False):
    """The browser the eagle opened FOR the user, wrapped in the same grounding
    it uses on its own.

    Replaces an earlier attempt that launched that Chrome with
    --remote-debugging-port and connected over CDP from outside. That does not
    work — Playwright's launch_persistent_context runs its own CDP channel and
    no listener appears — and it was unnecessary: browser_control is already
    holding a Playwright Page for the window.
    """
    from core.session_port import user_window
    return user_window(create=create)



def _what_is_here(port, limit: int = 12) -> str:
    """The names actually on the page, for a step that could not find its own.

    A dead end that only says "no control matches" makes the model guess
    again. `web_agency` has always answered this way — "The page has: …; use
    one of those names" — and the mission's own rungs did not, so a step like
    "Click the first result" blocked with nothing to replan from.
    """
    try:
        from actions.grounding.web.page import nodes_from_records
        from actions.web_agency import _spread
        names = []
        # Spread across the page, not the first N. Taking the front returned
        # "Home; All Models; Following; MakerLab" on a page of search results —
        # the sidebar, in document order, which is exactly the positional bias
        # that hides content from the model everywhere else.
        for n in _spread(nodes_from_records(port.collect()), budget=60, run=6):
            nm = str(getattr(n, "name", "") or "").strip()
            if nm and nm not in names:
                names.append(nm)
            if len(names) >= limit:
                break
        return "; ".join(names)
    except Exception:
        return ""


def _user_click(step: Step) -> tuple[bool, str]:
    port, grounder = _user_window()
    if port is None:
        return False, "no browser window is open for the user"
    what = step.target or step.intent
    node = grounder.find_node(what)
    if node is None:
        here = _what_is_here(port)
        return False, (f"no control matching {what!r}"
                       + (f" — the page has: {here}" if here else ""))
    from actions.grounding.web.page import ref_of
    port.click(ref_of(node))
    return True, f"clicked {node.name!r} in the user's window"



#: Roles you can type into. `searchbox` first: typing a search query into a
#: newsletter signup is the failure this ordering exists to avoid.
_FIELD_ROLES = ("searchbox", "textbox")


def best_text_field(nodes):
    """The field a person would type in, or None.

    "Search for X" is two actions wearing one step — focus a field, then type
    — and a person does not need to be told which field. They look for the one
    you can type in. This is that.

    None is a real answer. Guessing at a control that is not editable is how
    `Page.fill: Element is not ...` happened on makerworld, and how text ends
    up somewhere nobody asked for.
    """
    best, best_rank = None, ()
    for n in nodes or ():
        role = str(getattr(n, "role", "") or "").lower()
        states = getattr(n, "states", frozenset()) or frozenset()
        if role not in _FIELD_ROLES:
            continue
        # Must be typable NOW. A disabled or off-screen field accepts nothing
        # and would report success for text that went nowhere.
        if "EDITABLE" not in states or "VISIBLE" not in states:
            continue
        name = str(getattr(n, "name", "") or "")
        rank = (
            _FIELD_ROLES.index(role) == 0,          # a searchbox wins
            "search" in name.lower(),               # then one that says so
            bool(name),                             # then a named one
            int(getattr(n, "width", 0) or 0),       # then the widest
        )
        if best is None or rank > best_rank:
            best, best_rank = n, rank
    return best


def _focus_and_type(port, grounder, text: str) -> tuple[bool, str]:
    """Put the cursor in the page's text field, then type. Exact, not blind."""
    try:
        where = port.type_into_focused(text)
        if where:
            return True, f"typed into the focused field ({where})"
    except Exception:
        pass
    try:
        from actions.grounding.web.page import nodes_from_records, ref_of
        field = best_text_field(nodes_from_records(port.collect()))
        if field is None:
            return False, ("no text field on this page to type into — the "
                           "page may not have loaded, or the field may be "
                           "behind a button that opens it")
        port.click(ref_of(field))
        typed = port.type_into_focused(text)
        if typed:
            return True, f"clicked {field.name or 'the search field'!r} and typed"
        port.fill(ref_of(field), text)
        return True, f"typed into {field.name or 'the search field'!r}"
    except Exception as e:
        return False, f"could not type into the page's text field: {e}"


def _web_type(step: Step) -> tuple[bool, str]:
    """Type in the eagle's OWN browser, into the focused field when the step
    names no control. Same reasoning as `_user_type`, other browser."""
    if not step.text:
        return False, "nothing to type"
    if not step.target:
        try:
            from actions.grounding.web.browser import default_browser
            page = default_browser().page()
            if page is not None:
                ok, detail = _focus_and_type(page, None, step.text)
                if ok:
                    return ok, detail
        except Exception as e:
            return False, f"could not type into the page's text field: {e}"
    return _web("type")(step)


def _user_type(step: Step) -> tuple[bool, str]:
    port, grounder = _user_window()
    if port is None:
        return False, "no browser window is open for the user"
    if not step.text:
        return False, "nothing to type"

    # "Type motherboard" names no control, because a person does not name one
    # — they have just clicked the field. With no explicit target, type into
    # whatever the PAGE reports as focused: exact, because the browser knows,
    # and it is the field the previous step clicked. Without this the step
    # fell through to the OS keyboard, which is how "motherboard" ended up in
    # the user's terminal.
    if not step.target:
        ok, detail = _focus_and_type(port, grounder, step.text)
        if ok:
            return ok, detail

    what = step.target or step.intent
    node = grounder.find_node(what)
    if node is None:
        return False, f"no field matching {what!r} in the user's window"
    from actions.grounding.web.page import ref_of
    port.fill(ref_of(node), step.text)
    return True, f"typed into {node.name!r} in the user's window"


def _same_page(a: str, b: str) -> bool:
    """Close enough that navigating again would only cost a reload."""
    def norm(u):
        u = (u or "").strip().lower().rstrip("/")
        for pfx in ("https://", "http://", "www."):
            if u.startswith(pfx):
                u = u[len(pfx):]
        return u
    x, y = norm(a), norm(b)
    return bool(x) and bool(y) and (x == y or x.startswith(y) or y.startswith(x))


def _user_open(step: Step) -> tuple[bool, str]:
    port, _ = _user_window(create=True)   # the one rung that may open one
    why = _needs_url(step)
    if why:
        return False, why
    if port is None:
        return False, "no browser window is open for the user"
    # Already there is DONE, not a reason to load it again. Re-navigating
    # costs a page load, the scroll position, and anything already typed.
    here = port.url()
    if _same_page(here, step.url):
        return True, f"already on {here}"
    port.goto(step.url)
    return True, f"navigated the user's window to {step.url}"


def _user_look(step: Step) -> tuple[bool, str]:
    port, _ = _user_window()
    if port is None:
        return False, "no browser window is open for the user"
    from actions.grounding.web.page import nodes_from_records
    nodes = nodes_from_records(port.collect())
    return bool(nodes), f"{len(nodes)} controls in the user's window"


# ── the disk: getting a file, reading it, filling one, sending it back ──────
#
# These four runners are what makes "download a form, fill it in with details
# from a file on my Desktop, upload and submit it" a workflow the eagle can
# actually run rather than four steps that each report success while doing
# nothing. `step.data` — the SAME dict as `Mission.facts`, wired in by
# `mission_ladder.attempt` right before any rung runs — is how a later runner
# finds what an earlier one produced: the downloaded file's path, the values
# read off the disk, the filled copy's path. None of it is re-derived by
# guessing at filenames from the step's own wording where a fact already
# answers the question.


def _web_download(step: Step) -> tuple[bool, str]:
    """Click a download control and keep the path it produced.

    Distinct from `web_click` for the reason `web_agency._download` documents:
    a click succeeds when the page reacts, a download succeeds only when a
    file is on disk. The path goes onto the blackboard as `downloaded_file` —
    the only way "fill the downloaded form" can find it without guessing.
    """
    from actions.web_agency import web_agency
    ok, detail, data = _verdict_data(web_agency(parameters={
        "action": "download", "description": step.target or step.intent}))
    if ok and data.get("path"):
        step.data["downloaded_file"] = data["path"]
        step.data["downloaded_name"] = data.get("name", "")
    return ok, detail


#: A location word in the step's own phrasing, and the folder it means.
#: Checked in this order so the first one named wins rather than the last.
_FOLDER_WORDS = (("desktop", "Desktop"), ("downloads", "Downloads"),
                 ("download", "Downloads"), ("documents", "Documents"))

#: A bare filename with a document-shaped extension, as a person would type
#: it in a sentence — never a full path, which nobody speaks aloud. `\S+`
#: rather than a wider class: an earlier version allowed spaces inside the
#: match, so it greedily swallowed "read the file my-details.txt" as ONE
#: filename (there is only one '.txt' in the sentence, and every character
#: before it — including "read the file " — is a legal char in that class).
#: A filename has no un-escaped spaces, so stopping at whitespace is correct,
#: not merely convenient.
_FILENAME = re.compile(
    r"\S+\.(?:txt|pdf|csv|json|md|docx?|rtf)\b", re.I)


def _named_file(intent: str) -> Path | None:
    """A file the step's OWN wording points to, in whichever folder it names.

    None when the step names no filename at all — the honest answer for
    something like "fill it in", which means whatever was already found, not
    a fresh guess.
    """
    m = _FILENAME.search(intent)
    if not m:
        return None
    folder = Path.home()
    for word, sub in _FOLDER_WORDS:
        if word in intent:
            folder = Path.home() / sub
            break
    return folder / m.group(0)


#: "Label: value" — the shape both a person's own notes and a form template
#: use. Loose on the label on purpose: the vocabulary belongs to whatever file
#: or site produced it, never predicted in advance.
_FIELD_LINE = re.compile(r"^[ \t]*([A-Za-z][A-Za-z /]{0,40}?):[ \t]*(.*)$")


def _parse_fields(text: str) -> dict[str, str]:
    """Every "Label: value" line in `text`, keyed by the label lowercased.

    A blank value is skipped — that is the TEMPLATE's own empty line
    ("FULL NAME:"), not data, and letting it in would erase a real value
    read earlier with nothing.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _FIELD_LINE.match(line)
        if not m:
            continue
        value = m.group(2).strip()
        if value:
            out[m.group(1).strip().lower()] = value
    return out


def _file_read(step: Step) -> tuple[bool, str]:
    """Read a file off the disk and harvest any "Label: value" lines from it.

    Never touches the browser. Before this ladder existed, "Read the file on
    my Desktop" fell to `web_look`, which read the current PAGE instead and
    reported success having never touched the disk — see this module's and
    `mission_ladder`'s docstrings for the failure this replaces.
    """
    path = _named_file(step.intent.lower()) if step.intent else None
    if path is None:
        # No filename in this step's own words — "read it" after a download
        # means THAT file, not a fresh guess.
        prior = step.data.get("downloaded_file")
        path = Path(prior) if prior else None
    if path is None:
        return False, "could not tell which file to read — no filename in the step"
    if not path.is_file():
        return False, f"no file at {path}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"could not read {path}: {e}"
    fields = _parse_fields(text)
    step.data.setdefault("fields", {}).update(fields)
    step.data["last_read_path"] = str(path)
    return True, (f"read {len(text)} characters from {path}"
                  + (f", {len(fields)} labelled values" if fields else ""))


def _fill_template(text: str, fields: dict[str, str]) -> tuple[str, list[str]]:
    """Every blank "LABEL:" line in `text`, filled from `fields`.

    Returns the filled text and any labels with no matching value — a mission
    must report a gap, never submit a form with one silently left blank.
    """
    out, missing = [], []
    for line in text.splitlines():
        m = _FIELD_LINE.match(line)
        if m and not m.group(2).strip():
            label = m.group(1).strip()
            value = fields.get(label.lower())
            if value:
                out.append(f"{label}: {value}")
                continue
            missing.append(label)
        out.append(line)
    return "\n".join(out) + "\n", missing


def _file_write(step: Step) -> tuple[bool, str]:
    """Fill the downloaded template with details read earlier, and save it.

    The template is whichever file a prior `download` step produced —
    `downloaded_file` on the blackboard — never re-guessed from THIS step's
    wording, because "fill the downloaded form" names no filename of its own.
    The values are whatever `file_read` harvested; without any, there is
    nothing to fill with, and that is reported rather than writing an empty
    copy that would later fail the upload silently.
    """
    fields = step.data.get("fields") or {}
    if not fields:
        return False, ("no details have been read yet — read the source file "
                       "before filling the form")
    prior = step.data.get("downloaded_file")
    template = Path(prior) if prior else _named_file((step.intent or "").lower())
    if template is None or not template.is_file():
        return False, "could not find the downloaded form to fill in"
    try:
        text = template.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return False, f"could not read {template}: {e}"
    filled, missing = _fill_template(text, fields)
    if missing:
        return False, f"no value found for: {', '.join(missing)}"
    dest = template.with_name(f"{template.stem}-filled{template.suffix}")
    try:
        dest.write_text(filled, encoding="utf-8")
    except Exception as e:
        return False, f"could not save the filled form: {e}"
    step.data["filled_file"] = str(dest)
    return True, f"filled {len(fields)} values and saved {dest}"


def _web_upload(step: Step) -> tuple[bool, str]:
    """Hand whatever file this mission produced to an upload control.

    Prefers the FILLED file over the raw download: uploading the blank
    template would pass a form with nothing in it, and this runner has no way
    to catch that once the upload itself succeeds.
    """
    path = step.data.get("filled_file") or step.data.get("downloaded_file")
    if not path:
        return False, "no file to upload — nothing downloaded or filled yet"
    from actions.web_agency import web_agency
    return _verdict(web_agency(parameters={
        "action": "upload", "description": step.target or step.intent,
        "path": path}))


def build_runners() -> dict[str, Callable[[Step], tuple[bool, str]]]:
    return {
        "web_open":     _web("open"),
        "browser_open": _browser_open,
        "user_open":     _user_open,
        "user_look":     _user_look,
        "web_look":     _web("look"),
        "screen_look":  _computer("screen_find"),
        "user_click":    _user_click,
        "web_click":    _web("click"),
        "screen_click": _computer("screen_click"),
        "vision_click": _computer("screen_click"),   # resolver falls to vision
        "user_type":     _user_type,
        "web_type":     _web_type,
        "screen_type":  _runners_screen_type,
        "press_keys":   _press_keys,
        "web_download": _web_download,
        "file_read":    _file_read,
        "file_write":   _file_write,
        "web_upload":   _web_upload,
    }
