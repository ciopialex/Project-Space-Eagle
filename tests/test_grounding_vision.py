from PIL import Image

from actions.grounding.vision import VisionGrounder, downscale


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, text):
        self._text = text
        self.calls = []

    def generate_content(self, model=None, contents=None):
        self.calls.append((model, contents))
        return FakeResponse(self._text)


class FakeClient:
    def __init__(self, text):
        self.models = FakeModels(text)


def _screen(w=1920, h=1080):
    return Image.new("RGB", (w, h), "black")


def test_downscale_shrinks_and_reports_scale():
    img, scale = downscale(_screen(1920, 1080), 1280)
    assert max(img.size) == 1280
    assert scale == 1920 / 1280


def test_downscale_leaves_small_images_alone():
    img, scale = downscale(_screen(800, 600), 1280)
    assert img.size == (800, 600)
    assert scale == 1.0


def test_coordinates_are_rescaled_to_full_screen():
    # model sees a 1280-wide image and reports (640, 360) -> centre of screen
    client = FakeClient("640,360")
    g = VisionGrounder(client_fn=lambda: client,
                       grab_fn=lambda: _screen(1920, 1080))
    el = g.find("the Save button")
    assert el is not None
    assert el.center == (960, 540)
    assert el.source == "vision"


def test_vision_elements_carry_no_states():
    """A picture cannot tell you whether a button is disabled."""
    g = VisionGrounder(client_fn=lambda: FakeClient("100,100"),
                       grab_fn=lambda: _screen())
    el = g.find("the Save button")
    assert el.states == frozenset()


def test_not_found_returns_none():
    g = VisionGrounder(client_fn=lambda: FakeClient("NOT_FOUND"),
                       grab_fn=lambda: _screen())
    assert g.find("the Save button") is None


def test_garbage_response_returns_none():
    g = VisionGrounder(client_fn=lambda: FakeClient("I'm not sure, sorry!"),
                       grab_fn=lambda: _screen())
    assert g.find("the Save button") is None


def test_never_raises_when_client_explodes():
    def boom():
        raise RuntimeError("no api key")
    g = VisionGrounder(client_fn=boom, grab_fn=lambda: _screen())
    assert g.find("the Save button") is None
    assert g.available() is False


def test_grounder_has_name():
    assert VisionGrounder(client_fn=lambda: FakeClient("0,0"),
                          grab_fn=lambda: _screen()).name == "vision"
