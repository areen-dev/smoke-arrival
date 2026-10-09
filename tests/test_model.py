"""Tests of the model maths only: clustering, wind sampling, advection,
arrival, the index. All inputs are synthetic, so the suite runs offline
while Sagar's FIRMS fixture is still missing.

Written test-first: each test fails before its function exists, and each
would fail if the maths were wrong — not just if the code raised.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.model import (
    advect,
    cluster_sources,
    forecast_city,
    index_band,
    smoke_load_index,
)
from app.wind import sample_wind

T0 = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)


@dataclass
class Fire:
    """Stand-in for Sagar's FIRMS record: same fields, synthetic values."""

    lat: float
    lon: float
    frp: float
    acq_date: str = "2026-10-08"
    acq_time: str = "0800"
    confidence: str = "nominal"
    satellite: str = "N20"


def make_grid(u=0.0, v=0.0, hours=72, t0=T0,
              lats=(26.0, 28.0, 30.0, 32.0, 34.0),
              lons=(72.0, 74.0, 76.0, 78.0, 80.0, 82.0)):
    """A synthetic uniform wind grid: constant u/v at every point and hour.

    Built in the shape app/wind.py returns: "times" as hourly timestamps,
    and u/v as (hours, lat, lon). t0 stays a parameter for readability.
    """
    return {
        "lats": list(lats),
        "lons": list(lons),
        "times": [t0 + timedelta(hours=h) for h in range(hours)],
        "hours": hours,
        "u": np.full((hours, len(lats), len(lons)), float(u)),
        "v": np.full((hours, len(lats), len(lons)), float(v)),
    }


def cell(lat, lon, frp, detections=13, newest="2026-10-08 0839"):
    """One clustered source cell, as cluster_sources returns them."""
    return {"lat": lat, "lon": lon, "frp": frp,
            "detections": detections, "newest": newest}


# --- cluster_sources ---------------------------------------------------------

def test_cluster_sums_frp_of_fires_in_the_same_cell():
    fires = [
        Fire(lat=30.61, lon=74.88, frp=20.0, acq_time="0805"),
        Fire(lat=30.64, lon=74.86, frp=33.3, acq_time="0839"),
    ]
    cells = cluster_sources(fires, cell_deg=0.25)

    assert len(cells) == 1
    assert cells[0]["lat"] == pytest.approx(30.625, abs=0.01)  # centre of the cell
    assert cells[0]["lon"] == pytest.approx(74.875, abs=0.01)
    assert cells[0]["frp"] == pytest.approx(53.3)
    assert cells[0]["detections"] == 2
    assert cells[0]["newest"] == "2026-10-08 0839"


def test_cluster_keeps_fires_in_different_cells_separate():
    fires = [
        Fire(lat=30.61, lon=74.88, frp=20.0),
        Fire(lat=30.90, lon=75.85, frp=5.0),
    ]
    cells = cluster_sources(fires, cell_deg=0.25)

    assert len(cells) == 2
    assert [c["frp"] for c in cells] == [20.0, 5.0]  # biggest first


def test_cluster_pads_short_acq_times_before_comparing():
    # "930" must sort before "1105" once padded to "0930".
    fires = [
        Fire(lat=30.61, lon=74.88, frp=1.0, acq_time="930"),
        Fire(lat=30.62, lon=74.89, frp=1.0, acq_time="1105"),
    ]
    cells = cluster_sources(fires, cell_deg=0.25)

    assert cells[0]["newest"] == "2026-10-08 1105"


def test_cluster_accepts_dict_records_like_the_fetch_layer_might_return():
    fires = [{"lat": 30.61, "lon": 74.88, "frp": 7.0}]
    cells = cluster_sources(fires, cell_deg=0.25)

    assert cells[0]["frp"] == 7.0


# --- sample_wind -------------------------------------------------------------

def test_sample_wind_interpolates_bilinearly_in_space():
    grid = make_grid(hours=2, lats=(0.0, 1.0), lons=(0.0, 1.0))
    grid["u"][:] = [[0.0, 2.0], [2.0, 4.0]]  # u[lat][lon]

    u, v = sample_wind(grid, 0.25, 0.25, hour_offset=0.0)
    assert u == pytest.approx(1.0)  # corners 0, 2, 2, 4 at weight 0.25 each
    assert v == pytest.approx(0.0)

    u, _ = sample_wind(grid, 0.5, 0.5, hour_offset=0.0)
    assert u == pytest.approx(2.0)  # the mean of the four corners


