"""The rungs, bound to real tools. Each runner is (step) -> (ok, detail), and ok
means the world changed. A tool result with no `ok` counts as a failure.
"""
from __future__ import annotations

import re

from pathlib import Path
from typing import Callable

from core.mission import Step
from core.tool_result import ToolResult, normalize
from actions.grounding.web.user_actions import (
    _same_page, best_text_field, focus_and_type, user_click as _ua_click,
    user_look as _ua_look, user_open as _ua_open, user_type as _ua_type,
    what_is_here,
)


def _verdict(raw) -> tuple[bool, str]:
    """A tool's return as (ok, detail). No `ok` on the wire is a failure: a wrong
    reading would advance a mission past a step that never happened.
    """
    tr: ToolResult = normalize(raw)
    resp = tr.to_response()
    if "ok" not in resp:
        return False, f"tool gave no verdict: {tr.message[:120]}"
    return bool(resp["ok"]), tr.message[:200]


def _verdict_data(raw) -> tuple[bool, str, dict]:
    """_verdict plus the tool's .data (a download's path, an upload's confirmation),
    which never reaches the model.
    """
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
            # Not in the model's tool declaration, or any click could pass
            # confirmed=True. Only this runner sets it, for a mission the user already
            # approved (Mission.authorized).
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
    """The name of the focused text field, or None. None means unknown, never
    permission. A module-local name so tests can patch it; the check lives in
    actions/grounding/focus.py.
    """
    from actions.grounding.focus import focused_editable_name
    return focused_editable_name()


def _refuse_blind(step: Step) -> tuple[bool, str]:
    return False, (
        "refusing to type: nothing identifiable has keyboard focus, so the "
        "text could land anywhere — a terminal, a chat, someone's half-filled "
        "form. Click the field first, then type.")


def _runners_screen_type(step: Step) -> tuple[bool, str]:
    """Type through the OS keyboard, only once focus is known."""
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
    """The last resort: press the keys one by one."""
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
    """Why an open step cannot run, or "": a step with no URL is a planning defect,
    not a browser failure.
    """
    return "" if step.url else (
        f"the step {step.intent!r} names no address to open — it needs a url")


def _browser_open(step: Step) -> tuple[bool, str]:
    """Open the user's browser, which web_agency cannot see into. Last rung: after
    it, the next steps are screen-and-vision only.
    """
    from actions.browser_control import browser_control
    why = _needs_url(step)
    if why:
        return False, why
    # A window already showing this page is not reopened.
    port, _ = _user_window()
    if port is not None and _same_page(port.url(), step.url):
        return True, f"already on {step.url}"
    return _verdict(browser_control(
        parameters={"action": "go_to", "url": step.url}))


def _user_window(create: bool = False):
    """The browser the eagle opened for the user, wrapped in the same grounding:
    browser_control already holds its Playwright Page.
    """
    from core.session_port import user_window
    return user_window(create=create)


def _user_click(step: Step) -> tuple[bool, str]:
    r = _ua_click(step.target or step.intent)
    return r.ok, r.message


def _web_type(step: Step) -> tuple[bool, str]:
    """Type in the eagle's own browser, into the focused field when the step names no
    control.
    """
    if not step.text:
        return False, "nothing to type"
    if not step.target:
        try:
            from actions.grounding.web.browser import default_browser
            page = default_browser().page()
            if page is not None:
                ok, detail = focus_and_type(page, None, step.text)
                if ok:
                    return ok, detail
        except Exception as e:
            return False, f"could not type into the page's text field: {e}"
    return _web("type")(step)


def _user_type(step: Step) -> tuple[bool, str]:
    # A target is searched for directly. With no target, user_type(None, ...) first
    # tries the focused field; only if that fails is the intent text searched for.
    if step.target:
        r = _ua_type(step.target, step.text)
        return r.ok, r.message
    r = _ua_type(None, step.text)
    if r.ok:
        return r.ok, r.message
    r = _ua_type(step.intent or None, step.text)
    return r.ok, r.message


def _user_open(step: Step) -> tuple[bool, str]:
    why = _needs_url(step)
    if why:
        return False, why
    r = _ua_open(step.url)
    return r.ok, r.message


def _user_look(step: Step) -> tuple[bool, str]:
    r = _ua_look()
    return r.ok, r.message


# The disk: getting a file, reading it, filling one, sending it back. step.data is
# Mission.facts, so a later runner finds what an earlier one produced (the downloaded
# path, the values read, the filled copy) instead of guessing filenames.


def _web_download(step: Step) -> tuple[bool, str]:
    """Click a download control and keep the path as `downloaded_file`. Success means
    a file on disk.
    """
    from actions.web_agency import web_agency
    ok, detail, data = _verdict_data(web_agency(parameters={
        "action": "download", "description": step.target or step.intent}))
    if ok and data.get("path"):
        step.data["downloaded_file"] = data["path"]
        step.data["downloaded_name"] = data.get("name", "")
    return ok, detail


# A location word in the step's phrasing and the folder it means; first named wins.
_FOLDER_WORDS = (("desktop", "Desktop"), ("downloads", "Downloads"),
                 ("download", "Downloads"), ("documents", "Documents"))

# A bare filename with a document extension, as spoken: no spaces, never a full path.
_FILENAME = re.compile(
    r"\S+\.(?:txt|pdf|csv|json|md|docx?|rtf)\b", re.I)


def _named_file(intent: str) -> Path | None:
    """A file the step's own wording names, in the folder it names, or None."""
    m = _FILENAME.search(intent)
    if not m:
        return None
    folder = Path.home()
    for word, sub in _FOLDER_WORDS:
        if word in intent:
            folder = Path.home() / sub
            break
    return folder / m.group(0)


# "Label: value", the shape of both a person's notes and a form template.
_FIELD_LINE = re.compile(r"^[ \t]*([A-Za-z][A-Za-z /]{0,40}?):[ \t]*(.*)$")


def _parse_fields(text: str) -> dict[str, str]:
    """Every "Label: value" line in `text`, keyed by the lowercased label. Blank
    values (a template's empty line) are skipped.
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
    """Read a file off the disk and harvest its "Label: value" lines. Never touches
    the browser.
    """
    path = _named_file(step.intent.lower()) if step.intent else None
    if path is None:
        # No filename in the step: use the file already found.
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
    """Fill every blank "LABEL:" line from `fields`. Returns the text and any labels
    left unfilled, which a mission must report.
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
    """Fill the downloaded template (`downloaded_file`) with the values read earlier,
    and save it. No values: report it rather than write an empty copy.
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
    """Upload what this mission produced, preferring the filled file over the raw
    download.
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
