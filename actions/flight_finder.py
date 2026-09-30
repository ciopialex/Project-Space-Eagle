from __future__ import annotations

from datetime import date
from pathlib import Path

from actions.flights.dates import parse_date
from actions.flights.google import GoogleFlightsReader, parse_labels, search_url
from actions.flights.model import Flight, Query
from actions.flights.places import alternatives, resolve
from core.tool_result import ToolResult

_CABINS = {"economy": "economy", "premium": "premium economy", "premium economy": "premium economy",
           "business": "business", "first": "first"}
_KEEP = 10
_SPEAK = 3


def _place(text: str, role: str) -> tuple[object, ToolResult | None]:
    spoken = (text or "").strip()
    if not spoken:
        return None, ToolResult.failure(f"I need to know where you're flying {role}.",
                                        guidance=f"Ask the user where they are flying {role}.")
    place = resolve(spoken)
    if place:
        return place, None
    options = alternatives(spoken)
    if options:
        names = ", ".join(f"{p.name} ({p.code})" for p in options)
        return None, ToolResult.failure(
            f"There's more than one {spoken}: {names}.",
            guidance=f"Ask the user which {spoken} they mean, then call again with its airport code.")
    return None, ToolResult.failure(
        f"I don't know an airport for '{spoken}'.",
        guidance=f"Ask the user which city or airport they mean by '{spoken}'.")


def _when(text: str, today: date, what: str) -> tuple[date | None, ToolResult | None]:
    when = parse_date(text or "", today)
    if when is None:
        return None, ToolResult.failure(f"I couldn't understand the {what} date '{text}'.",
                                        guidance=f"Ask the user for the {what} date.")
    if when < today:
        return None, ToolResult.failure(f"The {what} date {when.isoformat()} is in the past.",
                                        guidance=f"Ask the user for a {what} date from today on.")
    return when, None


def _spoken(said, place) -> str:
    said = (said or "").strip()
    if said and not (len(said) == 3 and said.isupper()):
        return said[:1].upper() + said[1:]
    return place.name if place.kind == "city" else place.code


def _price_text(f: Flight) -> str:
    if f.price is None:
        return "price not shown"
    amount = int(f.price) if f.price == int(f.price) else f.price
    return f"{amount} {f.currency}".strip()


def _stops_text(f: Flight) -> str:
    return "nonstop" if f.stops == 0 else f"{f.stops} stop" + ("s" if f.stops > 1 else "")


def _line(f: Flight) -> str:
    return f"{f.airline}, {_stops_text(f)}, {f.depart} to {f.arrive}, {_price_text(f)}"


def _ranked(flights: list[Flight]) -> list[Flight]:
    return sorted(flights, key=lambda f: (f.price is None, f.price or 0, f.stops, f.duration_min or 0))


def _save(q: Query, flights: list[Flight], url: str) -> Path:
    folder = Path.home() / "Documents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Flights {q.origin.code}-{q.destination.code} {q.depart.isoformat()}.txt"
    lines = [f"{q.origin.name} to {q.destination.name}, {q.depart.isoformat()}"
             + (f", back {q.ret.isoformat()}" if q.ret else ""), url, ""]
    lines += [f"{i}. {_line(f)}" for i, f in enumerate(flights, 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def flight_finder(parameters: dict, player=None, browser=None, today: date | None = None) -> ToolResult:
    p = parameters or {}
    today = today or date.today()
    origin, err = _place(p.get("origin", ""), "from")
    if err:
        return err
    destination, err = _place(p.get("destination", ""), "to")
    if err:
        return err
    if origin.code == destination.code:
        return ToolResult.failure("The departure and arrival are the same place.",
                                  guidance="Ask the user where they want to fly to.")
    depart, err = _when(p.get("date", ""), today, "departure")
    if err:
        return err
    ret = None
    if p.get("return_date"):
        ret, err = _when(p["return_date"], today, "return")
        if err:
            return err
        if ret < depart:
            return ToolResult.failure("The return date is before the departure.",
                                      guidance="Ask the user for the return date again.")
    try:
        adults = max(1, min(9, int(p.get("passengers") or 1)))
    except (TypeError, ValueError):
        adults = 1
    cabin = _CABINS.get(str(p.get("cabin") or "economy").strip().lower(), "economy")
    q = Query(origin, destination, depart, ret, adults, cabin)
    url = search_url(q)

    state, labels = (browser or GoogleFlightsReader()).read(url)
    if state in ("consent_blocked", "not_loaded"):
        return ToolResult.failure("Flight search isn't reachable right now.",
                                  guidance="Tell the user flight search did not load. Offer to open Google Flights in the browser.",
                                  url=url)
    flights = _ranked(parse_labels(labels))[:_KEEP]
    route = (f"{_spoken(p.get('origin'), origin)} to {_spoken(p.get('destination'), destination)}"
             f" on {depart.strftime('%A %-d %B')}")
    if not flights:
        return ToolResult.success(f"Google shows no flights from {route}.", url=url, flights=[])

    spoken = "; ".join(_line(f) for f in flights[:_SPEAK])
    message = f"Cheapest from {route}: {spoken}."
    if player is not None and hasattr(player, "show_content"):
        try:
            player.show_content(f"FLIGHTS {origin.code}-{destination.code}",
                                "\n".join(f"{i}. {_line(f)}" for i, f in enumerate(flights, 1)))
        except Exception:
            pass
    data = {"url": url, "flights": [
        {"airline": f.airline, "stops": f.stops, "depart": f.depart, "arrive": f.arrive,
         "duration_min": f.duration_min, "price": f.price, "currency": f.currency} for f in flights]}
    if p.get("save"):
        try:
            message += f" Saved to {_save(q, flights, url).name} in Documents."
        except OSError:
            message += " I couldn't save the list to Documents."
    return ToolResult.success(message, **data)
