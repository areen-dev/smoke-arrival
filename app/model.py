"""The model: clustering, advection, arrival, the index — pure maths.

No network, no files, no clock of its own: everything takes plain data in
and returns plain data out, so the whole model runs against synthetic
inputs in the tests (no live FIRMS or wind needed).

The wind grid every function below receives is a plain dictionary:

    {
      "lats":  [lat, ...]            ascending, degrees
      "lons":  [lon, ...]            ascending, degrees
      "times": [datetime, ...]       hourly timestamps, first hour first
      "hours": int                   number of hourly steps (e.g. 72)
      "u":     array (hours, lat, lon)   eastward wind, m/s
      "v":     array (hours, lat, lon)   northward wind, m/s
    }

Wind sampling is NOT in this module. Fetching the wind, converting
direction to u/v and interpolating it to a point belongs to app/wind.py
(seat A). This module imports `sample_wind` from there. If you are looking
for the interpolation code, it is in his file, not mine.

`times` is the key his fetch actually returns. Hour offsets are measured
from `times[0]`, and a timestamp carrying no timezone is read as UTC,
because his parse strips it. A "t0" key is accepted too, so neither
spelling can break the integration.
"""

from __future__ import annotations

import math
from datetime import timedelta, timezone

from app import config
from app.wind import sample_wind

CELL_DEG = config.FIRMS["cell_deg"]


