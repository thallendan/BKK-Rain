#!/usr/bin/env python3
"""
status.py

Builds status.json: one small summary per Bangkok weather site, for the hub page
(index.html) and the shared nav bar (bkk-nav.js).

Reads (local, written earlier by fetch_data.py and your monsoon script):
    data.json       macro page: teleconnections, dams, Pak Nam tide
    monsoon.json    monsoon page: 850 hPa winds, model rain, cyclones

Fetches (CloudFront, the same feeds the pages themselves read):
    bma-rain-latest.json, outlying-rain-latest.json   rain gauge page
    klongmap-latest.json                              klong page + HydroView
    master_water_level_data.json                      HydroView

Rules:
  * Nothing is simulated. A feed that cannot be read gives level "unknown".
  * Levels: unknown < normal < watch < warning < critical.
  * "stale" means the underlying data is older than the site's max_age_min.
    The hub re-checks this in the browser, so a stale status.json cannot show
    a false "Normal".
  * Python standard library only.
"""

import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timezone

OUTPUT_FILE = "status.json"
DATA_JSON = "data.json"
MONSOON_JSON = "monsoon.json"

CDN = "https://d2jyi6rznjny0r.cloudfront.net/"
URL_BMA = CDN + "bma-rain-latest.json"
URL_OUTLYING = CDN + "outlying-rain-latest.json"
URL_KLONG = CDN + "klongmap-latest.json"
URL_MASTER = CDN + "master_water_level_data.json"

# Tune these to how often each feed is really refreshed.
MAX_AGE_MIN = {
    "macro": 360,     # data.json: climate indices are monthly, dams daily, tide live
    "monsoon": 360,   # monsoon.json: model run
    "gauges": 20,     # BMA gauges, written every 5 min (the page itself warns at 20)
    "hydro": 120,     # master water file + klong map
    "klongs": 45,     # klong map
}

# Same box the klong page uses for the Bangna focus area.
BANGNA_BOUNDS = ((13.5883, 100.5170), (13.7217, 100.7230))

# Water IDs HydroView shows as klong/pump/gate nodes.
HYDRO_WATER_IDS = {"10", "19", "114", "43", "28", "42", "108", "109",
                   "132", "123", "184", "118", "106", "113", "102"}

LEVELS = ["unknown", "normal", "watch", "warning", "critical"]
RANK = {name: i for i, name in enumerate(LEVELS)}


# --------------------------------------------------------------------------- helpers

def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    """Parse an ISO-8601 string to an aware UTC datetime, or None."""
    if not s or not isinstance(s, str):
        return None
    try:
        s = s.strip().replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except ValueError:
        return None


def age_min(dt):
    return None if dt is None else (now_utc() - dt).total_seconds() / 60.0


def worst(*levels):
    return max(levels, key=lambda lv: RANK.get(lv, 0))


