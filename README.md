# Bangkok Weather & Water sites

Five live pages plus a hub, on GitHub Pages (user `thallendan`). Data is collected by scheduled jobs in GitHub
Actions and AWS, written to JSON files, and read by the pages in the browser. This file says what writes what.

| Page | Repo | Reads |
|---|---|---|
| Hub `index.html` | BKK-Rain | `status.json` |
| Macro climate `macro.html` | BKK-Rain | `data.json` |
| Monsoon winds `BKKmonsoon.html` | BKK-Rain | `monsoon.json` |
| Rain gauges `BKKraingaugegrid.html` | BKK-Rain | `bma-rain-latest.json`, `outlying-rain-latest.json` (CloudFront) |
| HydroView `HydroView.html` | BKK-Canal | `data.json`, `monsoon.json`, `klongmap-latest.json`, `master_water_level_data.json` |
| Klongs `localKlongsV.html` | BKK-Canal | `klongmap-latest.json` (CloudFront) |

Shared by all pages (BKK-Rain): `bkk-nav.js` (nav bar and light/dark button), `bkk.css` (shared look),
`bkk-tokens.css` (colour variables; imported by `bkk.css`, deploy it first).

## Who writes each data file

| File | Written by | Schedule |
|---|---|---|
| `data.json` | `fetch_data.py`, workflow `update_data.yml` ("Sync Thailand Climate Teleconnections") | every 30 min (cron-job.org), backup GitHub cron every 6 h |
| `monsoon.json` | `fetch_monsoon.py`, workflow `monsoon.yml` ("Update monsoon data") | hourly (cron-job.org), backup every 3 h |
| `status.json` | `status.py`, workflow `status.yml` ("Build status.json") | every 5 min (cron-job.org), backup GitHub cron |
| `bma-rain-latest.json`, `bma-rain-history.json` | AWS Lambda for BMA rain gauges | EventBridge, every 5 min |
| `road-flood-latest.json` | Same master Lambda: every BMA roadside flood sensor with coordinates and depth | every master run |
| `outlying-rain-latest.json` | AWS Lambda for outlying rain | EventBridge, every 10 min |
| `klongmap-latest.json` | AWS Lambda for the klong map | check its EventBridge rule |
| `master_water_level_data.json` | AWS Lambda `fetch_master_water_level` | check its EventBridge rule |

AWS files live in one S3 bucket (region ap-southeast-7, Thailand) and are served by CloudFront. GitHub workflows are
started on time by **cron-job.org**, which calls GitHub's `workflow_dispatch` API with a fine-grained token (only
BKK-Rain, Actions read/write; renew it before it expires). GitHub's own scheduler is only the backup, because it often
runs late or skips runs.

All three workflows push to `main`, so each one rebases and retries before pushing, and each has its own
`concurrency` group so two copies never run at once.

## What the hub shows

`status.py` reads `data.json`, `monsoon.json` and the CloudFront files and writes `status.json`: a level per site
(normal, watch, warning, critical, or no current data), a one-line headline, details, `level_since_utc`, and
`highlights` (the numbers in the "right now" strip). A site whose data is older than `max_age_min` is shown grey, and
the hub ignores it in the overall banner. Staleness limits and thresholds are at the top of `status.py`.

## `data.json` sources (`fetch_data.py`)

NOAA CPC (ENSO, SOI, OLR, CPAC850, Pacific heat content), Australian BoM (MJO), NOAA PSL (IOD), HII/RID dam report,
and the Thaiwater water-level API (`GLF001` Pak Nam tide, plus river stations `C.2`, `C.13`, `CPY011`). The Navy
predicted tide (`fews2.hii.or.th/.../tide_table/summary.txt`) is optional, and so is the ThaiWater `analyst/dam` API (the data behind waterchart.thaiwater.net). Its `dam_daily` rows give
each dam's daily inflow and release in **million m3 per day**, storage, % full and spill, saved under `dams.<dam>.flow`
(plus `net_mcm_day` = inflow minus release, and the water level from the hourly rows). HII's report stays the first source
for % full; the API fills in when HII has nothing. Each run writes `source_health`; look
there first when a number is missing. Slow sources (indices, dams) keep their last good value for a limited time,
marked `carried_from_utc`. The live tide is never carried over.

## The master Lambda (`aws/lambda_fetch_master_water_level.py`)

Writes `master_water_level_data.json`: key ThaiWater stations (tide gauge `GLF001`, river `C.2`, `C.13`, `CPY011`),
watergate counts, and the BMA roadside flood sensors that are wet. Sources run in parallel, and a source that fails
3 times in a row is skipped for 30 min, then 2 h, 6 h, 24 h, and retried by itself (state is in `source_health` in the
file). Dead sources were removed; `retired_sources` in the file says why (the old `chaophraya_full` page timed out,
the dam and tide pages returned nothing usable or "Request Rejected", and three BMA endpoints were 404). Dams and the
Pak Nam tide come from `data.json`. The code is kept here as a copy; the Lambda itself is deployed in AWS.

## Deploying changes

1. `bkk-tokens.css`, then `bkk.css` and `bkk-nav.js`.
2. Pages (BKK-Rain, then BKK-Canal).
3. `.py` files and workflows. After changing a workflow, run it once by hand from the Actions tab.

## When something looks wrong

- **Hub card grey:** its data is old. Open that site's file (table above) and check its timestamp, then the workflow or
  Lambda that writes it.
- **Macro card says "N/9 sources current":** open `data.json`, `source_health`.
- **Workflow emails:** open the failed run and expand the failing step. A rejected push means two workflows collided.
- **Cron-job.org returns 401/403:** the GitHub token expired or lost access.
