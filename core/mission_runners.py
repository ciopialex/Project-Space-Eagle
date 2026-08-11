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
    return _verdict(browser_control(
        parameters={"action": "go_to", "url": step.url}))




def _attached_grounder():
    """The user-facing Chrome, wrapped in the same grounding the eagle uses on
    its own browser.

    This rung is the one that was missing. The chain that stopped a real
    mission: makerworld bot-walls the eagle's headless browser, so `web_open`
    correctly fails and `browser_open` opens the page in the user's real
    Chrome — and every step after that had no way to touch it. `web_click`
    looks in the wrong browser, `screen_click` is blind because Chrome
    publishes nothing to the accessibility bus, and vision guesses.

    A Playwright page is a Playwright page. Wrapping the attached one in
    PagePort gives WebGrounder exactly what it already knows how to drive.
    """
    from actions.grounding.web.attach import attached_page
    from actions.grounding.web.browser import PagePort
    from actions.grounding.web.grounder import WebGrounder
    page = attached_page()
    if page is None:
        return None, None
    port = PagePort(page, call=lambda fn: fn())
    return port, WebGrounder(lambda: port)


def _cdp_click(step: Step) -> tuple[bool, str]:
    port, grounder = _attached_grounder()
    if port is None:
        return False, "no browser window is attached"
    what = step.target or step.intent
    node = grounder.find_node(what)
    if node is None:
        return False, f"no control matching {what!r} in the attached window"
    from actions.grounding.web.page import ref_of
    port.click(ref_of(node))
    return True, f"clicked {node.name!r} in the attached window"


def _cdp_type(step: Step) -> tuple[bool, str]:
    port, grounder = _attached_grounder()
    if port is None:
        return False, "no browser window is attached"
    if not step.text:
        return False, "nothing to type"
    what = step.target or step.intent
    node = grounder.find_node(what)
    if node is None:
        return False, f"no field matching {what!r} in the attached window"
    from actions.grounding.web.page import ref_of
    port.fill(ref_of(node), step.text)
    return True, f"typed into {node.name!r} in the attached window"


def _cdp_open(step: Step) -> tuple[bool, str]:
    port, _ = _attached_grounder()
    if port is None or not step.url:
        return False, "no attached window, or no url"
    port._page.goto(step.url, wait_until="domcontentloaded", timeout=30_000)
    return True, f"navigated the attached window to {step.url}"


def _cdp_look(step: Step) -> tuple[bool, str]:
    port, _ = _attached_grounder()
    if port is None:
        return False, "no browser window is attached"
    from actions.grounding.web.page import nodes_from_records
    nodes = nodes_from_records(port.collect())
    return bool(nodes), f"{len(nodes)} controls in the attached window"


def build_runners() -> dict[str, Callable[[Step], tuple[bool, str]]]:
    return {
        "web_open":     _web("open"),
        "browser_open": _browser_open,
        "cdp_open":     _cdp_open,
        "cdp_look":     _cdp_look,
        "web_look":     _web("look"),
        "screen_look":  _computer("screen_find"),
        "cdp_click":    _cdp_click,
        "web_click":    _web("click"),
        "screen_click": _computer("screen_click"),
        "vision_click": _computer("screen_click"),   # resolver falls to vision
        "cdp_type":     _cdp_type,
        "web_type":     _web("type"),
        "screen_type":  _computer("type"),
        "press_keys":   _press_keys,
    }
