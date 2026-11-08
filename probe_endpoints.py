#!/usr/bin/env python3
"""
probe_endpoints.py
Probe every candidate data endpoint and record what is reachable from WHERE
this script runs (egress IP/country matter: some Thai gov hosts block foreign IPs).

Usage
  python probe_endpoints.py out.json            # run probe, write report
  python probe_endpoints.py --compare a.json b.json   # table of differences

AWS Lambda: set handler to probe_endpoints.handler; optional env S3_BUCKET,
S3_KEY (default probe/<label>.json), PROBE_LABEL (default "lambda-th").
Stdlib only, so no layers needed.
"""
import json, re, ssl, sys, time, os, urllib.request, urllib.error
from datetime import datetime, timezone

BKK = "latitude=13.75&longitude=100.5&timezone=Asia%2FBangkok"
ENDPOINTS = [
    # --- already in your repo ---
    ("enso", "https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices"),
    ("mjo", "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt"),
    ("iod", "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.data"),
    ("hii_dam", "https://tiwrmdev.hii.or.th/DATA/REPORT/php/rid_dam_1.php?lang=en"),
    ("hii_sealevel", "https://tiwrmdev.hii.or.th/v3/sealevel"),
    ("hii_chaophraya_full", "https://tiwrm.hii.or.th/DATA/REPORT/php/chart/chaopraya/2013/chaopraya.php"),
    ("hii_chaophraya_small", "https://tiwrmdev.hii.or.th/DATA/REPORT/php/chart/chaopraya/small/chaopraya.php"),
    ("thaiwater_waterlevel", "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"),
    ("thaiwater_watergate", "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/watergate_load"),
    ("thaiwater_chart", "https://waterchart.thaiwater.net/basin/chaophraya"),
    ("bma_flood", "https://weather.bangkok.go.th/Flood/PageMap/GetData?id=0"),
    ("bma_water", "https://weather.bangkok.go.th/Water/PageMap/GetData"),
    ("bma_flow", "https://weather.bangkok.go.th/Flow/PageMap/GetData"),
    ("your_cloudfront_klongs", "https://d2jyi6rznjny0r.cloudfront.net/klongmap-latest.json"),
    # --- new candidates: rainfall / forecast (UNVERIFIED URLs, probe will tell) ---
    ("openmeteo_forecast", f"https://api.open-meteo.com/v1/forecast?{BKK}&hourly=precipitation,precipitation_probability,cape,dew_point_2m&daily=precipitation_sum&forecast_days=7"),
    ("openmeteo_archive", f"https://archive-api.open-meteo.com/v1/archive?{BKK}&start_date=2026-10-01&end_date=2026-10-31&daily=precipitation_sum"),
    ("openmeteo_airquality", "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=13.75&longitude=100.5&hourly=pm2_5"),
    ("tmd_ckan_packages", "https://catalog.tmd.go.th/api/3/action/package_list"),
    ("air4thai_aqi", "http://air4thai.pcd.go.th/services/getNewAQI_JSON.php"),
    ("thaiwater_rain24h", "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24h"),
    ("ibtracs_active", "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r01/access/csv/ibtracs.ACTIVE.list.v04r01.csv"),
    ("ipinfo", "https://ipinfo.io/json"),  # where am I running from?
]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def probe(key, url, timeout=20):
    r = {"key": key, "url": url, "ok": False, "status": None, "ms": None, "bytes": 0,
         "content_type": None, "cors_allow_origin": None, "json": False,
         "shape": None, "preview": None, "error_kind": None, "error": None}
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Origin": "https://example.github.io"})
    ctx = ssl.create_default_context()
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read()
            r.update(status=resp.status, content_type=resp.headers.get("Content-Type"),
                     cors_allow_origin=resp.headers.get("Access-Control-Allow-Origin"))
            r["bytes"] = len(body)
            text = body[:2_000_000].decode("utf-8", errors="replace")
            r["preview"] = re.sub(r"\s+", " ", text[:160])
            try:
                j = json.loads(text)
                r["json"] = True
                if isinstance(j, dict):
                    r["shape"] = {k: (len(v) if isinstance(v, (list, dict)) else type(v).__name__)
                                  for k, v in list(j.items())[:8]}
                elif isinstance(j, list):
                    r["shape"] = f"list[{len(j)}]"
            except ValueError:
                pass
            # Empty-but-200 detection (your ThaiWater case)
            r["ok"] = r["bytes"] > 0 and not (r["json"] and r["shape"] in ({}, "list[0]"))
            if r["bytes"] > 0 and not r["ok"]:
                r["error_kind"] = "empty_200"
    except urllib.error.HTTPError as e:
        r.update(status=e.code, error_kind=f"http_{e.code}", error=str(e.reason))
    except Exception as e:
        msg = str(e)
        kind = ("refused" if "refused" in msg.lower() else "timeout" if "timed out" in msg.lower()
                else "dns" if "name or service" in msg.lower() or "getaddrinfo" in msg.lower()
                else "ssl" if "ssl" in msg.lower() or "certificate" in msg.lower() else "other")
        r.update(error_kind=kind, error=msg[:200])
    r["ms"] = int((time.time() - t0) * 1000)
    return r


def run(label):
    results = [probe(k, u) for k, u in ENDPOINTS]
    ip = next((x for x in results if x["key"] == "ipinfo"), {})
    return {"label": label, "utc": datetime.now(timezone.utc).isoformat(),
            "egress": ip.get("preview"), "results": results}


def handler(event=None, context=None):  # AWS Lambda entry
    label = os.environ.get("PROBE_LABEL", "lambda-th")
    report = run(label)
    bucket = os.environ.get("S3_BUCKET")
    if bucket:
        import boto3
        boto3.client("s3").put_object(
            Bucket=bucket, Key=os.environ.get("S3_KEY", f"probe/{label}.json"),
            Body=json.dumps(report, ensure_ascii=False, indent=1).encode(),
            ContentType="application/json")
    return {"label": label, "ok": sum(x["ok"] for x in report["results"]), "total": len(report["results"])}


def compare(paths):
    reps = [json.load(open(p)) for p in paths]
    print(f"{'endpoint':26}" + "".join(f"{r['label'][:16]:18}" for r in reps) + "CORS")
    for key, _ in ENDPOINTS:
        row, cors = f"{key:26}", ""
        for rep in reps:
            x = next((y for y in rep["results"] if y["key"] == key), None)
            row += f"{('OK' if x['ok'] else (x['error_kind'] or 'fail')) if x else '-':18}"
            if x and x["ok"]:
                cors = cors or (x["cors_allow_origin"] or "none")
        print(row + cors)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--compare":
        compare(sys.argv[2:])
    else:
        out = sys.argv[1] if len(sys.argv) > 1 else "probe_report.json"
        rep = run(os.environ.get("PROBE_LABEL", "local"))
        json.dump(rep, open(out, "w"), ensure_ascii=False, indent=1)
        print(f"{sum(x['ok'] for x in rep['results'])}/{len(rep['results'])} ok -> {out}")
