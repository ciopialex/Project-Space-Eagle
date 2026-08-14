"""Act on the browser the eagle opened FOR THE USER — one implementation,
reached by two callers: a mission step, and a single ad-hoc voice command.

Before this file existed, everything here lived only inside
core/mission_runners.py's _user_click/_user_type/_user_open/_user_look,
private to that module and only reachable from inside a running mission.
A live babysitting session (2026-08-13/14) showed the cost of that: asked
to "click the search bar" as a single command, the model had no DOM-exact
option and fell to computer_control's pixel/AT-SPI path instead — a
completely different, less reliable implementation with its own bugs
(AT-SPI was structurally broken in that environment), unrelated to the
page the model was actually looking at.

`actions/browser_control.py` now exposes these directly as its own
`click`/`type`/`look` actions; `core/mission_runners.py`'s `_user_*`
functions became thin adapters that call the same code. There is exactly
one implementation of "find a control on the visible page and act on it"
from here on.
"""
from __future__ import annotations

from core.tool_result import ToolResult


def _user_window(create: bool = False):
    from core.session_port import user_window
    return user_window(create=create)


def what_is_here(port, limit: int = 12) -> str:
    """The names actually on the page, for a failure that could not find its
    own — "no control matches" alone makes the model guess again."""
    try:
        from actions.grounding.web.page import nodes_from_records
        from actions.web_agency import _spread
        names = []
        # Spread across the page, not the first N — taking the front returned
        # "Home; All Models; Following; MakerLab" on a page of search
        # results (the sidebar, in document order), the same positional
        # bias hidden content everywhere else in this codebase.
        for n in _spread(nodes_from_records(port.collect()), budget=60, run=6):
            nm = str(getattr(n, "name", "") or "").strip()
            if nm and nm not in names:
                names.append(nm)
            if len(names) >= limit:
                break
        return "; ".join(names)
    except Exception:
        return ""


#: Roles you can type into. `searchbox` first: typing a search query into a
#: newsletter signup is the failure this ordering exists to avoid.
_FIELD_ROLES = ("searchbox", "textbox")


def best_text_field(nodes):
    """The field a person would type in, or None.

    "Search for X" is two actions wearing one step — focus a field, then
    type — and a person does not need to be told which field; they look
    for the one they can type in. None is a real answer: guessing at a
    control that is not editable is how `Page.fill: Element is not ...`
    happened live, and how text ends up somewhere nobody asked for.
    """
    best, best_rank = None, ()
    for n in nodes or ():
        role = str(getattr(n, "role", "") or "").lower()
        states = getattr(n, "states", frozenset()) or frozenset()
        if role not in _FIELD_ROLES:
            continue
        if "EDITABLE" not in states or "VISIBLE" not in states:
            continue
        name = str(getattr(n, "name", "") or "")
        rank = (
            _FIELD_ROLES.index(role) == 0,
            "search" in name.lower(),
            bool(name),
            int(getattr(n, "width", 0) or 0),
        )
        if best is None or rank > best_rank:
            best, best_rank = n, rank
    return best


def focus_and_type(port, grounder, text: str) -> tuple[bool, str]:
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


def user_click(description: str) -> ToolResult:
    port, grounder = _user_window()
    if port is None:
        return ToolResult.failure(
            "no browser window is open for the user",
            guidance="Call browser_control action='go_to' with a url first.")
    node = grounder.find_node(description)
    if node is None:
        here = what_is_here(port)
        return ToolResult.failure(
            f"no control matching {description!r}"
            + (f" — the page has: {here}" if here else ""),
            guidance="Call browser_control action='look' and use one of "
                     "the names it returns.")
    from actions.grounding.web.page import ref_of
    port.click(ref_of(node))
    return ToolResult.success(f"clicked {node.name!r} in the user's window")


def user_type(description: str | None, text: str) -> ToolResult:
    port, grounder = _user_window()
    if port is None:
        return ToolResult.failure(
            "no browser window is open for the user",
            guidance="Call browser_control action='go_to' with a url first.")
    if not text:
        return ToolResult.failure("nothing to type",
                                  guidance="Pass text='...'.")

    # "Type motherboard" names no control, because a person does not name
    # one — they just clicked the field. With no explicit description, type
    # into whatever the PAGE reports as focused: exact, because the browser
    # knows, and it is the field a previous click landed on.
    if not description:
        ok, detail = focus_and_type(port, grounder, text)
        if ok:
            return ToolResult.success(detail)

    node = grounder.find_node(description or "")
    if node is None:
        return ToolResult.failure(
            f"no field matching {description!r} in the user's window",
            guidance="Call browser_control action='look' and use one of "
                     "the names it returns.")
    from actions.grounding.web.page import ref_of
    port.fill(ref_of(node), text)
    return ToolResult.success(f"typed into {node.name!r} in the user's window")


def user_open(url: str) -> ToolResult:
    port, _ = _user_window(create=True)   # the one call that may open one
    if not url:
        return ToolResult.failure("no url to open",
                                  guidance="Pass url='https://...'.")
    if port is None:
        return ToolResult.failure(
            "no browser window is open for the user",
            guidance="Retrying will not help by itself — the launch "
                     "budget may be spent, or the session could not start.")
    here = port.url()
    if _same_page(here, url):
        return ToolResult.success(f"already on {here}")
    port.goto(url)
    return ToolResult.success(f"navigated the user's window to {url}")


def user_look(limit: int = 40) -> ToolResult:
    port, _ = _user_window()
    if port is None:
        return ToolResult.failure(
            "no browser window is open for the user",
            guidance="Call browser_control action='go_to' with a url first.")
    from actions.grounding.web.page import nodes_from_records
    nodes = nodes_from_records(port.collect())

    # web_agency already checks this on its own hidden browser; this DOM
    # path never did. Live, 2026-08-14: a Cloudflare "Just a moment..."
    # interstitial has a handful of real controls (a heading, a Ray ID
    # footer link), so it read as an ordinary thin page instead of a wall —
    # every click/type attempt then failed against fields that were never
    # really on the page, with no indication why. Unlike web_agency's
    # hidden browser, this IS the window the user is looking at, so the
    # actionable answer is simpler: ask them to clear it themselves.
    from actions.grounding.web.handoff import bot_wall_reason
    blocked = bot_wall_reason(nodes, port.url())
    if blocked:
        return ToolResult.failure(
            f"the user's browser window is showing a human-verification "
            f"check, not the real page — {blocked}",
            guidance="This is the window the user can see. Ask them to "
                     "clear the check themselves (click through it), then "
                     "try again — do not keep retrying blind.")

    names = what_is_here(port, limit=limit)
    if not nodes:
        # A window with zero controls is not a look that "worked" — it is
        # indistinguishable from a blank/failed-to-load page. `user_look` is
        # a rung on mission_ladder's "read" ladder (web_look, user_look,
        # screen_look); reporting ok=True here would stop the ladder before
        # it ever reaches screen_look, the visual fallback that exists
        # exactly for this case. Matches the pre-refactor `ok=bool(nodes)`.
        return ToolResult.failure(
            "0 controls in the user's window",
            guidance="The page may not have loaded, or may be blank — try "
                     "browser_control action='screenshot' to see it.")
    return ToolResult.success(
        f"{len(nodes)} controls in the user's window"
        + (f": {names}" if names else ""))
