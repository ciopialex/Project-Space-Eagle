"""'What's the weather like today?' is onboarding's first suggestion, and a new user's
city is not known yet: the eagle answered it with "What city?". Without a city the
weather is for the computer's time-zone city, read from the disk, and the answer
says so."""
import os

from actions import weather_report as w


def test_the_time_zone_names_a_city(tmp_path):
    for zone, city in (("Europe/Bucharest", "Bucharest"), ("America/New_York", "New York")):
        link = tmp_path / zone.replace("/", "_")
        os.symlink(f"/usr/share/zoneinfo/{zone}", link)
        assert w.zone_city(str(link)) == city


def test_a_zone_with_no_city_names_none(tmp_path):
    for zone in ("Etc/UTC", "UTC"):
        link = tmp_path / zone.replace("/", "_")
        os.symlink(f"/usr/share/zoneinfo/{zone}", link)
        assert w.zone_city(str(link)) == ""


def _fake_service(monkeypatch, asked):
    def place(city):
        asked.append(city)
        return {"name": city, "latitude": 1.0, "longitude": 2.0, "country": "X"}
    monkeypatch.setattr(w, "_place", place)
    monkeypatch.setattr(w, "_forecast", lambda lat, lon, f: {
        "current": {"temperature_2m": 11.4, "weather_code": 3},
        "daily": {"temperature_2m_max": [14], "temperature_2m_min": [6]}})


def test_no_city_uses_the_computers_and_says_so(monkeypatch):
    asked = []
    _fake_service(monkeypatch, asked)
    monkeypatch.setattr(w, "zone_city", lambda: "Bucharest")
    r = w.weather_action({})
    assert r.ok and asked == ["Bucharest"]
    assert "11 degrees" in r.message and "time zone" in r.message


def test_a_named_city_is_used_as_said(monkeypatch):
    asked = []
    _fake_service(monkeypatch, asked)
    monkeypatch.setattr(w, "zone_city", lambda: "Bucharest")
    r = w.weather_action({"city": "Paris"})
    assert asked == ["Paris"] and "time zone" not in r.message


def test_no_city_and_no_zone_city_still_asks(monkeypatch):
    monkeypatch.setattr(w, "zone_city", lambda: "")
    r = w.weather_action({})
    assert not r.ok and "which city" in r.guidance
