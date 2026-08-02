"""Read the accessibility tree instead of guessing at pixels.

AT-SPI publishes every widget's name, role, state and exact screen bounds over
D-Bus. Reading it is local, takes milliseconds, and returns coordinates that are
correct rather than approximately correct. This is what a human's perception of
an interface actually corresponds to.
"""
from __future__ import annotations

import subprocess
from typing import Callable, Iterable, Iterator

from actions.grounding.base import Element, UINode, match_score

_MAX_NODES = 4000   # hard ceiling so a pathological tree can't hang the eagle
_MAX_DEPTH = 40

# No real display is this large. AT-SPI reports INT_MIN for unmapped
# components, and those coordinates must never reach the mouse.
_COORD_SANITY = 50_000

# AT-SPI states we care about, by Atspi.StateType member name.
_TRACKED_STATES = ("ENABLED", "SENSITIVE", "VISIBLE", "SHOWING",
                   "EDITABLE", "FOCUSED", "FOCUSABLE", "SELECTED", "CHECKED")


# The node shape is shared with the Windows and macOS grounders; this name
# is kept because AT-SPI is where structural grounding started.
AtspiNode = UINode


_BOOTSTRAP: dict | None = None


def _ensure_bindings_once() -> dict:
    """Make the system accessibility bindings reachable, at most once.

    Lazy and self-healing: a fresh `curl | bash` install has PyGObject on the
    machine but sealed outside the virtualenv, so without this the fast path
    would silently never engage and every user would quietly get the slow
    vision fallback.
    """
    global _BOOTSTRAP
    if _BOOTSTRAP is None:
        from actions.grounding.bootstrap import ensure_accessibility
        _BOOTSTRAP = ensure_accessibility()
    return _BOOTSTRAP


def atspi_enabled() -> bool:
    """Is the GNOME toolkit-accessibility bridge switched on?

    When this is false, GTK apps publish nothing and the tree looks empty —
    which is indistinguishable from 'no matches' unless we check.
    """
    try:
        out = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface",
             "toolkit-accessibility"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip().lower()
        return out == "true"
    except Exception:
        return False


def live_walker(scope: str = "active") -> Iterator[AtspiNode]:
    """Walk the real accessibility tree. Yields nothing on failure.

    scope="active" walks only the focused window — what a person is actually
    looking at. Measured on this machine: 53ms and 88 nodes, versus 967ms and
    3151 nodes for the whole desktop. It is also *more correct*: minimised and
    unmapped windows report their children at 0,0, so a desktop-wide walk
    invents plausible-looking elements that aren't on screen.

    scope="all" walks every window, and is the fallback when no window is
    active. Slow and blind beats fast and blind.
    """
    _ensure_bindings_once()
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except Exception:
        return

    def _extents(node):
        try:
            ext = Atspi.Component.get_extents(node, Atspi.CoordType.SCREEN)
            return int(ext.x), int(ext.y), int(ext.width), int(ext.height)
        except Exception:
            return 0, 0, 0, 0

    def _states(node) -> frozenset:
        try:
            state_set = node.get_state_set()
            return frozenset(
                s for s in _TRACKED_STATES
                if state_set.contains(getattr(Atspi.StateType, s))
            )
        except Exception:
            return frozenset()

    def _value(node) -> str:
        """Current text content, so the eagle can read a field back."""
        try:
            text = node.get_text_iface()
            if text is None:
                return ""
            # Static form: Atspi.Accessible.get_text is deprecated.
            count = Atspi.Text.get_character_count(text)
            return Atspi.Text.get_text(text, 0, count) or ""
        except Exception:
            return ""

    def _active_frame(desktop):
        """The window with keyboard focus — the one the user is looking at."""
        try:
            for i in range(desktop.get_child_count()):
                app = desktop.get_child_at_index(i)
                if app is None:
                    continue
                for j in range(app.get_child_count()):
                    frame = app.get_child_at_index(j)
                    if frame is None:
                        continue
                    if frame.get_state_set().contains(Atspi.StateType.ACTIVE):
                        return frame
        except Exception:
            return None
        return None

    count = 0
    try:
        desktop = Atspi.get_desktop(0)
    except Exception:
        return

    root = _active_frame(desktop) if scope == "active" else None
    if root is None:
        root = desktop

    stack = [(root, 0)]
    while stack and count < _MAX_NODES:
        node, depth = stack.pop()
        if depth > _MAX_DEPTH:
            continue
        try:
            n_children = node.get_child_count()
        except Exception:
            continue
        for i in range(n_children):
            try:
                child = node.get_child_at_index(i)
                if child is None:
                    continue
                stack.append((child, depth + 1))
                left, top, width, height = _extents(child)
                yield AtspiNode(
                    name=child.get_name() or "",
                    role=child.get_role_name() or "",
                    left=left, top=top, width=width, height=height,
                    states=_states(child), value=_value(child),
                )
                count += 1
            except Exception:
                continue


