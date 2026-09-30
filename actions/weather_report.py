"""The weather, said out loud.

This used to open a Google search tab and report "Showing the weather", which
answers a spoken question by handing the user a web page to read. It now asks
Open-Meteo (free, no key) and returns one sentence to say.
"""
from __future__ import annotations

import requests

from core import prefs
from core.tool_result import ToolResult

_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT = 6

#: WMO weather interpretation codes, in the words a person would use.
_CONDITIONS = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy",
    51: "drizzling", 53: "drizzling", 55: "drizzling",
    56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "raining", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snowing", 75: "heavy snow", 77: "snow grains",
    80: "showers", 81: "showers", 82: "heavy showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}


def _place(city: str) -> dict | None:
    r = requests.get(_GEOCODE, params={"name": city, "count": 1,
                                       "language": "en", "format": "json"},
                     timeout=_TIMEOUT)
    r.raise_for_status()
    results = (r.json() or {}).get("results") or []
    return results[0] if results else None


def _forecast(lat: float, lon: float, fahrenheit: bool) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 1,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }
    if fahrenheit:
        params.update(temperature_unit="fahrenheit", wind_speed_unit="mph")
    r = requests.get(_FORECAST, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json() or {}


def _first(daily: dict, key: str):
    values = daily.get(key) or []
    return values[0] if values else None


def weather_action(parameters: dict, player=None, session_memory=None) -> ToolResult:
    city = str((parameters or {}).get("city") or "").strip()
    if not city:
        return ToolResult.failure(
            "No city was given.",
            guidance="Ask the user which city, or use the one you remember "
                     "they live in.")

    fahrenheit = str(prefs.get("temperature_unit") or "").lower().startswith("f")
    try:
        place = _place(city)
        if place is None:
            return ToolResult.failure(
                f"There is no place called {city} in the weather service.",
                guidance="Ask the user which city they mean.")
        data = _forecast(place["latitude"], place["longitude"], fahrenheit)
    except requests.RequestException as e:
        print(f"[Weather] unreachable: {e}")
        return ToolResult.failure(
            "The weather service could not be reached.",
            guidance="Tell the user the weather is unavailable right now.")

    now = data.get("current") or {}
    daily = data.get("daily") or {}
    name = place.get("name") or city
    temp = now.get("temperature_2m")
    feels = now.get("apparent_temperature")
    condition = _CONDITIONS.get(now.get("weather_code"), "")
    high = _first(daily, "temperature_2m_max")
    low = _first(daily, "temperature_2m_min")
    rain = _first(daily, "precipitation_probability_max")

    if temp is None:
        return ToolResult.failure(
            f"The weather service had no reading for {name}.",
            guidance="Tell the user the weather is unavailable right now.")

    sentence = f"It's {round(temp)} degrees"
    if condition:
        sentence += f" and {condition}"
    sentence += f" in {name}"
    if high is not None and low is not None:
        sentence += f", with a high of {round(high)} and a low of {round(low)}"
    if rain is not None and rain >= 30:
        sentence += f", and a {round(rain)} percent chance of rain"
    sentence += "."

    unit = "F" if fahrenheit else "C"
    if player:
        try:
            player.write_log(f"SYS: Weather · {name}: {round(temp)}°{unit}"
                             f"{', ' + condition if condition else ''}")
        except Exception:
            pass
    return ToolResult.success(
        sentence, city=name, country=place.get("country", ""), unit=unit,
        temperature=temp, feels_like=feels, condition=condition,
        high=high, low=low, rain_chance=rain)