def test_sample_wind_interpolates_linearly_in_time_and_clamps():
    grid = make_grid(hours=2, lats=(0.0, 1.0), lons=(0.0, 1.0))
    grid["u"][1] = 10.0  # second hour: 10 m/s everywhere, first hour stays 0

    u, _ = sample_wind(grid, 0.5, 0.5, hour_offset=0.5)
    assert u == pytest.approx(5.0)
    u, _ = sample_wind(grid, 0.5, 0.5, hour_offset=0.25)
    assert u == pytest.approx(2.5)
    u, _ = sample_wind(grid, 0.5, 0.5, hour_offset=-5)
    assert u == pytest.approx(0.0)  # clamped to the first hour
    u, _ = sample_wind(grid, 0.5, 0.5, hour_offset=99)
    assert u == pytest.approx(10.0)  # clamped to the last hour


# --- advect ------------------------------------------------------------------

def test_advect_drifts_with_the_wind():
    grid = make_grid(u=5.0, v=0.0)
    target = {"lat": 31.0, "lon": 85.0, "radius_km": 25}  # far away, never hit

    result = advect(grid, 28.6139, 77.2090, start_time=T0, target=target)

    assert result["arrival_hour"] is None
    assert len(result["path"]) == 49  # hour 0 through 48 — the full horizon
    lon_step = 5.0 * 3600 / (111320 * math.cos(math.radians(28.6139)))
    assert result["path"][1][0] == pytest.approx(28.6139, abs=1e-6)  # v = 0
    assert result["path"][1][1] == pytest.approx(77.2090 + lon_step, abs=1e-4)


def test_advect_finds_arrival_in_the_target_radius():
    grid = make_grid(u=10.0, v=0.0)  # 36 km per hour, due east
    target = {"lat": 28.6139, "lon": 77.2090, "radius_km": 45}  # Delhi
    source = (28.6139, 75.5)  # ~167 km west of the centre

    result = advect(grid, source[0], source[1], start_time=T0, target=target)

    assert result["arrival_hour"] == 4.0
    assert result["travel_km"] == pytest.approx(144.0, abs=3.0)
    assert len(result["path"]) == 5  # hour 0 through 4, then stop


def test_advect_refuses_fires_that_barely_move_before_reaching_the_city():
    # A fire 46 km from the centre — just outside Delhi's 45 km radius —
    # drifting gently in. It enters the radius after 9 km, under the 50 km
    # minimum travel, so it is never credited as transported smoke.
    grid = make_grid(u=2.5, v=0.0)
    target = {"lat": 28.6139, "lon": 77.2090, "radius_km": 45}
    lon_offset = 46.0 / (111320 * math.cos(math.radians(28.6139)))  # km -> deg
    source_lon = 77.2090 - lon_offset

    result = advect(grid, 28.6139, source_lon, start_time=T0, target=target)

    assert result["arrival_hour"] is None


# --- smoke_load_index --------------------------------------------------------

def test_smoke_load_index_follows_the_half_point_formula():
    assert smoke_load_index([]) == 0.0
    assert smoke_load_index([4000.0]) == pytest.approx(50.0)  # the half point
    assert smoke_load_index([4000.0, 4000.0]) == pytest.approx(100 * 8000 / 12000)


def test_smoke_load_index_never_reaches_100():
    assert 99 < smoke_load_index([10**9]) < 100


# --- index_band --------------------------------------------------------------

def test_index_band_thresholds():
    assert index_band(0) == "clear"
    assert index_band(0.1) == "low"
    assert index_band(14.9) == "low"
    assert index_band(15) == "moderate"
    assert index_band(39.9) == "moderate"
    assert index_band(40) == "high"
    assert index_band(69.9) == "high"
    assert index_band(70) == "severe"
    assert index_band(100) == "severe"


# --- forecast_city -----------------------------------------------------------

