"""Build actions/flights/airports.json from OurAirports' public-domain CSV."""
from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path

import requests

PRIMARY_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
FALLBACK_URL = "https://ourairports.com/data/airports.csv"
OUT_PATH = Path(__file__).resolve().parent.parent / "actions" / "flights" / "airports.json"
SIZE_RANK = {"large_airport": 0, "medium_airport": 1}


def fetch_csv() -> tuple[str, str]:
    for url in (PRIMARY_URL, FALLBACK_URL):
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return url, resp.text
        except requests.RequestException:
            continue
    raise RuntimeError("could not fetch airports.csv from either source")


def build_rows(csv_text: str) -> list[list]:
    rows = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        rank = SIZE_RANK.get(row.get("type", ""))
        if rank is None:
            continue
        iata = (row.get("iata_code") or "").strip()
        if not iata:
            continue
        if (row.get("scheduled_service") or "").strip() != "yes":
            continue
        rows.append([
            iata,
            (row.get("name") or "").strip(),
            (row.get("municipality") or "").strip(),
            (row.get("iso_country") or "").strip(),
            rank,
        ])
    return rows


def main() -> None:
    url, csv_text = fetch_csv()
    rows = build_rows(csv_text)
    OUT_PATH.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"source: {url}", file=sys.stderr)
    print(f"rows: {len(rows)}", file=sys.stderr)
    print(f"bytes: {OUT_PATH.stat().st_size}", file=sys.stderr)


if __name__ == "__main__":
    main()
