from __future__ import annotations

import re
from datetime import date, timedelta

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
    "ianuarie": 1, "februarie": 2, "martie": 3, "aprilie": 4, "mai": 5,
    "iunie": 6, "iulie": 7, "septembrie": 9, "octombrie": 10,
    "noiembrie": 11, "decembrie": 12,
}

_WEEKDAYS: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_IN_RE = re.compile(r"in (\d+) (day|days|week|weeks)")
_THIS_WEEKDAY_RE = re.compile(r"this\s+([a-z]+)")
_WEEKDAY_RE = re.compile(r"(?:next\s+)?([a-z]+)")
_DAY_OF_MONTH_RE = re.compile(r"(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)")
_DMY_RE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")
_DM_RE = re.compile(r"(\d{1,2})[./](\d{1,2})")
_DAY_MONTH_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)")
_MONTH_DAY_RE = re.compile(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?")


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _roll_past_to_next_year(result: date | None, today: date) -> date | None:
    if result is None:
        return None
    if result < today:
        return _safe_date(result.year + 1, result.month, result.day)
    return result


def _next_day_of_month(day: int, today: date) -> date | None:
    year, month = today.year, today.month
    for _ in range(24):
        candidate = _safe_date(year, month, day)
        if candidate is not None and candidate >= today:
            return candidate
        month += 1
        if month > 12:
            month = 1
            year += 1
    return None


def parse_date(text: str, today: date) -> date | None:
    if not text or not text.strip():
        return None
    raw = text.strip()
    low = raw.lower()

    m = _ISO_RE.fullmatch(raw)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return _safe_date(y, mo, d)

    if low == "today":
        return today
    if low in ("tomorrow", "mâine", "maine"):
        return today + timedelta(days=1)
    if low in ("day after tomorrow", "poimâine", "poimaine"):
        return today + timedelta(days=2)

    m = _IN_RE.fullmatch(low)
    if m:
        n = int(m.group(1))
        days = n * 7 if m.group(2).startswith("week") else n
        return today + timedelta(days=days)

    m = _THIS_WEEKDAY_RE.fullmatch(low)
    if m and m.group(1) in _WEEKDAYS:
        target = _WEEKDAYS[m.group(1)]
        ahead = (target - today.weekday()) % 7
        return today + timedelta(days=ahead)

    m = _WEEKDAY_RE.fullmatch(low)
    if m and m.group(1) in _WEEKDAYS:
        target = _WEEKDAYS[m.group(1)]
        ahead = (target - today.weekday()) % 7
        if ahead == 0:
            ahead = 7
        return today + timedelta(days=ahead)

    m = _DAY_OF_MONTH_RE.fullmatch(low)
    if m:
        return _next_day_of_month(int(m.group(1)), today)

    m = _DMY_RE.fullmatch(raw)
    if m:
        d, mo, y = (int(g) for g in m.groups())
        return _safe_date(y, mo, d)

    m = _DM_RE.fullmatch(raw)
    if m:
        d, mo = (int(g) for g in m.groups())
        return _roll_past_to_next_year(_safe_date(today.year, mo, d), today)

    m = _DAY_MONTH_RE.fullmatch(low)
    if m and m.group(2) in _MONTHS:
        d = int(m.group(1))
        mo = _MONTHS[m.group(2)]
        return _roll_past_to_next_year(_safe_date(today.year, mo, d), today)

    m = _MONTH_DAY_RE.fullmatch(low)
    if m and m.group(1) in _MONTHS:
        mo = _MONTHS[m.group(1)]
        d = int(m.group(2))
        return _roll_past_to_next_year(_safe_date(today.year, mo, d), today)

    return None
