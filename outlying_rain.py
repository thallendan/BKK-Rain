#!/usr/bin/env python3
"""
outlying_rain.py  ->  data/outlying_rain.json

Rain telemetry from stations OUTSIDE Bangkok (a 20-150 km ring around the city),
so rain can be seen coming before it reaches the BMA gauges. Source: ThaiWater
rain_24h (works from a US GitHub runner). Stdlib only, no other repo files needed.

Output (compact, one file for the HTML page)
  summary        counts, max 1 h rain, nearest rain to Bangkok
  sectors        8 compass sectors: stations, wet stations, max rain, nearest wet / heavy km
  rings          20-50 / 50-100 / 100-150 km
  wet_stations   up to 150 stations with rain right now (name, lat/lon, km, bearing, 1 h, 24 h)
  approach       is the nearest rain edge closing in? speed + rough ETA (needs ~30 min of history)
  history        last 16 runs (kept in the file itself, so no database is needed)
If ThaiWater fails, the old file is left untouched and the script exits 1.
"""
import json, math, os, re, ssl, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

OUT = os.environ.get("OUT_FILE", "data/outlying_rain.json")
URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24h"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
CENTER = (13.7563, 100.5018)             # Bangkok reference point
MIN_KM, MAX_KM = 20, 150                 # ring of interest (inside 20 km the BMA gauges cover it)
MAX_AGE_MIN = 180                        # ignore station readings older than this
WET_MM, HEAVY_MM = 0.5, 10.0             # mm in last hour: "wet" and "heavy"
HIST_KEEP = 16
COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
ICT = timezone(timedelta(hours=7))


