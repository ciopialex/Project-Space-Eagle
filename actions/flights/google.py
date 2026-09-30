from __future__ import annotations

import re
import time
import urllib.parse

from actions.flights.model import Flight, Query

_ROWS_JS = """() => [...document.querySelectorAll('[role="listitem"] [aria-label], li [aria-label]')]
    .map(e => e.getAttribute('aria-label')).filter(Boolean)"""
_CONSENT_BUTTONS = ("Reject all", "Respinge tot", "Alle ablehnen", "Tout refuser", "Rechazar todo")
_WAIT_S = 15.0

_PRICE = re.compile(r"From ([\d.,]+) ([^.\d][^.]*?)(?: (?:round trip|one way) total)?\.", re.I)
_STOPS = re.compile(r"(Nonstop|(\d+) stops?) flight with (.+?)\.(?: |$)", re.I)
_TIMES = re.compile(r"at (\d{1,2}:\d{2} ?[AP]M) on .+? and arrives at .+? at (\d{1,2}:\d{2} ?[AP]M)", re.I)
_DURATION = re.compile(r"Total duration (?:(\d+) hr)? ?(?:(\d+) min)?", re.I)


def search_url(q: Query) -> str:
    text = f"Flights to {q.destination.code} from {q.origin.code} on {q.depart.isoformat()}"
    text += f" through {q.ret.isoformat()}" if q.ret else " one way"
    if q.adults > 1:
        text += f" {q.adults} adults"
    if q.cabin != "economy":
        text += f" {q.cabin} class"
    return "https://www.google.com/travel/flights?" + urllib.parse.urlencode({"q": text, "hl": "en"})


def _clean(label: str) -> str:
    return re.sub(r"[  \s]+", " ", label).strip()


def _price(text: str) -> tuple[float | None, str]:
    m = _PRICE.search(text)
    if not m:
        return None, ""
    raw = m.group(1).replace(",", "")
    try:
        return float(raw), m.group(2).strip()
    except ValueError:
        return None, m.group(2).strip()


def parse_labels(labels: list[str]) -> list[Flight]:
    flights, seen = [], set()
    for label in labels:
        text = _clean(label or "")
        stops, times = _STOPS.search(text), _TIMES.search(text)
        if not (stops and times and text.startswith("From ")):
            continue
        price, currency = _price(text)
        dur = _DURATION.search(text)
        minutes = None
        if dur and (dur.group(1) or dur.group(2)):
            minutes = int(dur.group(1) or 0) * 60 + int(dur.group(2) or 0)
        flight = Flight(
            price=price, currency=currency,
            airline=stops.group(3).strip(),
            stops=0 if stops.group(1).lower() == "nonstop" else int(stops.group(2)),
            depart=times.group(1), arrive=times.group(2),
            duration_min=minutes, label=label)
        key = (flight.airline, flight.depart, flight.arrive, flight.price)
        if key not in seen:
            seen.add(key)
            flights.append(flight)
    return flights


def _read(page, url: str) -> tuple[str, list[str]]:
    page.goto(url, timeout=30_000, wait_until="domcontentloaded")
    if "consent.google" in page.url:
        for name in _CONSENT_BUTTONS:
            button = page.get_by_role("button", name=name)
            if button.count():
                button.first.click()
                break
        else:
            return "consent_blocked", []
    deadline = time.monotonic() + _WAIT_S
    labels: list[str] = []
    while time.monotonic() < deadline:
        labels = page.evaluate(_ROWS_JS)
        if parse_labels(labels):
            return "ok", labels
        if "consent.google" not in page.url and page.get_by_text("No results", exact=False).count():
            return "no_results", labels
        page.wait_for_timeout(500)
    if "consent.google" in page.url:
        return "consent_blocked", []
    return ("not_loaded" if not labels else "no_results"), labels


class GoogleFlightsReader:
    def __init__(self, browser=None):
        self.browser = browser

    def read(self, url: str) -> tuple[str, list[str]]:
        if self.browser is None:
            from actions.grounding.web.browser import default_browser
            self.browser = default_browser()
        try:
            if getattr(self.browser, "running", True) is False:
                self.browser.start()
            return self.browser.call(lambda page: _read(page, url), timeout=60.0)
        except Exception:
            return "not_loaded", []
