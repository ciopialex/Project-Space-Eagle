from __future__ import annotations

import time

from actions.messaging import clamped_seconds, ensure_running, relevant_titles

TG_URL = "https://web.telegram.org/k/"
ROWS = '.chatlist-chat'
LOGIN = '[class*="qrContainer"], [class*="qrCanvas"], [class*="pageSignQR"]'
SEARCH = '.input-search input, input.input-search-input'
COMPOSER = '.input-message-input[contenteditable]'
HEADER_TITLE = '.sidebar-header .peer-title, #column-center .peer-title'
OUTGOING = '.bubble.is-out'
MESSAGE = '.message'
EXCLUDE = '.time, .time-inner, .reply, .reactions, [class^="bubble-"]'
NO_RESULTS = '[class*="empty" i]'

_TITLES_JS = """sel => [...document.querySelectorAll(sel)].map(r => {
    const s = r.querySelector('.peer-title');
    return s ? s.textContent.trim() : null;
}).filter(Boolean)"""

_CLICK_ROW_JS = """(args) => {
    const [rowSel, title] = args;
    const rows = [...document.querySelectorAll(rowSel)];
    for (const r of rows) {
        const s = r.querySelector('.peer-title');
        if (s && s.textContent.trim() === title) {
            r.click();
            return true;
        }
    }
    return false;
}"""

_LAST_OUTGOING_JS = r"""(args) => {
    const [bubbleSel, textSel, excludeSel] = args;
    const all = document.querySelectorAll(bubbleSel);
    if (!all.length) return null;
    const bubble = all[all.length - 1];
    const target = bubble.querySelector(textSel);
    if (!target) return null;
    const walk = node => {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent;
        if (node.nodeType !== Node.ELEMENT_NODE) return '';
        if (excludeSel && node.matches && node.matches(excludeSel)) return '';
        if (node.tagName === 'IMG') return node.getAttribute('alt') || '';
        let out = '';
        for (const child of node.childNodes) out += walk(child);
        return out;
    };
    const text = walk(target).replace(/\s+/g, ' ').trim();
    return text || null;
}"""


class TelegramWeb:
    platform = "telegram"

    def __init__(self, browser=None):
        if browser is None:
            from actions.grounding.web.browser import default_browser
            browser = default_browser()
        self.browser = browser

    def _open(self) -> None:
        ensure_running(self.browser)
        try:
            already = self.browser.call(lambda p: p.url.startswith(TG_URL))
        except Exception:
            already = False
        if not already:
            self.browser.goto(TG_URL)

    def ready(self) -> str | None:
        try:
            self._open()
        except Exception:
            return "not_loaded"

        def probe(p):
            if p.locator(ROWS).count():
                return None
            if p.locator(LOGIN).count():
                return "signed_out"
            return "not_loaded"
        try:
            return self.browser.call(probe)
        except Exception:
            return "not_loaded"

    def visible_titles(self) -> list[str]:
        try:
            titles = self.browser.call(lambda p: p.evaluate(_TITLES_JS, ROWS))
        except Exception:
            return []
        return list(titles or [])

    def search(self, query: str) -> list[str] | None:
        before = self.visible_titles()

        def go(p):
            box = p.locator(SEARCH).first
            box.click(timeout=6_000)
            box.fill(query)
        try:
            self.browser.call(go)
        except Exception:
            return None

        timeout = clamped_seconds("message_search_seconds", 6.0, 1.0, 20.0)
        deadline = time.monotonic() + timeout
        while True:
            titles = self.visible_titles()
            if titles != before:
                relevant = relevant_titles(titles, query)
                if relevant:
                    return relevant
            try:
                no_results = bool(self.browser.call(lambda p: p.locator(NO_RESULTS).count()))
            except Exception:
                no_results = False
            if no_results:
                return []
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.2)

    def open_chat(self, title: str) -> bool:
        def go(p):
            return p.evaluate(_CLICK_ROW_JS, [ROWS, title])
        try:
            clicked = bool(self.browser.call(go))
        except Exception:
            return False
        if not clicked:
            return False
        try:
            self.browser.call(lambda p: p.wait_for_timeout(600))
        except Exception:
            pass
        return self.current_title() == title

    def send(self, text: str) -> bool:
        def go(p):
            box = p.locator(COMPOSER).first
            box.click(timeout=6_000)
            box.fill(text)
            p.keyboard.press("Enter")
            p.wait_for_timeout(1200)
            return True
        try:
            return bool(self.browser.call(go))
        except Exception:
            return False

    def last_outgoing(self) -> str | None:
        try:
            return self.browser.call(
                lambda p: p.evaluate(_LAST_OUTGOING_JS, [OUTGOING, MESSAGE, EXCLUDE]))
        except Exception:
            return None

    def current_title(self) -> str | None:
        def probe(p):
            head = p.locator(HEADER_TITLE).first
            if head.count() == 0:
                return None
            shown = (head.inner_text() or "").strip()
            return shown or None
        try:
            return self.browser.call(probe)
        except Exception:
            return None
