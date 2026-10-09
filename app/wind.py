"""Open-Meteo forecast wind ingestion and conversion to east/north flow."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import numpy as np

from .config import WIND


def _as_location_list(payload: Any, expected: int) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        locations = [payload]
    elif isinstance(payload, list):
        locations = payload
    else:
        raise ValueError("Unexpected Open-Meteo response")
    if len(locations) != expected:
        raise ValueError(f"Expected {expected} wind points, received {len(locations)}")
    return locations


def fetch_wind_grid() -> dict[str, Any]:
    """Fetch the configured 10 m wind grid and return hourly u/v arrays.

    Arrays have shape ``(hours, latitude points, longitude points)``;
    positive ``u`` is eastward and positive ``v`` is northward, in m/s.
    """
    lats = list(WIND["grid_lats"])
    lons = list(WIND["grid_lons"])
    point_count = len(lats) * len(lons)
    params = {
        "latitude": ",".join(map(str, [lat for lat in lats for _ in lons])),
        "longitude": ",".join(map(str, lons * len(lats))),
        "hourly": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "ms",
        "forecast_days": int(WIND["forecast_days"]),
        "timezone": "UTC",
    }
    response = httpx.get(WIND["endpoint"], params=params, timeout=30.0)
    response.raise_for_status()
    locations = _as_location_list(response.json(), point_count)

    times = locations[0]["hourly"]["time"]
    shape = (len(times), len(lats), len(lons))
    speed = np.empty(shape, dtype=float)
    direction = np.empty(shape, dtype=float)
    for point_index, location in enumerate(locations):
        row, col = divmod(point_index, len(lons))
        hourly = location["hourly"]
        if hourly["time"] != times:
            raise ValueError("Open-Meteo returned inconsistent hourly timestamps")
        speed[:, row, col] = np.asarray(hourly["wind_speed_10m"], dtype=float)
        direction[:, row, col] = np.asarray(hourly["wind_direction_10m"], dtype=float)

    radians = np.deg2rad(direction)
    u = -speed * np.sin(radians)
    v = -speed * np.cos(radians)
    parsed_times = [datetime.fromisoformat(value).replace(tzinfo=None) for value in times]
    return {
        "lats": lats,
        "lons": lons,
        "times": parsed_times,
        "u": u,
        "v": v,
        "height_m": int(WIND["height_m"]),
        "points": point_count,
        "hours": len(times),
    }
