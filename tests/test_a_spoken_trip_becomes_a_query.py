import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.flights.dates import parse_date  # noqa: E402
from actions.flights.places import alternatives, resolve  # noqa: E402

FRI = date(2026, 10, 2)


@pytest.mark.parametrize("text,code", [
    ("OTP", "OTP"), ("otp", "OTP"), ("Bucharest", "BUH"), ("București", "BUH"),
    ("London", "LON"), ("Cluj", "CLJ"), ("Cluj-Napoca", "CLJ"), ("JFK", "JFK"), ("New York", "NYC"),
])
def test_places(text, code):
    assert resolve(text).code == code


def test_unknown_place_is_none():
    assert resolve("Atlantis") is None


def test_city_ambiguous_across_countries_is_none():
    assert resolve("Santiago") is None


def test_city_ambiguous_across_countries_lists_alternatives():
    codes = {p.code for p in alternatives("Santiago")}
    assert {"SCL", "STI"} <= codes
    assert len(alternatives("Santiago")) <= 4


def test_city_single_country_still_resolves():
    assert resolve("Porto").code == "OPO"


@pytest.mark.parametrize("text,code", [("NYC", "NYC"), ("LON", "LON")])
def test_metro_code_typed_directly_resolves_to_the_city(text, code):
    place = resolve(text)
    assert place.code == code
    assert place.kind == "city"


@pytest.mark.parametrize("text,code", [
    ("München", "MUC"), ("Munchen", "MUC"), ("muenchen", "MUC"),
])
def test_local_spellings_without_a_metro_code(text, code):
    assert resolve(text).code == code


@pytest.mark.parametrize("text,expected", [
    ("2026-10-10", date(2026, 10, 10)), ("10.10.2026", date(2026, 10, 10)),
    ("10 October", date(2026, 10, 10)), ("Oct 10th", date(2026, 10, 10)),
    ("tomorrow", date(2026, 10, 3)), ("mâine", date(2026, 10, 3)),
    ("next friday", date(2026, 10, 9)), ("friday", date(2026, 10, 9)), ("monday", date(2026, 10, 5)),
    ("in 3 days", date(2026, 10, 5)), ("1 September", date(2027, 9, 1)), ("15 noiembrie", date(2026, 11, 15)),
])
def test_dates(text, expected):
    assert parse_date(text, FRI) == expected


def test_nonsense_date_is_none():
    assert parse_date("whenever", FRI) is None


def test_in_2_weeks():
    assert parse_date("in 2 weeks", FRI) == date(2026, 10, 16)


def test_day_month_without_year_rolls_past_dates_forward():
    assert parse_date("10/10", date(2026, 12, 1)) == date(2027, 10, 10)


@pytest.mark.parametrize("text,expected", [
    ("this friday", date(2026, 10, 2)), ("this sunday", date(2026, 10, 4)),
])
def test_this_weekday_counts_today(text, expected):
    assert parse_date(text, FRI) == expected


@pytest.mark.parametrize("text,expected", [
    ("the 15th", date(2026, 10, 15)), ("15th", date(2026, 10, 15)),
])
def test_ordinal_day_of_month(text, expected):
    assert parse_date(text, FRI) == expected


def test_ordinal_day_of_month_rolls_to_next_month_when_already_past():
    assert parse_date("the 1st", FRI) == date(2026, 11, 1)
