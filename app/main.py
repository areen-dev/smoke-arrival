"""FastAPI service and Lambda handler for Smoke Arrival.

Seat C owns this file. It does three jobs:

1. Serves the map page (static/index.html) and its files.
2. Serves the four JSON endpoints from AGENTS.md section 5.
3. Calls the pipeline built by seats A and B and caches the result for 15 minutes.
   If a live fetch fails it serves the last good forecast (AGENTS.md section 12,
   trap 10). Only if there has never been a good forecast does it fall back to
   clearly labelled SAMPLE DATA, so the page always has something honest to show.

Run locally:  .venv\\Scripts\\uvicorn app.main:app --port 8000
Lambda entry: app.main.handler
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mangum import Mangum

from app import config

log = logging.getLogger("smoke-arrival")
logging.basicConfig(level=logging.INFO)

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
SAMPLE_FILE = STATIC_DIR / "mock" / "forecast.json"

HEATMAP_CAP = 600   # section 5: heatmap is capped at 600 cells
TOP_CELLS_CAP = 60  # section 5: top_cells is capped at 60
ARRIVALS_CAP = 12   # section 5: arrivals is capped at 12 per city
SAMPLE_PREFIX = "SAMPLE DATA"

# Last good live forecast, kept on disk so it survives a restart. Lambda only lets us write
# to the temp directory, so that is where it goes. It also lives in memory (_cache["last_good"]).
LAST_GOOD_FILE = Path(tempfile.gettempdir()) / "smoke-arrival-last-good.json"

app = FastAPI(title="Smoke Arrival", version="0.1.0")

# ---------------------------------------------------------------------------
# Cache: one forecast, reused for CACHE_TTL_SECONDS (15 minutes).
# ---------------------------------------------------------------------------
_cache: dict = {"at": 0.0, "value": None, "last_good": None}
_lock = asyncio.Lock()


async def _maybe_await(value):
    """Seat A's fetchers may be sync or async. Accept both."""
    if inspect.isawaitable(value):
        return await value
    return value