def _field(record, name, default=None):
    """Read a field from either a dict record or an object (Sagar's Fire)."""
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km, R = 6371.0088 (the spec's radius)."""
    r = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cluster_sources(fires, cell_deg=CELL_DEG):
    """Snap detections to the cell grid and sum FRP per cell.

    Returns one dict per cell — lat/lon at the cell centre, summed frp,
    detection count, and the newest acquisition time as "YYYY-MM-DD HHMM"
    — sorted by frp, biggest first. One cell = one fire source, so the
    advection runs on tens of cells instead of thousands of detections.
    """
    cells = {}
    for fire in fires:
        lat = _field(fire, "lat")
        lon = _field(fire, "lon")
        frp = _field(fire, "frp")
        date = str(_field(fire, "acq_date", "") or "")
        time = str(_field(fire, "acq_time", "") or "").zfill(4)

        key = (math.floor(lat / cell_deg), math.floor(lon / cell_deg))
        cell = cells.setdefault(key, {"frp": 0.0, "detections": 0, "newest": ""})
        cell["frp"] += frp
        cell["detections"] += 1
        stamp = f"{date} {time}"
        if stamp > cell["newest"]:
            cell["newest"] = stamp

    out = [
        {
            "lat": round((i + 0.5) * cell_deg, 2),
            "lon": round((j + 0.5) * cell_deg, 2),
            "frp": round(c["frp"], 2),
            "detections": c["detections"],
            "newest": c["newest"],
        }
        for (i, j), c in cells.items()
    ]
    out.sort(key=lambda c: c["frp"], reverse=True)
    return out


def advect(grid, lat, lon, start_time, target):
    """Carry one source particle through the wind and look for arrival.

    target is the city dict {lat, lon, radius_km}. Steps are dt_seconds,
    up to max_forecast_hours. An arrival counts only if the particle has
    travelled at least min_travel_km by the time it FIRST enters the
    radius — a fire that close to a city is local-ish burning, not
    transported smoke, so it is never credited (section 11's guard).

    Returns {"path": [(lat, lon), ...], "arrival_hour": float | None,
    "travel_km": float} — the path starts at hour 0 and stops at the
    arrival step (or the horizon); travel_km is the haversine distance
    covered up to that point.
    """
    dt = config.MODEL["dt_seconds"]
    steps = config.MODEL["max_forecast_hours"]
    min_travel = config.MODEL["min_travel_km"]

    stamp = grid.get("t0") or grid["times"][0]
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    hour0 = (start_time - stamp).total_seconds() / 3600.0
    p_lat, p_lon = float(lat), float(lon)
    path = [(p_lat, p_lon)]
    travelled = 0.0

    for step in range(1, steps + 1):
        u, v = sample_wind(grid, p_lat, p_lon, hour0 + step - 1)
        n_lat = p_lat + v * dt / 111320.0
        n_lon = p_lon + u * dt / (111320.0 * math.cos(math.radians(p_lat)))
        travelled += haversine_km(p_lat, p_lon, n_lat, n_lon)
        p_lat, p_lon = n_lat, n_lon
        path.append((p_lat, p_lon))

        inside = haversine_km(p_lat, p_lon, target["lat"], target["lon"]) <= target["radius_km"]
        if inside:
            if travelled >= min_travel:
                return {"path": path, "arrival_hour": float(step), "travel_km": travelled}
            # First entry, too little travel: never credited. Stop here.
            break

    return {"path": path, "arrival_hour": None, "travel_km": travelled}


def smoke_load_index(contributions):
    """The 0-100 smoke-load index — unit-relative and uncalibrated; it
    ranks one situation against another, not against µg/m³ of anything.
    Saturates: approaches 100, never reaches it."""
    total = float(sum(contributions))
    if total <= 0.0:
        return 0.0
    return 100.0 * total / (total + config.MODEL["index_half_point"])


def index_band(index):
    """Severity band for the index, per the spec's thresholds."""
    if index >= 70:
        return "severe"
    if index >= 40:
        return "high"
    if index >= 15:
        return "moderate"
    if index > 0:
        return "low"
    return "clear"


def forecast_city(city_key, sources, grid, now):
    """One city's forecast object, in the frozen section-5 shape.

    sources are clustered cells (cluster_sources output). A cell inside
    the city radius is burning *there*: counted in local_fire_cells, never
    as an arrival. The index sums ALL arriving contributions; the arrivals
    list is sorted by arrival hour and capped at 12 for display.
    """
    city = config.CITIES[city_key]
    radius = city["radius_km"]

    nearest = None
    local = 0
    considered = 0
    arrivals = []

    for c in sources:
        d0 = haversine_km(city["lat"], city["lon"], c["lat"], c["lon"])
        nearest = d0 if nearest is None else min(nearest, d0)
        if d0 <= radius:
            local += 1  # burning inside the city — not transported smoke
            continue
        considered += 1
        run = advect(grid, c["lat"], c["lon"], now, city)
        if run["arrival_hour"] is None:
            continue
        contribution = c["frp"] * math.exp(-run["travel_km"] / config.MODEL["decay_length_km"])
        arrivals.append({
            "cell": c,
            "arrival_hour": run["arrival_hour"],
            "travel_km": run["travel_km"],
            "contribution": contribution,
            "path": run["path"],
        })

    arrivals.sort(key=lambda a: (a["arrival_hour"], -a["contribution"]))
    index = smoke_load_index([a["contribution"] for a in arrivals])
    band = index_band(index)
    first = arrivals[0] if arrivals else None

    shown = [
        {
            "source_lat": round(a["cell"]["lat"], 2),
            "source_lon": round(a["cell"]["lon"], 2),
            "frp": a["cell"]["frp"],
            "detections": a["cell"]["detections"],
            "arrival_hour": a["arrival_hour"],
            "travel_km": round(a["travel_km"], 1),
            "contribution": round(a["contribution"], 1),
            "path": [[round(la, 4), round(lo, 4)] for la, lo in a["path"]],
        }
        for a in arrivals[:12]
    ]

    return {
        "city": city_key,
        "name": city["name"],
        "lat": city["lat"],
        "lon": city["lon"],
        "smoke_load_index": round(index, 2),
        "band": band,
        "arriving_sources": len(arrivals),
        "local_fire_cells": local,
        "upwind_cells_considered": considered,
        "nearest_source_km": round(nearest, 1) if nearest is not None else None,
        "first_arrival_hours": first["arrival_hour"] if first else None,
        "first_arrival_at": (now + timedelta(hours=first["arrival_hour"])).isoformat() if first else None,
        "explain": _explain(city, arrivals, local, nearest),
        "arrivals": shown,
    }


def _explain(city, arrivals, local, nearest_km):
    """One plain sentence for the page, in the three spec'd cases."""
    name = city["name"]
    if arrivals:
        n = len(arrivals)
        hours = arrivals[0]["arrival_hour"]
        return (
            f"Smoke from {n} fire cell{'s' if n != 1 else ''} upwind is "
            f"forecast to reach {name} in about {hours:.0f} h."
        )
    if local:
        return (
            f"Fires are burning inside {name} itself ({local} fire cell{'s' if local != 1 else ''}); "
            "no smoke is being carried in."
        )
    if nearest_km is not None:
        return (
            f"Nothing is being carried toward {name} in the next "
            f"{config.MODEL['max_forecast_hours']} h; nearest fire cell is {nearest_km:.0f} km away."
        )
    return f"No fires detected in the source region; nothing is arriving at {name}."
