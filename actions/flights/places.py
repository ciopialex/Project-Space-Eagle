from __future__ import annotations

import json
from pathlib import Path

from actions.flights.model import Place
from core.contact_match import normalise

_DATA_PATH = Path(__file__).resolve().parent / "airports.json"

_METRO: dict[str, tuple[str, str]] = {
    "london": ("LON", "London"), "londra": ("LON", "London"),
    "new york": ("NYC", "New York"),
    "paris": ("PAR", "Paris"),
    "milan": ("MIL", "Milan"), "milano": ("MIL", "Milan"),
    "rome": ("ROM", "Rome"), "roma": ("ROM", "Rome"),
    "bucharest": ("BUH", "Bucharest"), "bucuresti": ("BUH", "Bucharest"),
    "stockholm": ("STO", "Stockholm"),
    "tokyo": ("TYO", "Tokyo"), "tokio": ("TYO", "Tokyo"),
    "washington": ("WAS", "Washington"),
    "chicago": ("CHI", "Chicago"),
    "moscow": ("MOW", "Moscow"), "moscova": ("MOW", "Moscow"),
    "osaka": ("OSA", "Osaka"),
    "seoul": ("SEL", "Seoul"),
    "beijing": ("BJS", "Beijing"),
    "sao paulo": ("SAO", "Sao Paulo"),
    "rio de janeiro": ("RIO", "Rio de Janeiro"), "rio": ("RIO", "Rio de Janeiro"),
    "toronto": ("YTO", "Toronto"),
    "montreal": ("YMQ", "Montreal"),
    "berlin": ("BER", "Berlin"),
    "istanbul": ("IST", "Istanbul"),
}

_LOCAL_ALIASES: dict[str, str] = {
    "munchen": "munich",
    "muenchen": "munich",
}

_METRO_BY_CODE: dict[str, str] = {code: name for code, name in _METRO.values()}

_Row = tuple[str, str, str, str, int]

_by_iata: dict[str, _Row] = {}
_by_municipality: dict[str, list[_Row]] = {}
_by_first_word: dict[str, list[_Row]] = {}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    rows = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    for iata, name, municipality, iso_country, size_rank in rows:
        row: _Row = (iata, name, municipality, iso_country, size_rank)
        _by_iata[iata] = row
        if not municipality:
            continue
        key = normalise(municipality)
        if not key:
            continue
        _by_municipality.setdefault(key, []).append(row)
        _by_first_word.setdefault(key.split()[0], []).append(row)
    _loaded = True


def _best_in_country(city_key: str, rows: list[_Row]) -> _Row:
    return sorted(
        rows,
        key=lambda r: (r[4], 0 if city_key in normalise(r[1]) else 1, r[0]),
    )[0]


def _by_country(rows: list[_Row]) -> dict[str, list[_Row]]:
    groups: dict[str, list[_Row]] = {}
    for row in rows:
        groups.setdefault(row[3], []).append(row)
    return groups


def _matching_rows(key: str) -> list[_Row]:
    _load()
    return _by_municipality.get(key) or _by_first_word.get(key) or []


def _place_key(text: str) -> str | None:
    key = normalise(text.strip())
    if not key:
        return None
    return _LOCAL_ALIASES.get(key, key)


def resolve(text: str) -> Place | None:
    if not text or not text.strip():
        return None
    _load()
    raw = text.strip()

    code = raw.upper()
    if len(code) == 3 and code.isalpha():
        if code in _by_iata:
            row = _by_iata[code]
            return Place(code=row[0], name=row[1], kind="airport")
        if code in _METRO_BY_CODE:
            return Place(code=code, name=_METRO_BY_CODE[code], kind="city")

    key = _place_key(raw)
    if key is None:
        return None

    if key in _METRO:
        metro_code, display = _METRO[key]
        return Place(code=metro_code, name=display, kind="city")

    rows = _matching_rows(key)
    if not rows:
        return None
    countries = _by_country(rows)
    if len(countries) > 1:
        reps = [_best_in_country(key, group) for group in countries.values()]
        clear = _clear_winner(key, reps)
        if clear is None:
            return None
        return Place(code=clear[0], name=clear[1], kind="airport")
    row = _best_in_country(key, rows)
    return Place(code=row[0], name=row[1], kind="airport")


def _clear_winner(key: str, reps: list[_Row]) -> _Row | None:
    largest = min(r[4] for r in reps)
    top = [r for r in reps if r[4] == largest]
    if len(top) == 1:
        return top[0]
    named = [r for r in top if key in normalise(r[1])]
    return named[0] if len(named) == 1 else None


def alternatives(text: str) -> list[Place]:
    if not text or not text.strip():
        return []
    key = _place_key(text)
    if key is None or key in _METRO:
        return []

    rows = _matching_rows(key)
    if not rows:
        return []
    countries = _by_country(rows)
    if len(countries) < 2:
        return []

    reps = [_best_in_country(key, group) for group in countries.values()]
    reps.sort(key=lambda r: (r[4], r[1], r[0]))
    return [Place(code=r[0], name=r[1], kind="airport") for r in reps[:4]]
