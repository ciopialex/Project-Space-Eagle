

# ── A click that lands on a child still reaches the parent ─────────────────
# Live on eu.store.bambulab.com: the eagle resolved "Bambu Lab P2S 3D Printer"
# correctly and then could not click it for 5009ms across 76 tries, reporting
# "covered by something else". Nothing was covering it. The topmost element at
# the link's centre was the product IMAGE INSIDE that link — its own child,
# which the collector also stamps as a control.
#
# `receives_events` compared (name, role, bounds) and saw a mismatch, so a
# perfectly clickable product card was unreachable on every shop page built
# this way, which is most of them.

class _El:
    def __init__(self, name, role, x, y, w, h):
        self.name, self.role = name, role
        self.width, self.height = w, h
        self._x, self._y, self._w, self._h = x, y, w, h

    @property
    def bounds(self): return (self._x, self._y, self._w, self._h)
    @property
    def x(self): return self._x + self._w // 2
    @property
    def y(self): return self._y + self._h // 2


def test_a_hit_on_the_elements_own_child_counts_as_reaching_it():
    from actions.grounding.actionability import receives_events
    link = _El("Bambu Lab P2S 3D Printer", "link", 419, 299, 301, 301)
    image = _El("Bambu Lab P2S 3D Printer", "img", 419, 299, 301, 240)  # inside
    assert receives_events(link, lambda x, y: image) is True


def test_a_modal_over_the_target_still_blocks_it():
    """The case the check exists for must keep working: something genuinely
    on top, not contained by the target."""
    from actions.grounding.actionability import receives_events
    button = _El("Continue", "button", 400, 500, 120, 40)
    modal = _El("Accept cookies", "dialog", 0, 0, 1440, 900)   # covers everything
    assert receives_events(button, lambda x, y: modal) is False


def test_a_neighbour_that_is_not_inside_the_target_still_blocks_it():
    from actions.grounding.actionability import receives_events
    button = _El("Continue", "button", 400, 500, 120, 40)
    other = _El("Chat with us", "button", 380, 480, 200, 90)   # overlaps, not inside
    assert receives_events(button, lambda x, y: other) is False


def test_the_element_itself_still_passes():
    from actions.grounding.actionability import receives_events
    button = _El("Continue", "button", 400, 500, 120, 40)
    assert receives_events(button, lambda x, y: button) is True


def test_nothing_at_the_point_is_still_a_failure():
    from actions.grounding.actionability import receives_events
    button = _El("Continue", "button", 400, 500, 120, 40)
    assert receives_events(button, lambda x, y: None) is False
