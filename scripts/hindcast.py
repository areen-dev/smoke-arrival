"""Replay the whole pipeline on a past date — the demo's money shot.

Live mode tells you what is happening now. This tells you what happened on a
day worth showing: a November evening when Punjab was burning and Delhi woke
up under smoke. Same model, same maths, only the clock moves backwards.

    .venv/bin/python scripts/hindcast.py 2025-11-05
    .venv/bin/python scripts/hindcast.py 2025-11-05 --hour 06 --out show.json

What it needs from seat A (two functions that do not exist yet, see issue #5):

    app.firms.fetch_fires_archive(date) -> list[Fire] | list[dict]
        Dated fire detections from the FIRMS area API. Needs FIRMS_MAP_KEY
        in the environment; the key is never committed.

    app.wind.fetch_wind_grid_archive(date) -> dict
        The same grid dict fetch_wind_grid() returns ("lats", "lons",
        "times", "hours", "u", "v"), built from Open-Meteo's archive
        endpoint instead of the forecast one. u/v still (hours, lat, lon).

Until those two exist, this script stops with a plain message instead of a
traceback. Everything below the fetches — clustering, advection, arrivals,
the index — is seat B's and already works.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app import config, model  # noqa: E402


def run_hindcast(when: datetime, fires, grid) -> dict:
    """The replay itself: pure logic, no network.

    Takes the dated fires and the archived wind and returns the same
    document shape the API serves, so the page could render it unchanged.
    Kept separate from the fetching so it can be tested with synthetic
    data while seat A's archive fetchers are still being written.
    """
    sources = model.cluster_sources(fires, config.FIRMS["cell_deg"])
    cities = [
        model.forecast_city(key, sources, grid, when) for key in config.CITIES
    ]
    return {
        "kind": "hindcast",
        "replayed_at": when.isoformat(),
        "sources": {
            "raw_detections": len(fires),
            "cells": len(sources),
            "total_frp": round(sum(c["frp"] for c in sources), 1),
        },
        "cities": cities,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay the pipeline on a past date.")
    ap.add_argument("date", help="YYYY-MM-DD, e.g. 2025-11-05")
    ap.add_argument("--hour", type=int, default=6, help="hour of that day (default 6)")
    ap.add_argument("--out", help="where to write the JSON (default: hindcast-<date>.json)")
    args = ap.parse_args()

    try:
        from app.firms import fetch_fires_archive
        from app.wind import fetch_wind_grid_archive
    except ImportError as missing:
        print(f"Not ready yet: {missing}")
        print("Seat A still owes fetch_fires_archive (firms.py) and")
        print("fetch_wind_grid_archive (wind.py) — see issue #5.")
        return 2

    when = datetime.combine(
        datetime.strptime(args.date, "%Y-%m-%d").date(),
        time(hour=args.hour),
        tzinfo=timezone.utc,
    )
    fires = fetch_fires_archive(args.date)
    grid = fetch_wind_grid_archive(args.date)
    doc = run_hindcast(when, fires, grid)

    out = Path(args.out) if args.out else REPO_ROOT / f"hindcast-{args.date}.json"
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    s = doc["sources"]
    print(f"{args.date} {args.hour:02d}:00 UTC — {s['raw_detections']} detections, "
          f"{s['cells']} cells, FRP {s['total_frp']}")
    for c in doc["cities"]:
        first = c.get("first_arrival_hours")
        print(f"  {c['name']:<12} index {c['smoke_load_index']:>5}  {c['band']:<8} "
              f"first arrival: {first if first is not None else '-'} h")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