def _get(obj, name, default=None):
    """Read a field from a dict or an object, so we do not depend on A's and B's record types."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ---------------------------------------------------------------------------
# Sample data fallback
# ---------------------------------------------------------------------------
def _sample_forecast() -> dict:
    """Load the mock forecast and move its clock to now, so it reads like a fresh forecast.

    The model_note keeps its "SAMPLE DATA" prefix. The page shows a banner when it sees it.
    """
    doc = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    doc["generated_at"] = now.isoformat()
    for city in doc["cities"]:
        hours = city.get("first_arrival_hours")
        city["first_arrival_at"] = (now + timedelta(hours=hours)).isoformat() if hours is not None else None
    return doc


# ---------------------------------------------------------------------------
# Live pipeline (seats A and B)
# ---------------------------------------------------------------------------
def _cell_record(cell) -> dict:
    return {
        "lat": round(float(_get(cell, "lat")), 2),
        "lon": round(float(_get(cell, "lon")), 2),
        "frp": round(float(_get(cell, "frp", 0.0)), 1),
        "detections": int(_get(cell, "detections", _get(cell, "n", 0))),
        "newest": str(_get(cell, "newest", "")),
    }


async def _live_forecast() -> dict:
    """Run the real pipeline: fires in, wind in, model, then assemble the section 5 document.

    The function names below are the ones AGENTS.md section 7 promises:
      app.firms.fetch_fires(window), app.firms.in_source_region(fires)
      app.wind.fetch_wind_grid()
      app.model.cluster_sources(fires, cell_deg), app.model.forecast_city(city_key, sources, grid, now)
    If any of them is missing or has a different shape, this raises and the caller falls back to sample data.
    """
    from app import firms, model, wind  # imported here so the service still starts if A or B are not merged yet

    now = datetime.now(timezone.utc).replace(microsecond=0)
    window = config.FIRMS["window"]

    fires = await _maybe_await(firms.fetch_fires(window))
    raw_detections = len(fires)
    fires = firms.in_source_region(fires)
    grid = await _maybe_await(wind.fetch_wind_grid())
    sources = model.cluster_sources(fires, config.FIRMS["cell_deg"])

    cells = sorted((_cell_record(c) for c in sources), key=lambda c: -c["frp"])
    cities = [model.forecast_city(key, sources, grid, now) for key in config.CITIES]
    for city in cities:
        city["arrivals"] = city.get("arrivals", [])[:ARRIVALS_CAP]

    return {
        "generated_at": now.isoformat(),
        "window": window,
        "model_note": (
            "The smoke-load index is uncalibrated. It ranks relative load between cities and days; "
            "it is not a concentration in ug/m3. Wind is the 10 m forecast, not the wind where smoke travels."
        ),
        "sources": {
            "region": config.SOURCE_BBOX,
            "raw_detections": raw_detections,
            "cells": len(cells),
            "total_frp": round(sum(c["frp"] for c in cells), 1),
            "top_cells": cells[:TOP_CELLS_CAP],
            "heatmap": cells[:HEATMAP_CAP],
        },
        "wind": {
            "height_m": config.WIND["height_m"],
            "points": len(config.WIND["grid_lats"]) * len(config.WIND["grid_lons"]),
            "hours": config.WIND["forecast_days"] * 24,
        },
        "cities": cities,
    }


def _save_last_good(doc: dict) -> None:
    """Keep the newest successful live forecast in memory and on disk. Never raises."""
    _cache["last_good"] = doc
    try:
        tmp = LAST_GOOD_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(LAST_GOOD_FILE)  # swap in one step so a reader never sees half a file
    except OSError as exc:
        log.warning("Could not save the last good forecast: %r", exc)


def _load_last_good() -> dict | None:
    """The last successful live forecast: memory first, then the file. None if there never was one."""
    if _cache["last_good"] is not None:
        return _cache["last_good"]
    try:
        return json.loads(LAST_GOOD_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def _build_forecast() -> dict:
    """Live forecast, else the last good one, else labelled sample data. The page never sees an error."""
    try:
        doc = await _live_forecast()
    except Exception as exc:  # noqa: BLE001  any failure must degrade, never reach the visitor
        stale = _load_last_good()
        if stale is not None:
            log.warning("Live forecast unavailable, serving the last good forecast from %s: %r",
                        stale.get("generated_at"), exc)
            return stale
        log.warning("Live forecast unavailable and no earlier forecast exists, serving SAMPLE DATA: %r", exc)
        return _sample_forecast()
    _save_last_good(doc)
    return doc


async def get_forecast() -> dict:
    """Return the cached forecast, rebuilding it at most once per 15 minutes."""
    ttl = config.CACHE_TTL_SECONDS
    value = _cache["value"]
    if value is not None and time.monotonic() - _cache["at"] < ttl:
        return value
    async with _lock:  # one request rebuilds, the others wait for it
        value = _cache["value"]
        if value is not None and time.monotonic() - _cache["at"] < ttl:
            return value
        value = await _build_forecast()
        _cache["value"], _cache["at"] = value, time.monotonic()
        return value


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/cities")
async def cities() -> dict:
    return {
        "cities": [
            {"key": key, "name": c["name"], "lat": c["lat"], "lon": c["lon"], "radius_km": c["radius_km"]}
            for key, c in config.CITIES.items()
        ]
    }


@app.get("/api/forecast")
async def forecast() -> dict:
    return await get_forecast()


@app.get("/api/forecast/{city}")
async def forecast_city(city: str) -> dict:
    doc = await get_forecast()
    for item in doc["cities"]:
        if item["city"] == city:
            return {"generated_at": doc["generated_at"], "model_note": doc["model_note"], **copy.deepcopy(item)}
    known = ", ".join(config.CITIES)
    raise HTTPException(status_code=404, detail=f"Unknown city '{city}'. Try one of: {known}.")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# AWS Lambda entry point (seat A sets the handler to app.main.handler).
handler = Mangum(app, lifespan="off")

