from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIX = Path(__file__).resolve().parent / "fixtures" / "messaging"
playwright = pytest.importorskip("playwright.sync_api")


class FixtureBrowser:
    def __init__(self, page, html):
        self.page, self.html = page, html

    def goto(self, url):
        self.page.set_content(self.html.read_text("utf-8"))
        return url

    def call(self, fn, timeout=45.0):
        return fn(self.page)


@pytest.fixture(scope="module")
def page():
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser.new_page()
        browser.close()


@pytest.mark.parametrize("module,cls", [("whatsapp", "WhatsAppWeb"), ("telegram", "TelegramWeb")])
def test_a_signed_in_list_is_ready_and_lists_titles_not_previews(page, module, cls):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / f"{module}_chatlist.html"))
    surface.browser.goto("x")
    assert surface.ready() is None
    titles = surface.visible_titles()
    assert titles and all("\n" not in t for t in titles)


@pytest.mark.parametrize("module,cls,fixture", [("whatsapp", "WhatsAppWeb", "whatsapp_qr.html"),
                                                ("telegram", "TelegramWeb", "telegram_login.html")])
def test_a_signed_out_page_says_so(page, module, cls, fixture):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / fixture))
    surface.browser.goto("x")
    assert surface.ready() == "signed_out"


@pytest.mark.parametrize("module,cls", [("whatsapp", "WhatsAppWeb"), ("telegram", "TelegramWeb")])
def test_the_newest_outgoing_bubble_is_read(page, module, cls):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / f"{module}_chat.html"))
    surface.browser.goto("x")
    assert surface.last_outgoing() == "fixture probe"


@pytest.mark.parametrize("module,cls,fixture", [
    ("whatsapp", "WhatsAppWeb", "whatsapp_chat_emoji.html"),
    ("telegram", "TelegramWeb", "telegram_chat_emoji.html"),
])
def test_an_emoji_image_is_read_by_its_alt_text(page, module, cls, fixture):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / fixture))
    surface.browser.goto("x")
    assert surface.last_outgoing() == "hi \U0001F600"


@pytest.mark.parametrize("module,cls,fixture", [
    ("whatsapp", "WhatsAppWeb", "whatsapp_chat_reply.html"),
    ("telegram", "TelegramWeb", "telegram_chat_reply.html"),
])
def test_a_quoted_reply_preview_is_excluded(page, module, cls, fixture):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / fixture))
    surface.browser.goto("x")
    assert surface.last_outgoing() == "new text"


@pytest.mark.parametrize("module,cls", [("whatsapp", "WhatsAppWeb"), ("telegram", "TelegramWeb")])
def test_current_title_reads_the_open_chat_header(page, module, cls):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / f"{module}_chat.html"))
    surface.browser.goto("x")
    assert surface.current_title() == "Contact A"


@pytest.mark.parametrize("module,cls", [("whatsapp", "WhatsAppWeb"), ("telegram", "TelegramWeb")])
def test_current_title_is_none_with_no_chat_open(page, module, cls):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / f"{module}_chatlist.html"))
    surface.browser.goto("x")
    assert surface.current_title() is None


@pytest.mark.parametrize("module,cls,fixture,expect", [
    ("whatsapp", "WhatsAppWeb", "whatsapp_chatlist_delayed.html", ["Alex Popescu"]),
    ("telegram", "TelegramWeb", "telegram_chatlist_delayed.html", ["Alex Popescu"]),
])
def test_search_returns_the_filtered_list_once_it_changes(page, module, cls, fixture, expect):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / fixture))
    surface.browser.goto("x")
    assert surface.search("Alex") == expect


@pytest.mark.parametrize("module,cls,fixture", [
    ("whatsapp", "WhatsAppWeb", "whatsapp_chatlist_delayed_unrelated.html"),
    ("telegram", "TelegramWeb", "telegram_chatlist_delayed_unrelated.html"),
])
def test_an_unrelated_change_during_the_poll_is_not_mistaken_for_a_result(
        page, module, cls, fixture, monkeypatch):
    monkeypatch.setattr("actions.messaging.get_config", lambda: {"message_search_seconds": 1})
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / fixture))
    surface.browser.goto("x")
    assert surface.search("alex") is None


@pytest.mark.parametrize("module,cls,fixture", [
    ("whatsapp", "WhatsAppWeb", "whatsapp_chatlist_delayed_mixed.html"),
    ("telegram", "TelegramWeb", "telegram_chatlist_delayed_mixed.html"),
])
def test_only_the_relevant_titles_of_a_changed_list_are_returned(page, module, cls, fixture):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / fixture))
    surface.browser.goto("x")
    assert surface.search("alex") == ["Alex Popescu"]


@pytest.mark.parametrize("module,cls,fixture", [
    ("whatsapp", "WhatsAppWeb", "whatsapp_chatlist.html"),
    ("telegram", "TelegramWeb", "telegram_chatlist.html"),
])
def test_search_returns_none_when_the_list_never_changes(page, module, cls, fixture, monkeypatch):
    monkeypatch.setattr("actions.messaging.get_config", lambda: {"message_search_seconds": 1})
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / fixture))
    surface.browser.goto("x")
    assert surface.search("nobody matches this at all") is None


@pytest.mark.parametrize("module,cls,fixture", [
    ("whatsapp", "WhatsAppWeb", "whatsapp_chatlist_no_results.html"),
    ("telegram", "TelegramWeb", "telegram_chatlist_no_results.html"),
])
def test_search_returns_an_empty_list_when_the_app_confirms_no_results(page, module, cls, fixture):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    surface = getattr(mod, cls)(browser=FixtureBrowser(page, FIX / fixture))
    surface.browser.goto("x")
    assert surface.search("nobody") == []


class _StubBrowser:
    def __init__(self, running):
        self.running = running
        self.started = False
        self.navigated = []

    def start(self):
        self.started = True
        self.running = True

    def call(self, fn, timeout=45.0):
        return False

    def goto(self, url):
        self.navigated.append(url)
        return url


@pytest.mark.parametrize("module,cls", [("whatsapp", "WhatsAppWeb"), ("telegram", "TelegramWeb")])
def test_a_stopped_browser_is_started_before_navigating(module, cls):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    stub = _StubBrowser(running=False)
    surface = getattr(mod, cls)(browser=stub)
    surface._open()
    assert stub.started is True
    assert stub.navigated == [mod.WA_URL if module == "whatsapp" else mod.TG_URL]


@pytest.mark.parametrize("module,cls", [("whatsapp", "WhatsAppWeb"), ("telegram", "TelegramWeb")])
def test_a_running_browser_is_never_restarted(module, cls):
    mod = __import__(f"actions.messaging.{module}", fromlist=[cls])
    stub = _StubBrowser(running=True)
    surface = getattr(mod, cls)(browser=stub)
    surface._open()
    assert stub.started is False