def test_forecast_city_reports_an_arriving_source():
    grid = make_grid(u=10.0, v=0.0)  # 36 km per hour, due east
    sources = [cell(28.6139, 75.5, frp=300.0)]  # ~167 km west of Delhi's centre

    obj = forecast_city("delhi", sources, grid, now=T0)

    assert obj["city"] == "delhi"
    assert obj["smoke_load_index"] == pytest.approx(4.4, abs=0.3)
    assert obj["band"] == "low"
    assert obj["arriving_sources"] == 1
    assert obj["local_fire_cells"] == 0
    assert obj["upwind_cells_considered"] == 1
    assert obj["nearest_source_km"] == pytest.approx(166.8, abs=2)
    assert obj["first_arrival_hours"] == 4.0
    assert obj["first_arrival_at"] == (T0 + timedelta(hours=4)).isoformat()
    assert "reach" in obj["explain"]

    assert len(obj["arrivals"]) == 1
    a = obj["arrivals"][0]
    assert a["source_lat"] == pytest.approx(28.6139, abs=0.01)
    assert a["source_lon"] == pytest.approx(75.5, abs=0.01)
    assert a["frp"] == 300.0
    assert a["detections"] == 13
    assert a["arrival_hour"] == 4.0
    assert a["travel_km"] == pytest.approx(143.8, abs=3.0)
    assert a["contribution"] == pytest.approx(300 * math.exp(-144 / 300), rel=0.01)
    assert len(a["path"]) == 5
    assert a["path"][0] == pytest.approx([28.6139, 75.5], abs=0.01)


def test_forecast_city_counts_a_fire_inside_the_city_as_local():
    grid = make_grid(u=5.0, v=0.0)
    sources = [cell(28.62, 77.20, frp=500.0)]  # ~1 km from Delhi's centre

    obj = forecast_city("delhi", sources, grid, now=T0)

    assert obj["local_fire_cells"] == 1
    assert obj["arriving_sources"] == 0
    assert obj["arrivals"] == []
    assert obj["first_arrival_hours"] is None
    assert obj["first_arrival_at"] is None
    assert obj["smoke_load_index"] == 0.0
    assert obj["band"] == "clear"
    assert "inside" in obj["explain"]


def test_forecast_city_reports_nothing_when_the_wind_blows_away():
    grid = make_grid(u=-5.0, v=-5.0)  # south-west, away from Delhi
    sources = [cell(31.63, 74.87, frp=80.0)]  # Amritsar side

    obj = forecast_city("delhi", sources, grid, now=T0)

    assert obj["arriving_sources"] == 0
    assert obj["first_arrival_hours"] is None
    assert obj["smoke_load_index"] == 0.0
    assert obj["band"] == "clear"
    assert obj["nearest_source_km"] == pytest.approx(403.8, abs=10)
    assert "nearest" in obj["explain"]


def test_forecast_city_with_no_fires_at_all():
    obj = forecast_city("delhi", [], make_grid(), now=T0)

    assert obj["arriving_sources"] == 0
    assert obj["upwind_cells_considered"] == 0
    assert obj["nearest_source_km"] is None
    assert obj["band"] == "clear"
    assert "No fires detected" in obj["explain"]


def test_forecast_city_sorts_arrivals_and_sums_them_into_the_index():
    grid = make_grid(u=10.0, v=0.0)
    sources = [
        cell(28.6139, 75.5, frp=300.0),   # arrives at hour 4
        cell(28.6139, 74.5, frp=100.0),   # ~264 km out, arrives at hour 7
    ]

    obj = forecast_city("delhi", sources, grid, now=T0)

    assert [a["arrival_hour"] for a in obj["arrivals"]] == [4.0, 7.0]
    assert obj["arriving_sources"] == 2
    total = sum(a["contribution"] for a in obj["arrivals"])
    assert obj["smoke_load_index"] == pytest.approx(5.4, abs=0.3)
    assert obj["smoke_load_index"] == pytest.approx(100 * total / (total + 4000), abs=0.05)


def test_forecast_city_caps_the_arrivals_list_at_twelve():
    grid = make_grid(u=10.0, v=0.0)
    sources = [cell(28.6139, 74.2 + 0.1 * i, frp=10.0) for i in range(13)]

    obj = forecast_city("delhi", sources, grid, now=T0)

    assert len(obj["arrivals"]) == 12
    assert obj["arriving_sources"] == 13
    assert obj["smoke_load_index"] > 0
