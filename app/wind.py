"""Open-Meteo forecast wind ingestion and conversion to east/north flow."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import numpy as np

from .config import WIND


def _bracket(coordinates: list[float], value: float) -> tuple[int, int, float]:
    """Find the bounding coordinates and interpolation fraction, clamping edges."""
    points = np.asarray(coordinates, dtype=float)
    if points.ndim != 1 or len(points) == 0:
        raise ValueError("wind grid coordinates must be non-empty one-dimensional lists")
    if len(points) == 1:
        return 0, 0, 0.0
    if np.any(np.diff(points) <= 0):
        raise ValueError("wind grid coordinates must be strictly increasing")
    if value <= points[0]:
        return 0, 1, 0.0
    if value >= points[-1]:
        return len(points) - 2, len(points) - 1, 1.0
    upper = int(np.searchsorted(points, value, side="right"))
    lower = upper - 1
    fraction = float((value - points[lower]) / (points[upper] - points[lower]))
    return lower, upper, fraction


def sample_wind(
    grid: dict[str, Any], lat: float, lon: float, hour_offset: float = 0.0
) -> tuple[float, float]:
    """Sample ``(u, v)`` at a location and fractional hour offset.

    Wind components are bilinearly interpolated in space and linearly
    interpolated in time. Coordinates and times outside the grid are clamped
    to the nearest edge.
    """
    lats = list(grid["lats"])
    lons = list(grid["lons"])
    times = grid["times"]
    if not times:
        raise ValueError("wind grid must contain at least one time step")

    u = np.asarray(grid["u"], dtype=float)
    v = np.asarray(grid["v"], dtype=float)
    expected_shape = (len(times), len(lats), len(lons))
    if u.shape != expected_shape or v.shape != expected_shape:
        raise ValueError(f"wind components must have shape {expected_shape}")

    lat0, lat1, lat_weight = _bracket(lats, float(lat))
    lon0, lon1, lon_weight = _bracket(lons, float(lon))

    offset = min(max(float(hour_offset), 0.0), len(times) - 1)
    time0 = int(np.floor(offset))
    time1 = min(time0 + 1, len(times) - 1)
    time_weight = offset - time0

    def bilinear(values: np.ndarray, time_index: int) -> float:
        field = values[time_index]
        return float(
            field[lat0, lon0] * (1 - lat_weight) * (1 - lon_weight)
            + field[lat1, lon0] * lat_weight * (1 - lon_weight)
            + field[lat0, lon1] * (1 - lat_weight) * lon_weight
            + field[lat1, lon1] * lat_weight * lon_weight
        )

    sampled_u = bilinear(u, time0) * (1 - time_weight) + bilinear(u, time1) * time_weight
    sampled_v = bilinear(v, time0) * (1 - time_weight) + bilinear(v, time1) * time_weight
    return float(sampled_u), float(sampled_v)


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

