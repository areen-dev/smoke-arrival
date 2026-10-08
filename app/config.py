"""Every shared constant and assumption for the project.

Data only - no logic in this file. Everyone imports from here, so that the
whole team uses identical numbers. If you need to change a value, say so in
the team chat first: these numbers decide what the model reports.

Each value is commented with what it means and why it is set this way.
"""

# --- Where the fires are -----------------------------------------------------
# The stubble-burning belt: Punjab, Haryana, western Uttar Pradesh.
# Detections outside this box are ignored, because smoke from elsewhere is
# not what we are forecasting.
SOURCE_BBOX = {"min_lat": 28.5, "max_lat": 32.6, "min_lon": 73.0, "max_lon": 78.5}

# --- The cities we forecast for ----------------------------------------------
# radius_km is how close a particle must get to count as "arrived".
# A bigger city gets a bigger radius. These are judgement calls, not facts.
CITIES = {
    "delhi":      {"name": "Delhi NCR",  "lat": 28.6139, "lon": 77.2090, "radius_km": 45},
    "gurugram":   {"name": "Gurugram",   "lat": 28.4595, "lon": 77.0266, "radius_km": 20},
    "noida":      {"name": "Noida",      "lat": 28.5355, "lon": 77.3910, "radius_km": 20},
    "chandigarh": {"name": "Chandigarh", "lat": 30.7333, "lon": 76.7794, "radius_km": 25},
    "ludhiana":   {"name": "Ludhiana",   "lat": 30.9010, "lon": 75.8573, "radius_km": 25},
    "amritsar":   {"name": "Amritsar",   "lat": 31.6340, "lon": 74.8723, "radius_km": 25},
    "jaipur":     {"name": "Jaipur",     "lat": 26.9124, "lon": 75.7873, "radius_km": 30},
    "lucknow":    {"name": "Lucknow",    "lat": 26.8467, "lon": 80.9462, "radius_km": 30},
}

# --- The model's assumptions -------------------------------------------------
MODEL = {
    # How far we move a particle each step. 1 hour keeps the arithmetic
    # simple and matches the wind data's resolution.
    "dt_seconds": 3600,
    # How far ahead we look. 48 hours is long enough for smoke to cross
    # from Punjab to Delhi in typical winds.
    "max_forecast_hours": 48,
    # Smoke thins out as it travels. This is the distance over which its
    # contribution decays to about a third. Larger = smoke stays relevant
    # for longer. This is an assumption, not a measurement.
    "decay_length_km": 300.0,
    # The contribution at which the 0-100 index reads 50. Tune this FIRST
    # if the index looks badly scaled.
    "index_half_point": 4000.0,
    # A fire inside a city is burning THERE, not blowing in. Anything
    # closer than this to the target is reported as "local" and is never
    # counted as transported smoke.
    "min_travel_km": 50.0,
}

# --- Where the fire data comes from ------------------------------------------
FIRMS = {
    "base": "https://firms.modaps.eosdis.nasa.gov/data/active_fire",
    # Public CSV files, no API key needed. {window} is "24h", "48h" or "7d".
    "sensors": {
        "viirs_npp":    "suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_South_Asia_{window}.csv",
        "viirs_noaa20": "noaa-20-viirs-c2/csv/J1_VIIRS_C2_South_Asia_{window}.csv",
        "viirs_noaa21": "noaa-21-viirs-c2/csv/J2_VIIRS_C2_South_Asia_{window}.csv",
        "modis":        "modis-c6.1/csv/MODIS_C6_1_South_Asia_{window}.csv",
    },
    "window": "24h",
    # VIIRS reports confidence as low/nominal/high. MODIS reports 0-100.
    # Low-confidence detections are usually not real fires.
    "keep_confidence": ["nominal", "high"],
    "modis_min_confidence": 60,
    # Detections are snapped to this grid. One cell = one fire source, so we
    # advect tens of cells instead of thousands of individual detections.
    "cell_deg": 0.25,
}

# --- Where the wind comes from -----------------------------------------------
WIND = {
    "endpoint": "https://api.open-meteo.com/v1/forecast",
    # A coarse grid covering the burning belt and the cities downwind of it.
    # Wind is smooth over these distances, so a coarse grid is enough.
    "grid_lats": [26.0, 28.0, 30.0, 32.0, 34.0],
    "grid_lons": [72.0, 74.0, 76.0, 78.0, 80.0, 82.0],
    "forecast_days": 3,
    # 10 metres above ground. Smoke aloft travels on 850 hPa, but that is not
    # freely available as JSON. Stated as a known limitation in the README.
    "height_m": 10,
}

# --- The archive endpoint, used only by the hindcast -------------------------
# Same provider, same shape, but for past dates. This is what lets us replay
# a known bad day.
ARCHIVE_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"

# --- Serving -----------------------------------------------------------------
# How long a computed forecast is reused before being recomputed. FIRMS is
# refreshed a few times a day, so 15 minutes is generous and keeps the API fast.
CACHE_TTL_SECONDS = 900
