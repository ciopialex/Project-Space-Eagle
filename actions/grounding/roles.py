"""One vocabulary for what a control *is*, across three operating systems.

Each platform names the same button differently:

    Linux (AT-SPI)   "push button"
    Windows (UIA)    "Button"
    macOS (AX)       "AXButton"

`match_score` in base.py understands the AT-SPI names, because that is where
the grounding work started. Rather than teach it three vocabularies, every
platform normalizes into that one. A person says "the Save button" and means
the same thing on every machine; the eagle should too.

Unknown roles pass through lowercased rather than being dropped — an
unrecognised control still matches on its name, it just gets no role bonus.
"""
from __future__ import annotations

LINUX = "linux"
WINDOWS = "windows"
MACOS = "macos"

#: Windows UI Automation control types -> AT-SPI canonical names.
_WINDOWS: dict[str, str] = {
    "button":        "push button",
    "splitbutton":   "push button",
    "hyperlink":     "link",
    "edit":          "text",
    "document":      "text",
    "checkbox":      "check box",
    "radiobutton":   "radio button",
    "menuitem":      "menu item",
    "menu":          "menu",
    "menubar":       "menu bar",
    "tabitem":       "page tab",
    "tab":           "page tab list",
    "listitem":      "list item",
    "list":          "list",
    "image":         "image",
    "text":          "label",
    "combobox":      "combo box",
    "window":        "frame",
    "pane":          "panel",
    "toolbar":       "tool bar",
    "treeitem":      "tree item",
    "table":         "table",
    "slider":        "slider",
    "progressbar":   "progress bar",
    "spinner":       "spin button",
    "titlebar":      "title bar",
    "statusbar":     "status bar",
    "group":         "panel",
    "custom":        "panel",
}

#: macOS Accessibility roles -> AT-SPI canonical names. The "AX" prefix is
#: stripped before lookup, so both "AXButton" and "Button" resolve.
_MACOS: dict[str, str] = {
    "button":            "push button",
    "popupbutton":       "combo box",
    "menubutton":        "push button",
    "link":              "link",
    "textfield":         "text",
    "textarea":          "text",
    "securetextfield":   "password text",
    "checkbox":          "check box",
    "radiobutton":       "radio button",
    "menuitem":          "menu item",
    "menu":              "menu",
    "menubar":           "menu bar",
    "menubaritem":       "menu item",
    "tabgroup":          "page tab list",
    "radiogroup":        "panel",
    "row":               "table row",
    "cell":              "table cell",
    "list":              "list",
    "image":             "image",
    "statictext":        "label",
    "window":            "frame",
    "group":             "panel",
    "toolbar":           "tool bar",
    "scrollarea":        "scroll pane",
    "slider":            "slider",
    "progressindicator": "progress bar",
    "outline":           "tree",
    "table":             "table",
    "sheet":             "dialog",
}

_TABLES = {WINDOWS: _WINDOWS, MACOS: _MACOS}


def normalize(role: str, platform: str = LINUX) -> str:
    """Canonical role name for `role` as reported by `platform`.

    Linux roles are already canonical. Unknown roles are lowercased and
    returned unchanged, so an element the table doesn't know can still be
    matched on its name.
    """
    clean = str(role or "").strip().lower()
    if not clean:
        return ""
    table = _TABLES.get(str(platform or "").lower())
    if table is None:
        return clean
    if platform == MACOS and clean.startswith("ax"):
        clean = clean[2:]
    key = clean.replace(" ", "").replace("_", "")
    return table.get(key, clean)


def best_match(nodes, description: str, threshold: float = 0.5,
               platform: str = LINUX):
    """Highest-scoring node for `description`, or None.

    Shared by every structural grounder so the matching rules — role
    normalization, bounds sanity, score threshold — cannot drift apart
    between platforms.
    """
    from actions.grounding.base import match_score

    best = None
    best_score = threshold
    for node in nodes:
        if not _has_sane_bounds(node):
            continue
        score = match_score(description, node.name,
                            normalize(node.role, platform))
        if score >= threshold and score > best_score - 1e-9:
            if best is None or score > best_score:
                best_score, best = score, node
    return best


# No real display is this large. AT-SPI reports INT_MIN for unmapped
# components and UIA reports similar sentinels; those coordinates must never
# reach the mouse.
_COORD_SANITY = 50_000


def _has_sane_bounds(node) -> bool:
    """Reject controls the platform can't actually place on screen."""
    try:
        if node.width <= 0 or node.height <= 0:
            return False
        if abs(node.left) > _COORD_SANITY or abs(node.top) > _COORD_SANITY:
            return False
        if node.left + node.width < 0 or node.top + node.height < 0:
            return False
    except Exception:
        return False
    return True