def num(v):
    """Return v as float, or None (also None for NaN)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def load_local(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        print(f"  ! cannot read {path}: {e}")
        return None


def fetch_json(url, timeout=20):
    """GET url and parse JSON. Returns None on any failure."""
    req = urllib.request.Request(url + "?t=" + str(int(now_utc().timestamp())),
                                 headers={"User-Agent": "Mozilla/5.0 BKK-status/1.0",
                                          "Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # network, HTTP, JSON: all mean "feed unavailable"
        print(f"  ! cannot fetch {url}: {e}")
        return None


def site(level="unknown", headline="No data", details=None, updated=None,
         key=None, ok=False):
    """Standard site record."""
    lim = MAX_AGE_MIN.get(key, 60)
    a = age_min(updated)
    return {
        "level": level,
        "headline": headline,
        "details": details or [],
        "ok": ok,
        "updated_utc": iso(updated) if updated else None,
        "max_age_min": lim,
        "stale": (a is None) or (a > lim),
    }


# --------------------------------------------------------------------------- macro

def build_macro(dj):
    if not dj or dj.get("is_live") is False or dj.get("status") == "FETCH_FAILED":
        return site(headline="Climate feed unavailable", key="macro",
                    updated=parse_iso((dj or {}).get("updated_at_utc")))

    updated = parse_iso(dj.get("updated_at_utc") or dj.get("timestamp_utc"))
    level, parts, details = "normal", [], []

    # Source health (written by fetch_data.py). Absent in older data.json files: then skip.
    health = dj.get("source_health") or {}
    parsed_n, total_n = dj.get("parsed_count"), dj.get("total_endpoints")
    if parsed_n is not None and total_n:
        if parsed_n < 4:
            return site(headline=f"Only {parsed_n} of {total_n} climate sources parsed", key="macro",
                        updated=updated, details=[f"{k}: {v.get('error') or 'no values parsed'}"
                                                  for k, v in health.items() if not v.get("parsed")][:4])
        missing = [k for k, v in health.items() if not v.get("parsed") and not v.get("carried")]
        carried = [k for k, v in health.items() if v.get("carried")]
        if missing:
            details.append("No data from: " + ", ".join(missing))
        if carried:
            details.append("Using last good values (older) for: " + ", ".join(carried))
        if parsed_n < total_n:
            parts_suffix = f"{parsed_n}/{total_n} sources current"
        else:
            parts_suffix = None
    else:
        parts_suffix = None

    # Bangkok-zone net monsoon anomaly (your own teleconnection model, +/-60 % scale)
    zones = dj.get("zones") or []
    bkk = next((z for z in zones if z.get("id") == "ZONE01"), None)
    net = num(bkk.get("net_anomaly")) if bkk else None
    if net is not None:
        state = "wetter" if net >= 0 else "drier"
        parts.append(f"Monsoon {net:+.0f}% {state}")
        if net >= 40:
            level = worst(level, "warning")
        elif net >= 20:
            level = worst(level, "watch")
        details.append(f"Bangkok-zone net teleconnection anomaly {net:+.1f}%")

    tc = dj.get("teleconnections") or {}
    if tc.get("mjo_phase") is not None:
        details.append(f"MJO phase {tc['mjo_phase']} (amp {num(tc.get('mjo_amplitude')) or 0:.2f})")
    if num(tc.get("enso")) is not None:
        details.append(f"Nino 3.4 {num(tc['enso']):+.2f} C")

    # Dams
    dams = dj.get("dams") or {}
    comb = num(dams.get("combined_chao_phraya_storage_pct"))
    if comb is not None:
        parts.append(f"4-dam {comb:.0f}%")
        details.append(f"Chao Phraya 4-dam storage {comb:.1f}%")
        if comb >= 90:
            level = worst(level, "watch")
    near_full = [k for k in ("bhumibol", "sirikit", "kwai_noi", "pasak", "srinagarind", "vajiralongkorn")
                 if (num((dams.get(k) or {}).get("current_storage_pct")) or 0) >= 95]
    if near_full:
        # Informational only: full reservoirs at the end of the wet season are normal, and without
        # inflow or spill data they are not a hazard signal by themselves.
        details.append("Near full (95%+): " + ", ".join(near_full))

    # Pak Nam tide
    pk = dj.get("paknam_gauge") or {}
    msl = num(pk.get("water_level_msl"))
    if msl is not None:
        parts.append(f"Pak Nam {msl:+.2f} m")
        risk = pk.get("surge_risk")
        details.append(f"Pak Nam tide {msl:+.3f} m MSL, surge risk {risk}")
        if msl >= (num(pk.get("flood_wall_threshold_msl")) or 1.80):
            level = worst(level, "critical")
        elif risk == "HIGH":
            level = worst(level, "warning")
        elif risk == "MODERATE":
            level = worst(level, "watch")

    if not parts:
        return site(headline="Feed loaded but empty", key="macro", updated=updated)
    if parts_suffix:
        parts.append(parts_suffix)
    return site(level, " · ".join(parts), details, updated, "macro", ok=True)


# --------------------------------------------------------------------------- monsoon

def build_monsoon(mj):
    if not mj or not mj.get("series"):
        return site(headline="Monsoon model unavailable", key="monsoon",
                    updated=parse_iso((mj or {}).get("updated_utc")))

    updated = parse_iso(mj.get("updated_utc"))
    s = mj["series"]
    i = mj.get("now_index")
    try:
        regime = s["regime"][i]
        spd = s["core_spd850"][i]
        rain24 = sum((v or 0) for v in s["bkk_rain_mm"][i:i + 24])
    except (TypeError, KeyError, IndexError):
        return site(headline="Monsoon data malformed", key="monsoon", updated=updated)

    level = "normal"
    if rain24 >= 50:
        level = "warning"      # the monsoon page itself calls 50+ mm a flood risk
    elif rain24 >= 10:
        level = "watch"

    cyc = (mj.get("cyclones") or [None])[0]
    cyc_txt = "no cyclone"
    details = [f"Regime {regime}, 850 hPa wind {spd} m/s",
               f"Model rain for Bangkok, next 24 h: {rain24:.0f} mm"]
    if cyc and num(cyc.get("km")) is not None:
        km = num(cyc["km"])
        cyc_txt = f"{cyc.get('name', 'cyclone')} {km:.0f} km"
        details.append(f"Nearest cyclone {cyc.get('name')} {km:.0f} km, alert {cyc.get('alert')}")
        if km < 1000:
            level = worst(level, "warning" if str(cyc.get("alert", "")).lower() in ("orange", "red") else "watch")

    headline = f"{regime} {spd} m/s · {rain24:.0f} mm next 24 h · {cyc_txt}"
    return site(level, headline, details, updated, "monsoon", ok=True)


# --------------------------------------------------------------------------- gauges

def build_gauges(bma, outlying):
    if not bma or not isinstance(bma.get("stations"), list):
        return site(headline="Gauge feed unavailable", key="gauges")

    updated = parse_iso(bma.get("newest_reading_utc") or bma.get("generated_utc"))
    fresh = [st for st in bma["stations"] if not st.get("stale")]
    if len(fresh) < 10:     # same guard the gauge page uses
        return site(headline=f"Only {len(fresh)} gauges reporting", key="gauges", updated=updated)

    r1 = [(num(st.get("rain_1h")) or 0.0, st) for st in fresh]
    wet = sum(1 for st in fresh if (num(st.get("rain_15m")) or 0) > 0)
    max1, top = max(r1, key=lambda t: t[0])
    max24 = max((num(st.get("rain_24h")) or 0.0) for st in fresh)

    # Thresholds from the gauge page's own 1 h and 24 h colour scales.
    level = "normal"
    if max1 >= 60 or max24 >= 120:
        level = "critical" if max1 >= 60 else "warning"
    elif max1 >= 30 or max24 >= 60:
        level = "warning" if max1 >= 30 else "watch"
    elif max1 >= 10:
        level = "watch"

    headline = f"{wet} of {len(fresh)} gauges wet"
    if max1 > 0:
        headline += f" · max {max1:.0f} mm/h ({top.get('district_en', 'Bangkok')})"
    details = [f"{len(fresh)} of {bma.get('count', len(bma['stations']))} gauges fresh",
               f"Heaviest 1 h: {max1:.1f} mm; heaviest 24 h: {max24:.1f} mm"]

    # Outlying rain, 20-150 km out
    if outlying and outlying.get("summary"):
        og = parse_iso(outlying.get("generated_utc"))
        oo = parse_iso(outlying.get("newest_obs_utc") or outlying.get("generated_utc"))
        ogm, oom = age_min(og), age_min(oo)
        o_stale = (ogm is not None and ogm > 45) or (oom is not None and oom > 90)
        sm, ap = outlying["summary"], outlying.get("approach") or {}
        if o_stale:
            details.append("Outlying rain data is stale")
        else:
            status = ap.get("status")
            details.append(f"Outlying rain: {sm.get('wet')} of {sm.get('stations')} stations wet, "
                           f"{sm.get('heavy')} heavy"
                           + (f", rain {status}" if status in ("approaching", "arrived") else ""))
            if status in ("approaching", "arrived"):
                level = worst(level, "watch")
                headline += " · rain " + ("at Bangkok edge" if status == "arrived" else "approaching")
    return site(level, headline, details, updated, "gauges", ok=True)


# --------------------------------------------------------------------------- klongs / hydro

def _ms_stamp(v):
    """'/Date(1699999999000)/' -> aware datetime, else None."""
    m = re.search(r"-?\d+", str(v or ""))
    if not m:
        return None
    try:
        return datetime.fromtimestamp(int(m.group()) / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _stations(klong):
    if not klong:
        return None
    arr = klong.get("waterStation") or (klong.get("data") or {}).get("waterStation") or klong.get("data")
    return arr if isinstance(arr, list) else None


def _classify(item):
    """Return dict(level_state, wl, ts, pumps_active, kind, lat, lng, wid) for one station."""
    info = item.get("water_station_info") or {}
    wl = item.get("water_level_last") or {}
    pump_last = item.get("water_pump_last") or {}
    lat = num(info.get("latitude", item.get("latitude", wl.get("latitude"))))
    lng = num(info.get("longitude", item.get("longitude", wl.get("longitude"))))
    wl_in = num(wl.get("wl_in"))
    warn, crit = num(info.get("warning")), num(info.get("critical"))
    state = "normal"
    if wl_in is not None and wl_in != -99:
        if crit is not None and wl_in >= crit:
            state = "critical"
        elif warn is not None and wl_in >= warn:
            state = "warning"
    try:
        pump_count = int(info.get("pump_count") or 0)
    except (TypeError, ValueError):
        pump_count = 0
    active = sum(1 for p in range(1, pump_count + 1)
                 if pump_last.get(f"pump{p:02d}") in (True, 1, "1", "ON"))
    type_id = item.get("station_type_id", info.get("station_type_id"))
    try:
        gates = int(info.get("water_gate_count") or 0)
    except (TypeError, ValueError):
        gates = 0
    kind = "pump" if (type_id == 2 or pump_count > 0) else ("gate" if (type_id == 3 or gates > 0) else "level")
    wid = item.get("water_id") or info.get("water_id")
    return {"state": state, "wl_in": wl_in, "ts": _ms_stamp(wl.get("site_timestamp") or wl.get("dt") or item.get("dt")),
            "active": active, "kind": kind, "lat": lat, "lng": lng,
            "wid": str(wid) if wid is not None else None}


def build_klongs(klong):
    arr = _stations(klong)
    if not arr:
        return site(headline="Klong feed unavailable", key="klongs")
    (lo_lat, lo_lng), (hi_lat, hi_lng) = BANGNA_BOUNDS
    rows = [_classify(it) for it in arr]
    rows = [r for r in rows if r["lat"] is not None and r["lng"] is not None
            and lo_lat <= r["lat"] <= hi_lat and lo_lng <= r["lng"] <= hi_lng]
    if not rows:
        return site(headline="No Bangna stations in feed", key="klongs")

    crit = sum(r["state"] == "critical" for r in rows)
    warn = sum(r["state"] == "warning" for r in rows)
    pumps = sum(r["active"] for r in rows)
    offline = sum(1 for r in rows if r["kind"] == "level" and r["wl_in"] == -99)
    stamps = [r["ts"] for r in rows if r["ts"] and r["wl_in"] not in (None, -99)]
    updated = max(stamps) if stamps else None

    level = "critical" if crit else ("warning" if warn else "normal")
    headline = f"{len(rows)} stations · {crit} critical · {warn} warning"
    if pumps:
        headline += f" · {pumps} pumps on"
    details = [f"{sum(r['kind']=='level' for r in rows)} level sensors, "
               f"{sum(r['kind']=='gate' for r in rows)} gates, {sum(r['kind']=='pump' for r in rows)} pump stations",
               f"{offline} level sensors reporting offline (-99)"]
    return site(level, headline, details, updated, "klongs", ok=True)


def build_hydro(master, klong, dj):
    notes, level, parts, stamps = [], "normal", [], []
    got_any = False

    # Klong/pump/gate nodes HydroView shows
    arr = _stations(klong)
    if arr:
        got_any = True
        rows = [r for r in (_classify(it) for it in arr) if r["wid"] in HYDRO_WATER_IDS]
        crit = sum(r["state"] == "critical" for r in rows)
        warn = sum(r["state"] == "warning" for r in rows)
        off = sum(1 for r in rows if r["wl_in"] in (None, -99))
        stamps += [r["ts"] for r in rows if r["ts"] and r["wl_in"] not in (None, -99)]
        parts.append(f"{crit} critical · {warn} warning" if (crit or warn) else "klong nodes normal")
        notes.append(f"{len(rows)} of {len(HYDRO_WATER_IDS)} klong nodes found, {off} offline")
        level = worst(level, "critical" if crit else ("warning" if warn else "normal"))

    # Pak Nam estuary and dams (data.json), same signals HydroView uses
    if dj and dj.get("is_live") is not False:
        pk = dj.get("paknam_gauge") or {}
        msl = num(pk.get("water_level_msl"))
        if msl is not None:
            got_any = True
            risk = pk.get("surge_risk")
            parts.append(f"Pak Nam {msl:+.2f} m")
            notes.append(f"Pak Nam {msl:+.3f} m MSL, surge risk {risk}")
            if msl >= (num(pk.get("flood_wall_threshold_msl")) or 1.80):
                level = worst(level, "critical")
            elif risk == "HIGH":
                level = worst(level, "warning")
            elif risk == "MODERATE":
                level = worst(level, "watch")
            pt = parse_iso(dj.get("timestamp_utc"))
            if pt:
                stamps.append(pt)
        dams = dj.get("dams") or {}
        full = [k for k, v in dams.items() if isinstance(v, dict)
                and (num(v.get("current_storage_pct")) or 0) >= 95]
        if full:
            notes.append("Dams near full (95%+): " + ", ".join(full))   # informational, not a level

    # Chao Phraya river block (master file): report, no thresholds known here
    cp = ((master or {}).get("parsed_summary") or {}).get("chaophraya_full")
    if master:
        got_any = True
        mt = parse_iso(master.get("timestamp_utc"))
        if mt:
            stamps.append(mt)
        if cp and not cp.get("parse_error"):
            c2 = (cp.get("itc") or {}).get("C2") or {}
            if num(c2.get("level_msl")) is not None:
                notes.append(f"C.2 Nakhon Sawan {num(c2['level_msl']):.2f} m MSL"
                             + (f", flow {num(c2['flow_cms']):.0f} m3/s" if num(c2.get("flow_cms")) is not None else ""))
        else:
            notes.append("Chao Phraya river block missing or unparsed in master file")

    if not got_any:
        return site(headline="Basin feeds unavailable", key="hydro")
    updated = max(stamps) if stamps else None
    return site(level, " · ".join(parts) or "Basin feeds loaded", notes, updated, "hydro", ok=True)


# --------------------------------------------------------------------------- main

def main():
    print("Building status.json ...")
    dj = load_local(DATA_JSON)
    mj = load_local(MONSOON_JSON)
    bma = fetch_json(URL_BMA)
    outlying = fetch_json(URL_OUTLYING)
    klong = fetch_json(URL_KLONG)
    master = fetch_json(URL_MASTER)

    sites = {
        "macro": build_macro(dj),
        "monsoon": build_monsoon(mj),
        "gauges": build_gauges(bma, outlying),
        "hydro": build_hydro(master, klong, dj),
        "klongs": build_klongs(klong),
    }

    # Overall: worst level among sites whose data is current. Stale or unknown sites
    # never count as "normal", they are reported separately.
    live = {k: v for k, v in sites.items() if v["ok"] and not v["stale"]}
    lv = worst("normal", *[v["level"] for v in live.values()]) if live else "unknown"
    not_current = [k for k, v in sites.items() if k not in live]
    headline = {"normal": "All current feeds normal", "watch": "Watch: elevated conditions",
               "warning": "Warning: significant conditions", "critical": "Critical: act on local alerts",
               "unknown": "No current data"}[lv]
    if not_current and live:
        headline += f" ({len(not_current)} feed{'s' if len(not_current) != 1 else ''} stale or unavailable)"

    out = {
        "schema": 1,
        "generated_utc": iso(now_utc()),
        "overall": {"level": lv, "headline": headline, "not_current": not_current},
        "sites": sites,
    }

    # Skip a rewrite when nothing changed and the file is fresh, so the repo is not
    # committed to every few minutes. Rewritten at least every 55 minutes.
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            old = json.load(f)
        old_gen = parse_iso(old.get("generated_utc"))
        a = {k: v for k, v in old.items() if k != "generated_utc"}
        b = {k: v for k, v in out.items() if k != "generated_utc"}
        if a == b and old_gen and age_min(old_gen) < 55:
            print("No change; status.json left as is.")
            return 0
    except (OSError, ValueError):
        pass

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    for k, v in sites.items():
        print(f"  {k:8s} {v['level']:8s} {'STALE ' if v['stale'] else ''}{v['headline']}")
    print(f"Wrote {OUTPUT_FILE} (overall: {lv}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
