"""Act on the browser the eagle opened for the user. One implementation, reached by
browser_control's click/type/look actions and by mission runners.
"""
from __future__ import annotations

from core.tool_result import ToolResult


def _user_window(create: bool = False):
    from core.session_port import user_window
    return user_window(create=create)


def what_is_here(port, limit: int = 12) -> str:
    """The names actually on the page, for a failure that could not find its target."""
    try:
        from actions.grounding.web.page import nodes_from_records
        from actions.web_agency import _spread
        names = []
        # Spread across the page, not the first N (the sidebar).
        for n in _spread(nodes_from_records(port.collect()), budget=60, run=6):
            nm = str(getattr(n, "name", "") or "").strip()
            if nm and nm not in names:
                names.append(nm)
            if len(names) >= limit:
                break
        return "; ".join(names)
    except Exception:
        return ""


# Roles you can type into, searchbox first.
_FIELD_ROLES = ("searchbox", "textbox")


def best_text_field(nodes):
    """The field a person would type in, or None. None is a real answer: never guess
    at a non-editable control.
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
    """Focus the page's text field, then type. Exact, not blind."""
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

    # resolve_and_click retries among tied candidates until one produces a verified
    # outcome, falling back to vision. No consent gate here: gate_fn is a deliberate no-
    # op.
    node, outcome = grounder.resolve_and_click(
        description, page=port, click_fn=port.click,
        gate_fn=lambda node: None)

    # `node`, not `outcome`, says whether anything was clicked: a ticked checkbox
    # produces no page-level signal.
    if node is None:
        # Nothing was clicked at all.
        here = what_is_here(port)
        return ToolResult.failure(
            f"no control matching {description!r}"
            + (f" — the page has: {here}" if here else ""),
            guidance="Call browser_control action='look' and use one of "
                     "the names it returns.")

    # Vision clicked a coordinate: do not claim a name the page never gave.
    what = ("the matched element" if getattr(node, "from_vision", False)
            else f"{node.name!r}")

    if outcome:
        return ToolResult.success(f"clicked {what} in the user's window")
    # Delivered, unverified, and said that way round.
    return ToolResult.success(
        f"clicked {what} in the user's window. Nothing on the page changed "
        "— call browser_control action='look' if you expected it to.")


def user_type(description: str | None, text: str) -> ToolResult:
    port, grounder = _user_window()
    if port is None:
        return ToolResult.failure(
            "no browser window is open for the user",
            guidance="Call browser_control action='go_to' with a url first.")
    if not text:
        return ToolResult.failure("nothing to type",
                                  guidance="Pass text='...'.")

    # No named control: type into whatever the page reports as focused.
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
    port, _ = _user_window(create=True)  # The one call that may open a window.
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

    # This is the window the user is looking at: a bot wall is theirs to clear.
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
        # Zero controls is not a working look; ok=False lets the read ladder reach
        # screen_look.
        return ToolResult.failure(
            "0 controls in the user's window",
            guidance="The page may not have loaded, or may be blank — try "
                     "browser_control action='screenshot' to see it.")
    return ToolResult.success(
        f"{len(nodes)} controls in the user's window"
        + (f": {names}" if names else ""))