# ---------- helpers ----------
def get(url, timeout=40, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def num(x):
    try:
        v = float(x)
        return None if v < -900 else v          # ThaiWater uses big negatives for "missing"
    except (TypeError, ValueError):
        return None


def label(v):
    if isinstance(v, dict):
        return v.get("en") or v.get("th") or next(iter(v.values()), None)
    return v


def name_of(*objs):
    for o in objs:
        if not isinstance(o, dict):
            continue
        for k in ("station_name", "tele_station_name", "name"):
            if o.get(k):
                return label(o[k])
        for k, v in o.items():
            kl = k.lower()
            if "name" in kl and v and not kl.startswith(("river", "basin", "agency", "province")):
                return label(v)
    return None


def coords(*objs):
    for o in objs:
        if not isinstance(o, dict):
            continue
        la = [k for k in o if re.search(r"(^|_)lat(itude)?$", k.lower())]
        lo = [k for k in o if re.search(r"(^|_)(lon|long|lng|longitude)$", k.lower())]
        if la and lo:
            a, b = num(o[la[0]]), num(o[lo[0]])
            if a is not None and b is not None and a != 0:
                return a, b
    return None, None


def parse_time(s):
    """ThaiWater times are local (ICT) unless they carry a timezone."""
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00").replace("/", "-"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=ICT)).astimezone(timezone.utc)


def haversine(a, b, c, d):
    p = math.pi / 180
    h = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def bearing(a, b, c, d):
    p = math.pi / 180
    y = math.sin((d - b) * p) * math.cos(c * p)
    x = math.cos(a * p) * math.sin(c * p) - math.sin(a * p) * math.cos(c * p) * math.cos((d - b) * p)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def compass(brg):
    return COMPASS[int((brg + 22.5) // 45) % 8]


# ---------- collect ----------
def collect(text, now):
    j = json.loads(text)
    recs = j.get("data", []) if isinstance(j, dict) else j
    if not isinstance(recs, list) or len(recs) < 100:
        raise ValueError(f"Unexpected ThaiWater payload ({type(recs).__name__}, {len(recs) if hasattr(recs, '__len__') else '?'})")
    out, no_coord, too_old = [], 0, 0
    for r in recs:
        st = r.get("station") if isinstance(r.get("station"), dict) else {}
        lat, lon = coords(r, st)
        if lat is None:
            no_coord += 1
            continue
        km = haversine(*CENTER, lat, lon)
        if not (MIN_KM <= km <= MAX_KM):
            continue
        t = parse_time(r.get("rainfall_datetime"))
        age = round((now - t).total_seconds() / 60) if t else None
        if age is not None and age > MAX_AGE_MIN:
            too_old += 1
            continue
        r1, r24 = num(r.get("rain_1h")), num(r.get("rain_24h"))
        if r1 is None and r24 is None:
            continue
        brg = bearing(*CENTER, lat, lon)
        out.append({"name": name_of(st, r), "lat": round(lat, 4), "lon": round(lon, 4), "km": round(km),
                    "brg": round(brg), "dir": compass(brg), "r1": r1, "r24": r24,
                    "t": t.isoformat() if t else None, "age_min": age})
    return out, {"records_total": len(recs), "no_coords": no_coord, "too_old": too_old}


def nearest(sts, thr):
    c = [s for s in sts if (s["r1"] or 0) >= thr]
    if not c:
        return None
    s = min(c, key=lambda x: x["km"])
    return {"km": s["km"], "dir": s["dir"], "name": s["name"], "r1": s["r1"]}


def summarize(sts):
    sectors = []
    for i, d in enumerate(COMPASS):
        grp = [s for s in sts if s["dir"] == d]
        wet = [s for s in grp if (s["r1"] or 0) >= WET_MM]
        nw, nh = nearest(grp, WET_MM), nearest(grp, HEAVY_MM)
        sectors.append({"sector": d, "stations": len(grp), "wet": len(wet),
                        "max_1h": max((s["r1"] or 0 for s in grp), default=0),
                        "nearest_wet_km": nw["km"] if nw else None,
                        "nearest_heavy_km": nh["km"] if nh else None})
    rings = []
    for a, b in ((20, 50), (50, 100), (100, 150)):
        grp = [s for s in sts if a <= s["km"] < b or (b == 150 and s["km"] == 150)]
        rings.append({"ring": f"{a}-{b} km", "stations": len(grp),
                      "wet": sum(1 for s in grp if (s["r1"] or 0) >= WET_MM),
                      "max_1h": max((s["r1"] or 0 for s in grp), default=0)})
    wet = sorted([s for s in sts if (s["r1"] or 0) >= WET_MM], key=lambda s: s["r1"], reverse=True)
    ages = sorted(s["age_min"] for s in sts if s["age_min"] is not None)
    summary = {"stations": len(sts), "wet": len(wet), "heavy": sum(1 for s in wet if s["r1"] >= HEAVY_MM),
               "max_1h_mm": max((s["r1"] or 0 for s in sts), default=0),
               "median_age_min": ages[len(ages) // 2] if ages else None,
               "nearest_wet": nearest(sts, WET_MM), "nearest_heavy": nearest(sts, HEAVY_MM)}
    return summary, sectors, rings, wet[:150]


def approach(hist):
    """Is the nearest rain edge closing in on Bangkok? Compare latest run with ~30-60 min earlier."""
    pts = [(datetime.fromisoformat(h["obs"]), h["nearest_wet_km"]) for h in hist if h.get("nearest_wet_km") is not None and h.get("obs")]
    base = {"status": "collecting", "text": "Collecting history (needs about 30 minutes of runs).", "speed_kmh": None, "eta_min": None}
    if len(pts) < 2:
        return base
    t1, d1 = pts[-1]
    older = [p for p in pts[:-1] if 20 <= (t1 - p[0]).total_seconds() / 60 <= 90]
    if not older:
        return base
    t0, d0 = older[0]
    hrs = (t1 - t0).total_seconds() / 3600
    rate = (d0 - d1) / hrs                     # + = closing
    base["speed_kmh"] = round(rate, 1)
    if d1 <= MIN_KM + 5:
        base.update(status="arrived", text="Rain is at the edge of the Bangkok area.")
    elif rate >= 5 and d1 <= 130:
        eta = (d1 - MIN_KM) / rate * 60
        base.update(status="approaching", eta_min=round(eta) if eta <= 300 else None,
                    text=f"Nearest rain edge {d1} km out, closing at ~{round(rate)} km/h"
                         + (f"; could reach the Bangkok edge in ~{round(eta)} min." if eta <= 300 else "."))
    elif rate <= -5:
        base.update(status="receding", text=f"Nearest rain edge {d1} km out and moving away (~{round(-rate)} km/h).")
    else:
        base.update(status="steady", text=f"Nearest rain edge {d1} km out, little change.")
    return base


def main():
    now = datetime.now(timezone.utc)
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            pass
    try:
        sts, diag = collect(get(URL), now)
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}  (old file left untouched)")
        sys.exit(1)
    if len(sts) < 10:
        print(f"FAILED: only {len(sts)} usable stations in the ring (old file left untouched)")
        sys.exit(1)
    summary, sectors, rings, wet = summarize(sts)
    obs = max((s["t"] for s in sts if s["t"]), default=None)
    hist = prev.get("history", [])
    if obs and not (hist and hist[-1].get("obs") == obs):          # skip duplicate readings
        nw = summary["nearest_wet"]
        hist.append({"t": now.isoformat(), "obs": obs, "wet": summary["wet"], "max_1h": summary["max_1h_mm"],
                     "nearest_wet_km": nw["km"] if nw else None,
                     "nearest_heavy_km": (summary["nearest_heavy"] or {}).get("km"),
                     "sector_wet": {s["sector"]: s["wet"] for s in sectors}})
    hist = hist[-HIST_KEEP:]
    doc = {"source": "ThaiWater rain_24h (stations 20-150 km from Bangkok)",
           "generated_utc": now.isoformat(), "newest_obs_utc": obs,
           "ring_km": [MIN_KM, MAX_KM], "thresholds_mm_per_h": {"wet": WET_MM, "heavy": HEAVY_MM},
           "summary": summary, "approach": approach(hist), "sectors": sectors, "rings": rings,
           "wet_stations": wet, "history": hist, "_diag": diag}
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUT} ({os.path.getsize(OUT)//1024} KB): {summary['stations']} stations, {summary['wet']} wet, "
          f"nearest wet {summary['nearest_wet']}, approach={doc['approach']['status']}, median age {summary['median_age_min']} min")


if __name__ == "__main__":
    main()