class AtspiGrounder:
    """Locate an element by matching the accessibility tree."""

    name = "atspi"
    cost = "fast"      # local D-Bus, milliseconds

    def __init__(self,
                 walker: Callable[[], Iterable[AtspiNode]] | None = None,
                 threshold: float = 0.5) -> None:
        self._walker = walker or live_walker
        self._threshold = threshold

    def available(self) -> bool:
        try:
            for _ in self._walker():
                return True
        except Exception:
            return False
        return False

    @staticmethod
    def _has_sane_bounds(node: AtspiNode) -> bool:
        """Reject components AT-SPI can't actually place on screen.

        Unmapped components report INT_MIN for position. Clicking that sends
        the pointer to (-2147483648, -2147483648), which is how you lose a
        mouse. Seen live on a real desktop, not hypothetical.
        """
        if node.width <= 0 or node.height <= 0:
            return False
        if abs(node.left) > _COORD_SANITY or abs(node.top) > _COORD_SANITY:
            return False
        if node.left + node.width < 0 or node.top + node.height < 0:
            return False
        return True

    def find(self, description: str) -> Element | None:
        best: tuple[float, AtspiNode] | None = None
        try:
            for node in self._walker():
                if not self._has_sane_bounds(node):
                    continue
                score = match_score(description, node.name, node.role)
                if score >= self._threshold and (best is None or score > best[0]):
                    best = (score, node)
        except Exception:
            return None

        if best is None:
            return None
        _, node = best
        return Element.from_bounds(node.name, node.role, node.left, node.top,
                                   node.width, node.height, "atspi",
                                   states=node.states, value=node.value)


_BRIDGE_HINT = (
    "Structural grounding is returning very little, so the eagle is falling "
    "back to slower vision lookups. Turning the accessibility bridge on "
    "usually fixes it:\n"
    "    gsettings set org.gnome.desktop.interface toolkit-accessibility true\n"
    "Some apps need a restart afterwards."
)


def bridge_status(enabled_fn: Callable[[], bool] | None = None,
                  walker: Callable[[], Iterable[AtspiNode]] | None = None,
                  min_nodes: int = 5) -> dict:
    """Report whether structural grounding is actually working.

    Deliberately read-only. Flipping a user's desktop settings without asking
    is not something a good employee does.

    Note: on this machine the tree is populated even with the bridge reported
    off, so the node count is the honest signal and the setting is only a
    likely remedy. Reporting "off, therefore broken" would have been wrong.
    """
    enabled_fn = enabled_fn or atspi_enabled
    walker = walker or live_walker

    try:
        enabled = bool(enabled_fn())
    except Exception:
        enabled = False

    count = 0
    try:
        for node in walker():
            if node.name and node.width > 0:
                count += 1
                if count >= 50:      # enough to prove the tree is live
                    break
    except Exception:
        count = 0

    return {"enabled": enabled, "nodes_visible": count,
            "working": count >= min_nodes,
            "hint": "" if count >= min_nodes else _BRIDGE_HINT}


def hit_test_at(x: int, y: int) -> Element | None:
    """What element is actually at this screen point?

    Playwright's "receives events" check needs this. Without a real
    implementation `receives_events` can never pass, so every click wait times
    out - which is exactly what happened the first time this ran live.
    """
    _ensure_bindings_once()
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except Exception:
        return None

    try:
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            for j in range(app.get_child_count()):
                frame = app.get_child_at_index(j)
                if frame is None:
                    continue
                if not frame.get_state_set().contains(Atspi.StateType.ACTIVE):
                    continue
                node = Atspi.Component.get_accessible_at_point(
                    frame, x, y, Atspi.CoordType.SCREEN)
                # Descend to the deepest component under the point.
                while node is not None:
                    deeper = Atspi.Component.get_accessible_at_point(
                        node, x, y, Atspi.CoordType.SCREEN)
                    if deeper is None or deeper == node:
                        break
                    node = deeper
                if node is None:
                    return None
                ext = Atspi.Component.get_extents(node, Atspi.CoordType.SCREEN)
                return Element.from_bounds(
                    node.get_name() or "", node.get_role_name() or "",
                    ext.x, ext.y, ext.width, ext.height, "atspi")
    except Exception:
        return None
    return None


def scroll_to_element(description: str, threshold: float = 0.5,
                      scope: str = "active") -> bool:
    """Scroll an element into view, the way a person scrolls to find something.

    A human doesn't give up because the button is below the fold. Uses
    Atspi.Component.scroll_to, which asks the application to bring the
    component on screen — no mouse-wheel guessing, no scrollbar arithmetic.

    Returns whether something was scrolled. Never raises.
    """
    _ensure_bindings_once()
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except Exception:
        return False

    try:
        desktop = Atspi.get_desktop(0)
    except Exception:
        return False

    def _frames():
        try:
            for i in range(desktop.get_child_count()):
                app = desktop.get_child_at_index(i)
                if app is None:
                    continue
                for j in range(app.get_child_count()):
                    frame = app.get_child_at_index(j)
                    if frame is None:
                        continue
                    if scope != "active":
                        yield frame
                    elif frame.get_state_set().contains(Atspi.StateType.ACTIVE):
                        yield frame
        except Exception:
            return

    best = None
    best_score = threshold
    for root in _frames():
        stack = [(root, 0)]
        seen = 0
        while stack and seen < _MAX_NODES:
            node, depth = stack.pop()
            if depth > _MAX_DEPTH:
                continue
            try:
                count = node.get_child_count()
            except Exception:
                continue
            for i in range(count):
                try:
                    child = node.get_child_at_index(i)
                    if child is None:
                        continue
                    stack.append((child, depth + 1))
                    seen += 1
                    score = match_score(description, child.get_name() or "",
                                        child.get_role_name() or "")
                    if score > best_score:
                        best_score, best = score, child
                except Exception:
                    continue

    if best is None:
        return False
    try:
        Atspi.Component.scroll_to(best, Atspi.ScrollType.ANYWHERE)
        return True
    except Exception:
        return False
