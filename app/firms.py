"""NASA FIRMS active-fire data ingestion."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import csv
from datetime import date as calendar_date
from io import StringIO
import os
from typing import Iterable
from urllib.parse import quote

import httpx

from .config import FIRMS, SOURCE_BBOX


@dataclass(frozen=True)
class Fire:
    """One FIRMS detection, retaining the source fields used downstream."""

    lat: float
    lon: float
    frp: float
    confidence: str | int | float
    acq_date: str
    acq_time: str
    satellite: str


def _confidence_allowed(sensor: str, confidence: str | int | float) -> bool:
    if sensor == "modis":
        try:
            return float(confidence) >= float(FIRMS["modis_min_confidence"])
        except (TypeError, ValueError):
            return False
    value = str(confidence).strip().lower()
    # Standard Processing archive CSVs use single-letter confidence codes.
    value = {"l": "low", "n": "nominal", "h": "high"}.get(value, value)
    return value in FIRMS["keep_confidence"]


def _parse_csv(text: str, sensor: str) -> list[Fire]:
    fires: list[Fire] = []
    reader = csv.DictReader(StringIO(text.lstrip("\ufeff")))
    required = {"latitude", "longitude", "frp", "confidence", "acq_date", "acq_time", "satellite"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError(f"Unexpected FIRMS CSV columns for {sensor}")

    for row in reader:
        confidence: str | int | float = (row.get("confidence") or "").strip()
        if sensor != "modis":
            confidence = {"l": "low", "n": "nominal", "h": "high"}.get(
                str(confidence).lower(), confidence
            )
        if not _confidence_allowed(sensor, confidence):
            continue
        try:
            fire = Fire(
                lat=float(row["latitude"]),
                lon=float(row["longitude"]),
                frp=float(row["frp"]),
                confidence=confidence,
                acq_date=row["acq_date"].strip(),
                acq_time=row["acq_time"].strip().zfill(4),
                satellite=row["satellite"].strip(),
            )
        except (TypeError, ValueError):
            continue
        if fire.frp >= 0:
            fires.append(fire)
    return fires


def _fetch_sensor(client: httpx.Client, sensor: str, path_template: str, window: str) -> list[Fire]:
    url = f"{FIRMS['base']}/{path_template.format(window=window)}"
    response = client.get(url)
    response.raise_for_status()
    return _parse_csv(response.text, sensor)


def _deduplicate(fires: Iterable[Fire]) -> list[Fire]:
    """Collapse same-time, effectively co-located cross-sensor detections."""
    by_key: dict[tuple[float, float, str, str], Fire] = {}
    confidence_rank = {"low": 0, "nominal": 1, "high": 2}
    for fire in fires:
        key = (round(fire.lat, 4), round(fire.lon, 4), fire.acq_date, fire.acq_time)
        old = by_key.get(key)
        if old is None:
            by_key[key] = fire
            continue
        old_rank = confidence_rank.get(str(old.confidence).lower(), 0)
        new_rank = confidence_rank.get(str(fire.confidence).lower(), 0)
        if (new_rank, fire.frp) > (old_rank, old.frp):
            by_key[key] = fire
    return list(by_key.values())


def fetch_fires(window: str | None = None) -> list[Fire]:
    """Fetch and merge the four public FIRMS CSV feeds.

    ``window`` is ``24h``, ``48h`` or ``7d``. Low-confidence VIIRS records
    and MODIS records below the configured threshold are discarded.
    """
    window = window or str(FIRMS["window"])
    if window not in {"24h", "48h", "7d"}:
        raise ValueError("window must be one of: 24h, 48h, 7d")

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=len(FIRMS["sensors"])) as pool:
            futures = [
                pool.submit(_fetch_sensor, client, sensor, path, window)
                for sensor, path in FIRMS["sensors"].items()
            ]
            records = [fire for future in futures for fire in future.result()]
    return _deduplicate(records)


def _fetch_archive_sensor(
    client: httpx.Client, sensor: str, source: str, area: str, day: str, map_key: str
) -> list[Fire]:
    url = (
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"{quote(map_key, safe='')}/{source}/{area}/1/{day}"
    )
    response = client.get(url)
    response.raise_for_status()
    return _parse_csv(response.text, sensor)


def fetch_fires_archive(date: str) -> list[Fire]:
    """Fetch and merge one historical day of FIRMS Standard Processing data.

    A free NASA FIRMS MAP_KEY must be provided in the ``FIRMS_MAP_KEY``
    environment variable. The key is read at runtime and is never stored here.
    """
    try:
        day = calendar_date.fromisoformat(date).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("date must be in YYYY-MM-DD format") from exc
    if day != date:
        raise ValueError("date must be in YYYY-MM-DD format")

    map_key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not map_key:
        raise RuntimeError(
            "Set FIRMS_MAP_KEY in the environment to use historical FIRMS data"
        )

    # Archive products cover the same source region used by the live feeds.
    area = ",".join(
        str(SOURCE_BBOX[key])
        for key in ("min_lon", "min_lat", "max_lon", "max_lat")
    )
    sources = {
        "viirs_npp": "VIIRS_SNPP_SP",
        "viirs_noaa20": "VIIRS_NOAA20_SP",
        "viirs_noaa21": "VIIRS_NOAA21_SP",
        "modis": "MODIS_SP",
    }
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            futures = [
                pool.submit(_fetch_archive_sensor, client, sensor, source, area, day, map_key)
                for sensor, source in sources.items()
            ]
            records = [fire for future in futures for fire in future.result()]
    return _deduplicate(records)


def in_source_region(fires: Iterable[Fire]) -> list[Fire]:
    """Keep detections inside the configured Punjab/Haryana source box."""
    return [
        fire for fire in fires
        if SOURCE_BBOX["min_lat"] <= fire.lat <= SOURCE_BBOX["max_lat"]
        and SOURCE_BBOX["min_lon"] <= fire.lon <= SOURCE_BBOX["max_lon"]
    ]


