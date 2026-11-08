#!/usr/bin/env python3
"""
collect.py  ->  data/latest.json

Pulls the endpoints that the probe showed working from a US GitHub runner,
trims the big ThaiWater payloads to the Bangkok area, and writes ONE compact
JSON for the HTML page. Reuses the proven parsers in fetch_data.py (keep that
file in the same folder). If a source fails, the previous value is kept and
marked "stale". Stdlib only.

Sections: forecast, rain_obs, waterlevel, watergates, cyclones, climate, flags
"""
import csv, io, json, math, os, re, ssl, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

from fetch_data import (ENDPOINTS, extract_parameters, extract_numeric_teleconnections,
                        extract_dams_data, extract_paknam_gauge)

OUT = os.environ.get("OUT_FILE", "data/latest.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
BKK_LAT, BKK_LON = 13.75, 100.50
BBOX = (13.3, 14.2, 100.2, 101.0)            # lat_min, lat_max, lon_min, lon_max (Bangkok + ring provinces)
AREA_WORDS = ("Bangkok", "กรุงเทพ", "Samut Prakan", "สมุทรปราการ", "Nonthaburi", "นนทบุรี",
              "Pathum Thani", "ปทุมธานี")
# flag thresholds (tune later against history)
T = dict(fc24_heavy=30, fc24_rain=10, prob_high=70, obs1h_heavy=10, cyclone_km=1500, tide_high=1.5)

URL_FORECAST = ("https://api.open-meteo.com/v1/forecast?latitude=13.75&longitude=100.5"
                "&timezone=Asia%2FBangkok&past_days=2&forecast_days=7"
                "&hourly=precipitation,precipitation_probability,cape,dew_point_2m"
                "&daily=precipitation_sum,precipitation_probability_max")
URL_RAIN = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24h"
URL_WL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load"
URL_GATE = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/watergate_load"
URL_IBTRACS = ("https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-"
               "ibtracs/v04r01/access/csv/ibtracs.ACTIVE.list.v04r01.csv")

DEBUG = {}


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
        return None if v < -900 else v      # ThaiWater uses large negatives for "missing"
    except (TypeError, ValueError):
        return None


def first(d, keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d[k]
    return None


def label(v):
    if isinstance(v, dict):
        return v.get("en") or v.get("th") or next(iter(v.values()), None)
    return v


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


def in_area(rec, *extra):
    lat, lon = coords(rec, *extra)
    if lat is not None:
        return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]
    blob = json.dumps([rec, *extra], ensure_ascii=False)
    return any(w in blob for w in AREA_WORDS)


def haversine(a, b, c, d):
    p = math.pi / 180
    h = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def rows(j, section=None):
    if section:
        j = (j or {}).get(section, {})
    if isinstance(j, dict):
        j = j.get("data", [])
    return j if isinstance(j, list) else []


def now_bkk():
    return datetime.now(timezone(timedelta(hours=7)))


# ---------- sections ----------
def sec_forecast():
    j = json.loads(get(URL_FORECAST))
    h, d = j["hourly"], j["daily"]
    key = now_bkk().strftime("%Y-%m-%dT%H:00")
    i = next((n for n, t in enumerate(h["time"]) if t >= key), 0)
    pr = [x or 0 for x in h["precipitation"]]
    pp = [x or 0 for x in h["precipitation_probability"]]
    cape = [x or 0 for x in h["cape"]]
    nxt = lambda arr, n: arr[i:i + n]
    peak = max(range(i, min(i + 72, len(pr))), key=lambda n: pr[n])
    return {
        "source": "Open-Meteo best_match", "as_of": h["time"][i],
        "past_24h_mm": round(sum(pr[max(0, i - 24):i]), 1),
        "next_6h_mm": round(sum(nxt(pr, 6)), 1),
        "next_24h_mm": round(sum(nxt(pr, 24)), 1),
        "next_72h_mm": round(sum(nxt(pr, 72)), 1),
        "max_prob_24h_pct": max(nxt(pp, 24) or [0]),
        "max_cape_24h": max(nxt(cape, 24) or [0]),
        "dew_point_c": h["dew_point_2m"][i],
        "peak_hour_72h": {"time": h["time"][peak], "mm": pr[peak]},
        "hourly_next_48h": [{"t": h["time"][n], "mm": pr[n], "p": pp[n]}
                            for n in range(i, min(i + 48, len(pr)))],
        "daily": [{"date": d["time"][n], "mm": d["precipitation_sum"][n],
                   "p_max": d["precipitation_probability_max"][n]} for n in range(len(d["time"]))],
    }


def sec_rain_obs():
    recs = rows(json.loads(get(URL_RAIN)))
    DEBUG["rain_keys"] = sorted(recs[0].keys()) if recs else None
    DEBUG["rain_total"] = len(recs)
    out = []
    for r in recs:
        st = r.get("station") if isinstance(r.get("station"), dict) else {}
        if not in_area(r, st):
            continue
        out.append({"name": label(first(st, ["station_name", "tele_station_name", "name"])
                                  or first(r, ["station_name", "name"])),
                    "r1": num(r.get("rain_1h")), "r24": num(r.get("rain_24h")),
                    "t": r.get("rainfall_datetime")})
    r1 = [x["r1"] for x in out if x["r1"] is not None]
    r24 = [x["r24"] for x in out if x["r24"] is not None]
    top = sorted(out, key=lambda x: ((x["r1"] or 0), (x["r24"] or 0)), reverse=True)[:8]
    return {"source": "ThaiWater rain_24h", "stations_in_area": len(out),
            "stations_raining_1h": sum(1 for v in r1 if v > 0),
            "max_1h_mm": max(r1, default=None), "max_24h_mm": max(r24, default=None),
            "top": top}


def _lookup(j, skip):
    lk = {}
    for k, v in j.items():
        if k != skip and ("station" in k or "watergate" in k) and isinstance(v, dict):
            for it in v.get("data", []) or []:
                if isinstance(it, dict) and it.get("id") is not None:
                    lk[str(it["id"])] = it
    return lk


def _joined(j, section, id_keys):
    lk = _lookup(j, section)
    recs = rows(j, section)
    DEBUG[section + "_keys"] = sorted(recs[0].keys()) if recs else None
    DEBUG[section + "_total"] = len(recs)
    DEBUG[section + "_lookup"] = len(lk)
    for r in recs:
        sid = first(r, id_keys)
        st = lk.get(str(sid)) or (r.get("station") if isinstance(r.get("station"), dict) else {})
        yield r, st


def sec_waterlevel():
    j = json.loads(get(URL_WL))
    out = []
    for r, st in _joined(j, "waterlevel_data", ["station_id", "waterlevel_station_id"]):
        if in_area(r, st):
            lat, lon = coords(r, st)
            out.append({"name": label(first(st, ["waterlevel_station_name", "station_name", "name"])),
                        "level": num(first(r, ["waterlevel_msl", "waterlevel_m", "waterlevel"])),
                        "t": r.get("waterlevel_datetime"), "lat": lat, "lon": lon})
    DEBUG["waterlevel_in_area"] = len(out)
    return {"source": "ThaiWater waterlevel_load", "count": len(out), "stations": out[:60]}


def sec_watergates():
    j = json.loads(get(URL_GATE))
    out = []
    for r, st in _joined(j, "watergate_data", ["watergate_id", "station_id", "watergate_station_id"]):
        if in_area(r, st):
            lat, lon = coords(r, st)
            out.append({"name": label(first(st, ["watergate_name", "station_name", "name"])),
                        "in": num(r.get("watergate_in")), "out": num(r.get("watergate_out")),
                        "t_in": r.get("watergate_datetime_in"), "t_out": r.get("watergate_datetime_out"),
                        "lat": lat, "lon": lon})
    DEBUG["watergates_in_area"] = len(out)
    return {"source": "ThaiWater watergate_load", "count": len(out), "gates": out[:60]}


def sec_cyclones():
    text = get(URL_IBTRACS)
    latest = {}
    for r in csv.DictReader(io.StringIO(text)):
        try:
            t = datetime.fromisoformat(r["ISO_TIME"].strip()).replace(tzinfo=timezone.utc)
            lat, lon = float(r["LAT"]), float(r["LON"])
        except (ValueError, KeyError, TypeError):
            continue                      # skips the units row and blanks
        if r["SID"] not in latest or t > latest[r["SID"]][0]:
            latest[r["SID"]] = (t, lat, lon, r)
    cutoff = datetime.now(timezone.utc) - timedelta(days=5)
    out = []
    for sid, (t, lat, lon, r) in latest.items():
        if t >= cutoff:
            out.append({"name": r.get("NAME"), "basin": r.get("BASIN"), "time": t.isoformat(),
                        "lat": lat, "lon": lon, "wind_kt": num(r.get("WMO_WIND")),
                        "pres_hpa": num(r.get("WMO_PRES")),
                        "dist_km": round(haversine(BKK_LAT, BKK_LON, lat, lon))})
    out.sort(key=lambda x: x["dist_km"])
    return {"source": "IBTrACS ACTIVE", "active_5d": len(out), "nearest": out[:5]}


def sec_climate():
    params, status = {}, {}
    for ep in ENDPOINTS:
        try:
            body = get(ep["url"], timeout=40)
            p = extract_parameters(ep["key"], "", body)
            params.update(p or {})
            status[ep["key"]] = "ok" if p else "parsed_nothing"
        except Exception as e:
            status[ep["key"]] = f"fail: {str(e)[:60]}"
    return {"teleconnections": extract_numeric_teleconnections(params),
            "dams": extract_dams_data(params), "tide": extract_paknam_gauge(params),
            "fetch_status": status}


def make_flags(s):
    f, fc = [], s.get("forecast") or {}
    if (fc.get("next_24h_mm") or 0) >= T["fc24_heavy"]:
        f.append({"level": "high", "msg": f"Heavy rain forecast: {fc['next_24h_mm']} mm in 24 h"})
    elif (fc.get("next_24h_mm") or 0) >= T["fc24_rain"]:
        f.append({"level": "med", "msg": f"Rain likely: {fc['next_24h_mm']} mm in 24 h"})
    if (fc.get("max_prob_24h_pct") or 0) >= T["prob_high"]:
        f.append({"level": "med", "msg": f"Rain probability up to {fc['max_prob_24h_pct']}%"})
    ro = s.get("rain_obs") or {}
    if (ro.get("max_1h_mm") or 0) >= T["obs1h_heavy"]:
        f.append({"level": "high", "msg": f"Heavy rain falling now: {ro['max_1h_mm']} mm/h at a station"})
    cy = (s.get("cyclones") or {}).get("nearest") or []
    if cy and cy[0]["dist_km"] <= T["cyclone_km"]:
        f.append({"level": "med", "msg": f"Tropical system {cy[0]['name']} {cy[0]['dist_km']} km away"})
    tide = ((s.get("climate") or {}).get("tide") or {}).get("water_level_msl")
    if tide is not None and tide >= T["tide_high"]:
        f.append({"level": "high", "msg": f"High Gulf tide: {tide} m MSL"})
    return f


def main():
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            pass
    sections = {"forecast": sec_forecast, "rain_obs": sec_rain_obs, "waterlevel": sec_waterlevel,
                "watergates": sec_watergates, "cyclones": sec_cyclones, "climate": sec_climate}
    out, status = {}, {}
    for name, fn in sections.items():
        t0 = time.time()
        try:
            out[name] = fn()
            status[name] = {"ok": True, "ms": int((time.time() - t0) * 1000)}
        except Exception as e:
            old = prev.get(name)
            out[name] = dict(old, stale=True) if isinstance(old, dict) else None
            status[name] = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"}
        print(f"[{'OK' if status[name]['ok'] else 'FAIL'}] {name} {status[name]}")
    out["flags"] = make_flags(out)
    out["status"] = status
    out["generated_utc"] = datetime.now(timezone.utc).isoformat()
    out["_debug"] = DEBUG
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUT} ({os.path.getsize(OUT)//1024} KB)")
    sys.exit(0 if any(v["ok"] for v in status.values()) else 1)


if __name__ == "__main__":
    main()
