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


def _user_click(step: Step) -> tuple[bool, str]:
    port, grounder = _user_window()
    if port is None:
        return False, "no browser window is open for the user"
    what = step.target or step.intent
    node = grounder.find_node(what)
    if node is None:
        return False, f"no control matching {what!r} in the user's window"
    from actions.grounding.web.page import ref_of
    port.click(ref_of(node))
    return True, f"clicked {node.name!r} in the user's window"


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
                where = page.type_into_focused(step.text)
                if where:
                    return True, f"typed into the focused field ({where})"
        except Exception as e:
            return False, f"could not type into the focused field: {e}"
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
        try:
            where = port.type_into_focused(step.text)
            if where:
                return True, f"typed into the focused field ({where})"
        except Exception as e:
            return False, f"could not type into the focused field: {e}"

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
    }
