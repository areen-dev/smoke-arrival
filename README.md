# Smoke Arrival

Forecasts **when** crop-residue burning smoke reaches a city — as a time,
not a colour on a map.

Built for Environmental Hacks (WeMakeDevs × AWS, 8–11 October 2026), Track: Air.

## The problem

Every October, farmers in Punjab and Haryana burn paddy residue. Delhi sits
250–400 km downwind and breathes it. Existing tools report air quality
**after** the smoke has arrived. They measure; they do not forecast. Nobody
can tell a school or someone with asthma: "smoke reaches you at 7pm tomorrow,
for a four-hour window."

## What it does

Takes public satellite fire detections (NASA FIRMS) and forecast wind
(Open-Meteo), carries each burning area forward through the wind, and reports
the **arrival time** and path per city, plus a relative 0–100 smoke-load index.

## Stack

Python 3.13 · FastAPI · Mangum · httpx · numpy · pytest · uv ·
Leaflet (vendored, not from a CDN) · AWS Lambda + Function URL · CloudWatch

## Status

Skeleton only. Nothing is implemented yet — see AGENTS.md for the build plan,
the frozen JSON contract, and which files you own.

## Run

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000    # once app/main.py exists
.venv/bin/python -m pytest tests -q
```

## Cost

Runs inside the AWS Free Tier. Lambda gives 1 million requests and 400,000
GB-seconds of compute per month, and CloudWatch gives 10 custom metrics and
10 alarms — both always-free allowances, not a trial. No database, no queue,
no storage, so nothing else is billed.

## Credits and licences

- Leaflet 1.9.4 — BSD 2-Clause, vendored at `static/vendor/leaflet/`
- FastAPI (MIT), Mangum (MIT), httpx (BSD-3-Clause), numpy (BSD-3-Clause)
- Fire data: NASA FIRMS — open data, attribution requested
- Wind: Open-Meteo — CC BY 4.0, attribution required

## AI tools used

Disclose every AI tool used, as the event rules require.

- <!-- each member: add the assistant you used -->
