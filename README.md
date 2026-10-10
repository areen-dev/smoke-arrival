# Smoke Arrival

Forecasts **when** crop-residue burning smoke reaches a city — as a time,
not a colour on a map.

Built for Environmental Hacks (WeMakeDevs × AWS, 8–11 October 2026), Track: Air.
Team of three.

## The problem

Every October, farmers in Punjab and Haryana burn paddy residue. Delhi sits
250–400 km downwind and breathes it. Existing tools report air quality
**after** the smoke has arrived. They measure; they do not forecast. Nobody
can tell a school or someone with asthma: "smoke reaches you at 7pm tomorrow,
for a four-hour window."

## What it does

Takes public satellite fire detections (NASA FIRMS) and wind (Open-Meteo
forecast, or the archive for a past date), carries every burning area forward
through the wind field, and reports per city:

- **when** smoke arrives — a clock time and hours from now
- **the path** it took to get there, drawn on the map
- **a relative 0–100 smoke-load index**, ranked between cities

Plus the fires themselves, sized by intensity, so the cause and the effect sit
on one screen.

## How it works

1. Four FIRMS sensors are downloaded, merged, de-duplicated, and snapped to
   0.25° cells. Each cell becomes one source, carrying the summed fire
   radiative power of every detection inside it.
2. Hourly wind on a 5×6 grid is fetched and converted to u/v components
   (direction is where wind blows *from*, so the sign flips).
3. Every source is carried forward through the wind in 1-hour steps, up to
   48 hours. Smoke thins with distance as it travels.
4. **Arrival** is the first hour a particle enters a city's radius. The index
   weights each arrival by fire power and by how far it travelled.
5. One JSON document comes out of the API; the page renders it.

## Reading the output honestly

- The index is **relative, not a concentration**. It ranks cities and days
  against each other; it is not µg/m³, and the JSON says so in `model_note`.
- Wind is the 10 m forecast, not the wind at the height smoke travels.
- The source region **deliberately includes Pakistani Punjab**. Smoke does not
  stop at a border, and pretending it does would flatter the model.
- On a calm day the page says *clear* everywhere. That is the correct answer,
  not a bug.

## The hindcast

The same pipeline replays a past date:

```bash
.venv/bin/python scripts/hindcast.py 2025-11-05
```

That matters because today may be a quiet day. The hindcast shows the model on
a day when the burning was bad — the event the tool exists for.

## Where AWS fits

FastAPI served by **AWS Lambda behind a Function URL**, region `ap-south-1`,
via Mangum. One function, one URL, no containers. The bundle is built for
Lambda's platform — `x86_64` manylinux wheels, Python 3.13 — by
`scripts/package_lambda.sh`, so the deploy cannot silently depend on the
build machine.

Chosen because it is the smallest thing that satisfies "deployed on AWS" and
it stays inside the always-free tier. CloudWatch collects the logs.

## Run locally

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000     # then open http://localhost:8000
.venv/bin/python -m pytest tests -q            # 18 tests, no network needed
```

The historical FIRMS API needs a free `FIRMS_MAP_KEY` in the environment. The
live pipeline needs no keys at all.

## Status

The pipeline runs end to end on live data: fires in, wind in, arrivals out,
served by the API and rendered by the page. The 18 model tests pass. The
deploy to Lambda is the remaining step.

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

Disclosed as the event rules require. Every member used an assistant, and each
is named here.

- **Seat A — data and deploy (Sagar):** Codex, by OpenAI — ingestion modules,
  the archive fetchers, Lambda packaging and the deploy
- **Seat B — model, integration, writeup (Areen):** Hermes, by Nous Research —
  code, tests, review, and this document
- **Seat C — API and page (Ayesha):** Claude

## Repository history

Every commit falls inside the event window, 8–11 October 2026.

## Demo video

<!-- paste the YouTube link here before submitting — must show AWS -->
