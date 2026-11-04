"""On Wayland the screen comes from the desktop's screenshot portal, not from X.

Measured on Ubuntu 26.04 (GNOME, Wayland): mss returned a frame with no lit pixel
at all, so "What's on my screen?" showed the model a black image. The portal
returned the real desktop once the user allowed it. These tests stand in for the
portal at its D-Bus boundary (`_portal_call`); the VM run is the real one.
"""
import pytest
from PIL import Image

from core import screen_grab


@pytest.fixture
def wayland(monkeypatch):
    monkeypatch.setattr(screen_grab.sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setattr(screen_grab, "_from_mss",
                        lambda: pytest.fail("mss sees only X11 windows on Wayland"))


def test_the_portal_picture_is_used_and_its_file_removed(wayland, monkeypatch, tmp_path):
    shot = tmp_path / "Screenshot.png"
    Image.new("RGB", (64, 40), (200, 120, 40)).save(shot)
    monkeypatch.setattr(screen_grab, "_portal_call",
                        lambda timeout: (0, {"uri": ("s", f"file://{shot}")}))
    img = screen_grab.grab()
    assert img.size == (64, 40) and img.getpixel((5, 5)) == (200, 120, 40)
    assert not shot.exists(), "the portal saves into the user's Pictures; it is not left there"


@pytest.mark.parametrize("code", [1, 2])
def test_a_refusal_says_where_to_turn_screenshots_on(wayland, monkeypatch, code):
    monkeypatch.setattr(screen_grab, "_portal_call", lambda timeout: (code, {}))
    with pytest.raises(screen_grab.ScreenshotUnavailable) as e:
        screen_grab.grab()
    assert "Settings" in str(e.value) and "Screenshots" in str(e.value)


def test_an_unanswered_question_asks_the_user_to_click_allow(wayland, monkeypatch):
    def never(timeout):
        raise TimeoutError
    monkeypatch.setattr(screen_grab, "_portal_call", never)
    with pytest.raises(screen_grab.ScreenshotUnavailable) as e:
        screen_grab.grab()
    assert "Allow" in str(e.value)


def test_a_desktop_without_the_portal_is_said_plainly(wayland, monkeypatch):
    def missing(timeout):
        raise ConnectionRefusedError("no session bus")
    monkeypatch.setattr(screen_grab, "_portal_call", missing)
    with pytest.raises(screen_grab.ScreenshotUnavailable) as e:
        screen_grab.grab()
    assert "describe" in str(e.value)


def test_x11_keeps_using_mss(monkeypatch):
    monkeypatch.setattr(screen_grab.sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(screen_grab, "_portal_call", lambda t: pytest.fail("no portal on X11"))
    monkeypatch.setattr(screen_grab, "_from_mss", lambda: Image.new("RGB", (8, 8)))
    assert screen_grab.grab().size == (8, 8)
