#!/usr/bin/env python3
"""fetch_monsoon.py - builds monsoon.json for BKKmonsoon.html
Sources: Open-Meteo (GFS/ECMWF blend: 850/925 hPa winds, MSLP, rain), GDACS (tropical cyclones).
Stdlib only. Missing sources are reported as null/empty, never simulated."""
import json, math, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

OUT = "monsoon.json"
API = "https://api.open-meteo.com/v1/forecast"
GDACS = "https://www.gdacs.org/xml/rss.xml"
UA = {"User-Agent": "BKK-Rain-monsoon/1.0"}
BKK = (13.75, 100.5)
# Index points: Bangkok, Gulf of Thailand, Andaman Sea, South China Sea
PTS = {"BKK": BKK, "GULF": (11.0, 101.0), "ANDAMAN": (12.0, 96.0), "SCS": (12.0, 110.0)}
CORE = ["BKK", "GULF", "ANDAMAN"]            # averaged into the "core" monsoon index
LATS = list(range(0, 31, 5)); LONS = list(range(85, 131, 5))
HIGH_PT, LOW_PT = (30, 110), (15, 90)        # China high vs Bay of Bengal low (grid points)
PAST, FUT = 3, 7

def get(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return r.read()
        except Exception as e:
            err = e
            try: err = f"{e} {e.read().decode()[:300]}"
            except Exception: pass
            print("retry", i + 1, err); time.sleep(10 * (i + 1))
    print("FAILED", url[:90], err); return None

def om(points, hourly):
    q = {"latitude": ",".join(str(p[0]) for p in points), "longitude": ",".join(str(p[1]) for p in points),
         "hourly": ",".join(hourly), "wind_speed_unit": "ms", "timezone": "UTC",
         "past_days": PAST, "forecast_days": FUT}
    raw = get(API + "?" + urllib.parse.urlencode(q, safe=","))
    if not raw: return None
    d = json.loads(raw)
    return d if isinstance(d, list) else [d]

def uv(s, d):
    r = math.radians(d); return -s * math.sin(r), -s * math.cos(r)

def from_dir(u, v): return math.degrees(math.atan2(-u, -v)) % 360

def regime(u, v):
    if math.hypot(u, v) < 2.5: return "WEAK"
    d = from_dir(u, v)
    if 200 <= d < 290: return "SW"      # SW/westerly monsoon (Andaman/Bay of Bengal)
    if d >= 290 or d < 20: return "N"   # northerly/continental
    if d < 90: return "NE"              # NE monsoon (China/Siberian high)
    return "E"                          # E/SE (Pacific, typhoon-fed)

def bearing_dist(lat1, lon1, lat2, lon2):
    p1, p2, dl = map(math.radians, (lat1, lat2, lon2 - lon1))
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    km = 12742 * math.asin(math.sqrt(a))
    b = math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
        math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360
    return round(km), round(b)

def cyclones():
    raw = get(GDACS)
    if not raw: return None
    out = []
    try:
        for it in ET.fromstring(raw).iter("item"):
            f = {c.tag.split("}")[-1]: (c.text or "").strip() for c in it}
            if f.get("eventtype") != "TC" or f.get("iscurrent", "true").lower() == "false": continue
            lat, lon = map(float, f["point"].split())
            if not (-15 < lat < 50 and 50 < lon < 180): continue
            km, brg = bearing_dist(*BKK, lat, lon)
            out.append({"name": f.get("eventname") or f.get("title", "TC"), "lat": lat, "lon": lon,
                        "alert": f.get("alertlevel", ""), "title": f.get("title", ""),
                        "km": km, "bearing": brg, "link": f.get("link", "")})
    except Exception as e:
        print("GDACS parse error", e); return None
    return sorted(out, key=lambda c: c["km"])

def main():
    now = datetime.now(timezone.utc)
    res = {"updated_utc": now.isoformat(timespec="seconds"), "sources": {}}
    # One request shape (850 hPa wind + MSLP) for the wide grid AND the Bangkok-area index points.
    grid_pts = [(la, lo) for la in LATS for lo in LONS]
    names = list(PTS); allp = grid_pts + [PTS[n] for n in names]
    rows = []
    for k in range(0, len(allp), 37):
        d = om(allp[k:k + 37], ["wind_speed_850hPa", "wind_direction_850hPa", "pressure_msl"])
        if not d: rows = None; break
        rows += d
        time.sleep(3)
    res["sources"]["open_meteo_grid"] = bool(rows)
    res["sources"]["open_meteo_points"] = bool(rows)
    if not rows: raise SystemExit("No model data retrieved; keeping previous monsoon.json")
    gr, pr = rows[:len(grid_pts)], dict(zip(names, rows[len(grid_pts):]))
    # --- Bangkok-area core index (hourly) ---
    h = {n: pr[n]["hourly"] for n in names}
    times = h["BKK"]["time"]; n = len(times)
    cu, cv = [], []
    for i in range(n):
        us = [uv(h[p]["wind_speed_850hPa"][i], h[p]["wind_direction_850hPa"][i]) for p in CORE
              if h[p]["wind_speed_850hPa"][i] is not None]
        cu.append(sum(x[0] for x in us) / len(us) if us else 0); cv.append(sum(x[1] for x in us) / len(us) if us else 0)
    ex = om([BKK], ["precipitation", "relative_humidity_850hPa"])   # optional extras
    res["sources"]["open_meteo_rain"] = bool(ex)
    eh = ex[0]["hourly"] if ex else {}
    r1 = lambda a: [round(x, 1) for x in a]
    res["series"] = {"times": times, "core_u850": r1(cu), "core_v850": r1(cv),
        "core_spd850": r1([math.hypot(a, b) for a, b in zip(cu, cv)]),
        "core_dir850": [round(from_dir(a, b)) for a, b in zip(cu, cv)],
        "regime": [regime(a, b) for a, b in zip(cu, cv)],
        "bkk_rain_mm": eh.get("precipitation", [None] * n), "bkk_rh850": eh.get("relative_humidity_850hPa", [None] * n),
        "bkk_mslp": h["BKK"]["pressure_msl"]}
    res["now_index"] = max(i for i, t in enumerate(times) if t <= now.strftime("%Y-%m-%dT%H:00"))
    # --- wide grid (6-hourly) ---
    t = gr[0]["hourly"]["time"]; idx = [i for i, x in enumerate(t) if x.endswith(("00:00", "06:00", "12:00", "18:00"))]
    g = {"times": [t[i] for i in idx], "lats": LATS, "lons": LONS, "pts": []}
    for (la, lo), d in zip(grid_pts, gr):
        hh = d["hourly"]
        g["pts"].append({"lat": la, "lon": lo, "spd": [hh["wind_speed_850hPa"][i] for i in idx],
            "dir": [hh["wind_direction_850hPa"][i] for i in idx], "p": [hh["pressure_msl"][i] for i in idx]})
    res["grid"] = g
    ph = next(p for p in g["pts"] if (p["lat"], p["lon"]) == HIGH_PT)["p"]
    pl = next(p for p in g["pts"] if (p["lat"], p["lon"]) == LOW_PT)["p"]
    res["pressure_gradient"] = {"times": g["times"], "china_high": ph, "bob_low": pl,
        "diff_hpa": [None if a is None or b is None else round(a - b, 1) for a, b in zip(ph, pl)]}
    cy = cyclones(); res["sources"]["gdacs_cyclones"] = cy is not None; res["cyclones"] = cy or []
    with open(OUT, "w") as f: json.dump(res, f, separators=(",", ":"))
    print("wrote", OUT, res["sources"])

if __name__ == "__main__": main()
