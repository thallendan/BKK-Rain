#!/usr/bin/env python3
"""
fetch_data.py

Fetches live weather, climate, and hydrological data from 9 primary endpoints:
1. ENSO (Niño Indices - NOAA CPC)
2. MJO (RMM Index - Australian BoM)
3. IOD (Indian Ocean Dipole - NOAA PSL)
4. CPAC850 (Zonal Wind Anomaly - NOAA CPC)
5. SOI (Southern Oscillation Index - NOAA CPC)
6. OLR (Outgoing Longwave Radiation - NOAA CPC)
7. Pacific Heat Content Index (NOAA CPC)
8. Thailand Dam Storage (HII/RID)
9. Chao Phraya Estuary Sea Level & Tide (HII/Navy)

Parses scalar teleconnection indices, reservoir storage, and Pak Nam tide gauge
telemetry, strictly handling missing endpoints as unavailable (no simulation),
and writes standardized output strictly to 'data.json'.

Reliability behaviour (added):
  * Per-source health is written to data.json ("source_health", "parsed_count").
  * TLS certificates are verified, except for the hosts in VERIFY_EXEMPT_HOSTS.
  * If a SLOW-changing source (monthly climate indices, daily dam report) fails, its last good
    value is carried forward, labelled with "carried_from_utc" and dropped after a maximum age.
    The live Pak Nam tide is never carried forward.
  * If NO source could be parsed, data.json is left untouched and the script exits with an
    error, so the workflow fails visibly instead of publishing an empty file.
"""

import json
import ssl
import sys
import time
import re
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

OUTPUT_FILE = "data.json"

# Hosts whose certificate problems are tolerated (the Thai government sites). Everything else
# is verified. If another source starts failing with a certificate error, its host can be
# added here, but prefer fixing the cause.
VERIFY_EXEMPT_HOSTS = ("hii.or.th",)

# endpoint key -> (maximum carry age in hours, parameter names or "prefix_" patterns it produces).
# The Pak Nam tide ("chao_phraya_tide") is deliberately absent: a stale tide must never look live.
CARRY_RULES = {
    "enso": (240, ["enso_nino34_anom"]),
    "mjo": (72, ["mjo_phase_amp"]),
    "iod": (240, ["iod_dmi_index"]),
    "cpac850": (240, ["cpac850_wind_index"]),
    "soi": (240, ["soi_index"]),
    "olr": (240, ["olr_index"]),
    "heat_content": (240, ["pacific_heat_content"]),
    "dam_storage": (72, ["chao_phraya_4dam_combined", "dam_"]),
}

# Sources that are nice to have. When one of these fails (for example the Navy tide table has not
# rolled over to today yet) it does not count against "sources current" and is never a reason to fail.
OPTIONAL_SOURCES = {"tide_prediction", "dam_flows"}

DAM_KEYS = ["bhumibol", "sirikit", "kwai_noi", "pasak", "srinagarind", "vajiralongkorn"]

ENDPOINTS = [
    {
        "key": "enso",
        "name": "ENSO (Niño Indices)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices"
    },
    {
        "key": "mjo",
        "name": "MJO (RMM Index)",
        "url": "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt"
    },
    {
        "key": "iod",
        "name": "IOD (Indian Ocean Dipole)",
        "url": "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.data"
    },
    {
        "key": "cpac850",
        "name": "CPAC850 (Zonal Wind Anomaly)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/cpac850"
    },
    {
        "key": "soi",
        "name": "Tahiti-Darwin Sea Pressure Difference (SOI)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/soi"
    },
    {
        "key": "olr",
        "name": "Outgoing Longwave Radiation (OLR)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/olr"
    },
    {
        "key": "heat_content",
        "name": "Pacific Heat Content Index",
        "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/ocean/index/heat_content_index.txt"
    },
    {
        "key": "dam_storage",
        "name": "Thailand Dam Storage (HII/RID)",
        "url": "https://tiwrmdev.hii.or.th/DATA/REPORT/php/rid_dam_1.php?lang=en"
    },
    {
        "key": "chao_phraya_tide",
        "name": "Chao Phraya Estuary Sea Level (Fort Phrachulachomklao, GLF001) via Thaiwater API",
        "url": "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/waterlevel_load",
        # Same headers the thaiwater.net web app sends.
        "headers": {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.thaiwater.net/",
            "Origin": "https://www.thaiwater.net",
        },
    },
    {
        "key": "dam_flows",
        "name": "Dam inflow, release and storage (ThaiWater analyst API, same data as waterchart.thaiwater.net)",
        "url": "https://api-v3.thaiwater.net/api/v1/thaiwater30/analyst/dam",
        "headers": {"Accept": "*/*", "Referer": "https://waterchart.thaiwater.net/", "Origin": "https://waterchart.thaiwater.net"},
    },
    {
        "key": "tide_prediction",
        "name": "Predicted tide extremes (Royal Thai Navy N03 Fort Chula, via HII FEWS)",
        "url": "https://fews2.hii.or.th/model-output/data_portal/tide_table/summary.txt",
        "headers": {"Referer": "https://www.thaiwater.net/", "Origin": "https://www.thaiwater.net"},
    }
]


def parse_chao_phraya_tide(html_text):
    """
    Parses real-time sea level gauge (GLF001) and predicted tide levels (N03)
    for Fort Phrachulachomklao at the Chao Phraya Estuary from HII/Thaiwater.
    """
    params = {}
    clean_html = html_text.replace("&nbsp;", " ")

    # 1. Real-time Gauge Telemetry (Station GLF001 - Fort Phrachulachomklao)
    glf_match = re.search(r'<tr[^>]*rel="GLF001"[^>]*>(.*?)</tr>', clean_html, re.DOTALL | re.IGNORECASE)
    if glf_match:
        row_content = glf_match.group(1)
        td_values = re.findall(r'<td[^>]*>(.*?)</td>', row_content, re.DOTALL | re.IGNORECASE)
        td_clean = [re.sub(r'<[^>]+>', '', td).strip() for td in td_values]

        if len(td_clean) >= 5:
            date_str = td_clean[2]
            time_str = td_clean[3]
            level_str = td_clean[4]
            try:
                level_val = float(level_str)
                params["chao_phraya_estuary_realtime_level"] = {
                    "label": "Chao Phraya Estuary Real-Time Sea Level (Fort Phrachulachomklao)",
                    "value": f"{level_val:+.3f}",
                    "unit": "m MSL",
                    "period": f"{date_str} {time_str}",
                    "description": "Live Telemetry Sea Level at Chao Phraya River Mouth (HII Station GLF001)"
                }
            except ValueError:
                pass

    # 2. Predictive Tide Extremes (Station N03 - Fort Phrachulachomklao)
    n03_match = re.search(r'<tr[^>]*rel="N03"[^>]*>(.*?)</tr>', clean_html, re.DOTALL | re.IGNORECASE)
    if n03_match:
        row_content = n03_match.group(1)
        td_values = re.findall(r'<td[^>]*>(.*?)</td>', row_content, re.DOTALL | re.IGNORECASE)
        td_clean = [re.sub(r'<[^>]+>', ' ', td).strip() for td in td_values]

        if len(td_clean) >= 3:
            high_info = " ".join(td_clean[1].split())
            low_info = " ".join(td_clean[2].split())
            params["chao_phraya_estuary_predicted_tide"] = {
                "label": "Chao Phraya Estuary Daily Tide Bounds (Fort Phrachulachomklao)",
                "value": f"Max: {high_info} | Min: {low_info}",
                "unit": "m MSL (Time)",
                "period": "Today Forecast",
                "description": "Royal Thai Navy Predictive Tide Extremes (Station N03)"
            }

    return params if params else None


def parse_dam_storage_bkk_region(html_text):
    """
    Parses reservoir storage directly from raw line text to handle malformed HTML
    from HII (unclosed <tr> tags) without DOM parser dependencies.
    """
    target_dams = {
        "bhumibol": {
            "keywords": ["bhumibol", "bhumipol", "ภูมิพล"],
            "label": "Bhumibol Dam (Ping River)",
            "role": "Chao Phraya Upper Basin Headwater Storage",
            "capacity": 13462.0
        },
        "sirikit": {
            "keywords": ["sirikit", "สิริกิติ์"],
            "label": "Sirikit Dam (Nan River)",
            "role": "Chao Phraya Upper Basin Headwater Storage",
            "capacity": 9510.0
        },
        "kwai_noi": {
            "keywords": ["kheaw noi", "kwai noi", "kwae noi", "แควน้อย"],
            "label": "Kwai Noi Bamrung Dan Dam",
            "role": "Chao Phraya Mid-Basin Flow Regulation",
            "capacity": 939.0
        },
        "pasak": {
            "keywords": ["pa sak jorasit", "pasak", "pa sak", "ป่าสัก"],
            "label": "Pasak Jolasid Dam (Pasak River)",
            "role": "Lower Chao Phraya / Eastern BKK Flow Buffer",
            "capacity": 872.0
        },
        "srinagarind": {
            "keywords": ["srinakarin", "srinagarind", "ศรีนครินทร์"],
            "label": "Srinagarind Dam (Mae Klong Basin)",
            "role": "Western Diversion & Samut Prakan Salinity Repulsion",
            "capacity": 17745.0
        },
        "vajiralongkorn": {
            "keywords": ["wachiralongkorn", "vajiralongkorn", "วชิราลงกรณ"],
            "label": "Vajiralongkorn Dam (Mae Klong Basin)",
            "role": "Western Diversion & Samut Prakan Salinity Repulsion",
            "capacity": 8860.0
        }
    }

    clean_html = html_text.replace("&nbsp;", " ").replace("<tr", "\n<tr")
    parsed_dams = {}

    for line in clean_html.splitlines():
        plain_text = re.sub(r"<[^>]+>", " ", line)
        plain_text_lower = " ".join(plain_text.split()).lower()

        if not plain_text_lower:
            continue

        for dam_key, dam_meta in target_dams.items():
            if dam_key not in parsed_dams and any(kw in plain_text_lower for kw in dam_meta["keywords"]):
                matches = re.findall(r'[-+]?\d+(?:,\d{3})*(?:\.\d+)?', plain_text)
                numbers = []
                for m in matches:
                    try:
                        numbers.append(float(m.replace(",", "")))
                    except ValueError:
                        pass

                if len(numbers) >= 5:
                    cap = numbers[0]
                    storage_mcm = numbers[2]
                    storage_pct = numbers[3]
                    usable_mcm = numbers[4]

                    parsed_dams[dam_key] = {
                        "label": dam_meta["label"],
                        "role": dam_meta["role"],
                        "capacity_mcm": cap,
                        "storage_mcm": storage_mcm,
                        "storage_pct": storage_pct,
                        "usable_mcm": usable_mcm
                    }

    param_results = {}

    chao_phraya_keys = ["bhumibol", "sirikit", "kwai_noi", "pasak"]
    cp_cap_sum = sum(parsed_dams[k]["capacity_mcm"] for k in chao_phraya_keys if k in parsed_dams)
    cp_storage_sum = sum(parsed_dams[k]["storage_mcm"] for k in chao_phraya_keys if k in parsed_dams)
    cp_usable_sum = sum(parsed_dams[k]["usable_mcm"] for k in chao_phraya_keys if k in parsed_dams)

    if cp_cap_sum > 0 and all(k in parsed_dams for k in chao_phraya_keys):
        cp_pct = round((cp_storage_sum / cp_cap_sum) * 100.0, 1)
        param_results["chao_phraya_4dam_combined"] = {
            "label": "Chao Phraya 4-Dam Combined Storage",
            "value": f"{cp_pct}%",
            "unit": f"{cp_storage_sum:,.0f} / {cp_cap_sum:,.0f} MCM (Usable: {cp_usable_sum:,.0f} MCM)",
            "period": "Latest Report",
            "description": "Primary BKK & Samut Prakan Water Supply & Salinity Buffer"
        }

    for k, info in parsed_dams.items():
        param_results[f"dam_{k}"] = {
            "label": info["label"],
            "value": f"{info['storage_pct']}%",
            "unit": f"{info['storage_mcm']:,.0f} / {info['capacity_mcm']:,.0f} MCM (Usable: {info['usable_mcm']:,.0f} MCM)",
            "period": "Latest Report",
            "description": info["role"]
        }

    return param_results


def parse_enso_sstoi(body_text):
    lines = [line.strip() for line in body_text.splitlines() if line.strip() and not line.startswith("YR")]
    if not lines:
        return None
    parts = lines[-1].split()
    if len(parts) >= 10:
        yr, mon, nino34_anom = parts[0], parts[1], parts[9]
        return {
            "enso_nino34_anom": {
                "label": "ENSO (Niño 3.4 SST Anomaly)",
                "value": f"{float(nino34_anom):+.2f}",
                "unit": "°C",
                "period": f"{yr}-{int(mon):02d}",
                "description": "Equatorial Pacific Ocean SST Anomaly"
            }
        }
    return None


def parse_mjo_rmm(body_text):
    lines = [line.strip() for line in body_text.splitlines() if line.strip() and not line.startswith("RMM") and not line.startswith("year")]
    for line in reversed(lines):
        parts = line.split()
        if len(parts) >= 7:
            yr, mon, day, rmm1, rmm2, phase, amp = parts[:7]
            if phase != "999" and float(amp) < 900:
                return {
                    "mjo_phase_amp": {
                        "label": "MJO (Phase & Amplitude)",
                        "value": f"Phase {phase} (Amp {float(amp):.2f})",
                        "unit": "index",
                        "period": f"{yr}-{int(mon):02d}-{int(day):02d}",
                        "description": "Madden-Julian Oscillation Index"
                    }
                }
    return None


def parse_iod_dmi(body_text):
    lines = [line.strip() for line in body_text.splitlines() if line.strip() and len(line.split()) > 2]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for line in reversed(lines):
        parts = line.split()
        if len(parts) >= 13 and parts[0].isdigit():
            yr = parts[0]
            values = parts[1:13]
            for idx in range(11, -1, -1):
                try:
                    val = float(values[idx])
                    if val > -900:
                        return {
                            "iod_dmi_index": {
                                "label": "Indian Ocean Dipole (DMI)",
                                "value": f"{val:+.2f}",
                                "unit": "°C",
                                "period": f"{yr}-{months[idx]}",
                                "description": "Equatorial Indian Ocean SST Gradient"
                            }
                        }
                except ValueError:
                    continue
    return None


def parse_table_latest_value(body_text, param_key, label, unit, desc):
    lines = [line.strip() for line in body_text.splitlines() if line.strip() and line[:4].isdigit()]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for line in reversed(lines):
        line_clean = re.sub(r'(\d)-', r'\1 -', line)
        parts = line_clean.split()
        if len(parts) >= 2 and parts[0].isdigit():
            yr = parts[0]
            values = parts[1:]
            for idx in range(min(12, len(values)) - 1, -1, -1):
                try:
                    val = float(values[idx])
                    if val > -900:
                        return {
                            param_key: {
                                "label": label,
                                "value": f"{val:+.2f}",
                                "unit": unit,
                                "period": f"{yr}-{months[idx]}",
                                "description": desc
                            }
                        }
                except ValueError:
                    continue
    return None


def parse_pacific_heat_content(body_text):
    lines = [line.strip() for line in body_text.splitlines() if line.strip() and line[0].isdigit()]
    for line in reversed(lines):
        parts = line.split()
        if len(parts) >= 4 and parts[0].isdigit():
            yr, mon, heat_val = parts[0], parts[1], parts[3]
            try:
                val = float(heat_val)
                return {
                    "pacific_heat_content": {
                        "label": "Pacific Upper 300m Heat Content",
                        "value": f"{val:+.2f}",
                        "unit": "°C anomaly",
                        "period": f"{yr}-{int(mon):02d}",
                        "description": "Equatorial Subsurface Ocean Heat Anomaly (160E-80W)"
                    }
                }
            except ValueError:
                continue
    return None


TIDE_STATION_CODE = "GLF001"      # Fort Phrachulachomklao, Chao Phraya estuary (HII telemetry code)
TIDE_MAX_AGE_HOURS = 6            # an older reading is reported as unavailable, never as "live"
RIVER_STATION_CODES = ("C.2", "C.13", "CPY011")   # Nakhon Sawan, Chao Phraya Dam, Ayutthaya (HydroView nodes)


def parse_thaiwater_tide(body_text):
    """Live sea level at the Chao Phraya mouth from the Thaiwater water-level API (JSON).

    Finds station GLF001 and emits the same parameter the old HTML parser did, so the rest of the
    pipeline (paknam_gauge, macro page, status.py) is unchanged. The API has no predicted-tide
    extremes, so "chao_phraya_estuary_predicted_tide" is no longer produced.
    Returns None (with a reason via ValueError) when the station is missing or the reading is old.
    """
    doc = json.loads(body_text)
    rows = ((doc.get("waterlevel_data") or {}).get("data")) or []
    if not isinstance(rows, list) or not rows:
        raise ValueError("water-level API returned no station rows")

    match = None
    for row in rows:
        st = row.get("station") or {}
        if str(st.get("tele_station_oldcode", "")).upper() == TIDE_STATION_CODE:
            match = row
            break
    if match is None:
        near = []
        for row in rows:
            nm = ((row.get("station") or {}).get("tele_station_name") or {}).get("en") or ""
            code = (row.get("station") or {}).get("tele_station_oldcode") or ""
            if "chula" in nm.lower() or "pak nam" in nm.lower() or str(code).upper().startswith("GLF"):
                near.append(f"{code} {nm}".strip())
        raise ValueError(f"station {TIDE_STATION_CODE} not found among {len(rows)} stations; "
                         f"similar: {', '.join(near[:6]) or 'none'}")

    level = match.get("waterlevel_msl")
    stamp = match.get("waterlevel_datetime")
    if level in (None, ""):
        raise ValueError(f"station {TIDE_STATION_CODE} has no current level (value is empty)")
    level_val = float(level)
    when = datetime.strptime(str(stamp), "%Y-%m-%d %H:%M").replace(tzinfo=timezone(timedelta(hours=7)))
    age_h = (datetime.now(timezone.utc) - when).total_seconds() / 3600.0
    if age_h < -0.5:
        raise ValueError(f"reading time {stamp} is in the future; timezone assumption (ICT) may be wrong")
    if age_h > TIDE_MAX_AGE_HOURS:
        raise ValueError(f"latest {TIDE_STATION_CODE} reading ({stamp} ICT) is {age_h:.1f} h old")

    name = ((match.get("station") or {}).get("tele_station_name") or {}).get("en") or TIDE_STATION_CODE

    # Other Chao Phraya stations HydroView shows (its own source for them times out).
    river = {}
    for row in rows:
        st = row.get("station") or {}
        code = str(st.get("tele_station_oldcode", ""))
        if code not in RIVER_STATION_CODES:
            continue
        try:
            rl = float(row.get("waterlevel_msl"))
            obs = datetime.strptime(str(row.get("waterlevel_datetime")), "%Y-%m-%d %H:%M").strftime("%Y-%m-%dT%H:%M:00+07:00")
        except (TypeError, ValueError):
            continue
        try:
            flow = float(row.get("discharge"))
        except (TypeError, ValueError):
            flow = None
        river[code] = {"name": (st.get("tele_station_name") or {}).get("en") or code,
                       "level_msl": rl, "flow_cms": flow, "obs": obs,
                       "situation_level": row.get("situation_level")}

    return {
        "thaiwater_river_stations": {
            "label": "Thaiwater river stations (for HydroView)",
            "value": f"{len(river)} of {len(RIVER_STATION_CODES)}",
            "unit": "stations", "period": str(stamp), "description": "River stations from the Thaiwater water-level API",
            "stations": river
        },
        "chao_phraya_estuary_realtime_level": {
            "label": "Chao Phraya Estuary Real-Time Sea Level (Fort Phrachulachomklao)",
            "value": f"{level_val:+.3f}",
            "unit": "m MSL",
            "period": str(stamp),          # plain "YYYY-MM-DD HH:MM" (Bangkok time): HydroView parses this
            "description": f"Live telemetry sea level at the Chao Phraya River mouth (HII station {TIDE_STATION_CODE}, {name})"
        }
    }


def parse_navy_tide_summary(body_text):
    """Today's predicted high/low tide at Fort Chula (Navy station N03) from the HII FEWS summary.txt (CSV)."""
    import csv, io
    today = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d")
    rows = list(csv.DictReader(io.StringIO(body_text.lstrip("\ufeff"))))
    if not rows or "code" not in rows[0]:
        raise ValueError("summary.txt has no CSV header with a 'code' column")
    row = next((r for r in rows if (r.get("code") or "").strip().upper() == "N03"), None)
    if row is None:
        raise ValueError(f"station N03 not found among {len(rows)} rows")
    if (row.get("date") or "").strip() != today:
        raise ValueError(f"N03 prediction is for {row.get('date')}, not today ({today})")
    hi, lo = float(row["max_value"]), float(row["min_value"])
    return {
        "chao_phraya_estuary_predicted_tide": {
            "label": "Chao Phraya Estuary Predicted Tide Extremes (Fort Chula, N03)",
            "value": f"High {hi:+.2f} m @ {row['max_time'].strip()} | Low {lo:+.2f} m @ {row['min_time'].strip()}",
            "unit": "m MSL (time, ICT)",
            "period": today,
            "description": "Royal Thai Navy predicted tide extremes for today (station N03)"
        }
    }


# English and Thai name fragments used to find our six dams in the API (names differ by agency).
DAM_NAME_HINTS = {
    "bhumibol": ("BHUMIBOL", "ภูมิพล"),
    "sirikit": ("SIRIKIT", "สิริกิติ์"),
    "kwai_noi": ("KWAI NOI", "KHWAE NOI", "แควน้อย"),
    "pasak": ("PASAK", "ป่าสัก"),
    "srinagarind": ("SRINAGARIND", "SRINAKARIN", "ศรีนครินทร์"),
    "vajiralongkorn": ("VAJIRALONGKORN", "WACHIRALONGKORN", "วชิราลงกรณ"),
}
DAM_CAPACITY_MCM = {"bhumibol": 13462.0, "sirikit": 9510.0, "kwai_noi": 939.0, "pasak": 872.0,
                    "srinagarind": 17745.0, "vajiralongkorn": 8860.0}
DAM_FLOW_MAX_AGE_HOURS = 72


def parse_dam_flows(body_text):
    """Inflow, release, spill, level and storage for our dams from the ThaiWater analyst/dam API.

    The API lists every dam (several agencies, old rows from past years included), so for each of our
    dams the NEWEST row is used and rows older than DAM_FLOW_MAX_AGE_HOURS are ignored. Its own
    "dam_storage_percent" is 0 for the large dams, so percentages are NOT taken from here (the HII
    report remains the source for them). Units of inflow/release are as reported by the API.
    """
    doc = json.loads(body_text)
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        raise ValueError("unexpected response shape (no 'data' object)")
    rows = [r for v in data.values() if isinstance(v, list) for r in v if isinstance(r, dict)]
    if not rows:
        raise ValueError("no dam rows in response")
    now_ict = (datetime.now(timezone.utc) + timedelta(hours=7)).replace(tzinfo=None)   # naive Bangkok time
    best = {}
    for r in rows:
        names = " ".join(str(x) for x in ((r.get("dam") or {}).get("dam_name") or {}).values()).upper()
        stamp = str(r.get("dam_date") or "")
        for key, hints in DAM_NAME_HINTS.items():
            if any(h.upper() in names for h in hints):
                if key not in best or stamp > best[key]["dam_date"]:
                    best[key] = {"dam_date": stamp, "row": r}
    params = {}
    for key, rec in best.items():
        stamp, r = rec["dam_date"], rec["row"]
        try:
            when = datetime.strptime(stamp[:16], "%Y-%m-%d %H:%M" if len(stamp) >= 16 else "%Y-%m-%d")
        except ValueError:
            continue
        if (now_ict - when).total_seconds() / 3600.0 > DAM_FLOW_MAX_AGE_HOURS:
            continue                                    # an old row (e.g. 2020) is not a current reading
        num = lambda k: (float(r[k]) if r.get(k) not in (None, "") else None)
        inflow, released, spilled = num("dam_inflow"), num("dam_released"), num("dam_spilled")
        storage, level = num("dam_storage"), num("dam_level")
        cap = DAM_CAPACITY_MCM.get(key)
        params[f"dam_flow_{key}"] = {
            "label": f"{key} flow", "value": f"in {inflow if inflow is not None else '-'} / out {released if released is not None else '-'}",
            "unit": "as reported by API", "period": stamp, "description": "ThaiWater analyst/dam",
            "inflow": inflow, "released": released, "spilled": spilled, "level_m": level, "storage_mcm": storage,
            "storage_pct_calc": round(storage / cap * 100.0, 1) if storage is not None and cap else None,
            "as_of": stamp,
        }
    if not params:
        raise ValueError(f"none of our dams found with a reading newer than {DAM_FLOW_MAX_AGE_HOURS} h "
                         f"({len(rows)} rows, {len(best)} name matches)")
    return params


def extract_parameters(endpoint_key, content_type, body_text):
    try:
        if endpoint_key == "enso":
            return parse_enso_sstoi(body_text)
        elif endpoint_key == "mjo":
            return parse_mjo_rmm(body_text)
        elif endpoint_key == "iod":
            return parse_iod_dmi(body_text)
        elif endpoint_key == "cpac850":
            return parse_table_latest_value(body_text, "cpac850_wind_index", "CPAC 850mb Trade Wind Index", "m/s anomaly", "Central Pacific Zonal Wind Anomaly")
        elif endpoint_key == "soi":
            return parse_table_latest_value(body_text, "soi_index", "Southern Oscillation Index (SOI)", "index", "Tahiti-Darwin Sea Level Pressure Anomaly")
        elif endpoint_key == "olr":
            return parse_table_latest_value(body_text, "olr_index", "Outgoing Longwave Radiation (OLR)", "W/m² anomaly", "Equatorial Pacific Cloudiness Proxy")
        elif endpoint_key == "heat_content":
            return parse_pacific_heat_content(body_text)
        elif endpoint_key == "dam_storage":
            return parse_dam_storage_bkk_region(body_text)
        elif endpoint_key == "chao_phraya_tide":
            return parse_thaiwater_tide(body_text)
        elif endpoint_key == "dam_flows":
            return parse_dam_flows(body_text)
        elif endpoint_key == "tide_prediction":
            return parse_navy_tide_summary(body_text)
    except Exception as e:
        return {"error": f"Parsing failed: {str(e)}"}
    return None


def classify_format(content_type: str, body_text: str) -> str:
    text_head = body_text.strip()[:300].lower()
    if "text/html" in content_type.lower() or "<html" in text_head or "<!doctype html" in text_head:
        return "HTML Document (Web Report)"
    if "application/json" in content_type.lower() or (text_head.startswith("{") or text_head.startswith("[")):
        return "JSON Structured Data"
    lines = [line.strip() for line in body_text.splitlines() if line.strip() and not line.strip().startswith("#")]
    if lines and len(lines[0].split()) >= 3:
        return "Fixed-Width / Space-Delimited ASCII Table"
    return "Plain Text / Unformatted"


def fetch_endpoint(endpoint: dict) -> tuple[dict, dict]:
    url = endpoint["url"]
    name = endpoint["name"]
    key = endpoint["key"]
    start_time = time.time()

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    headers.update(endpoint.get("headers") or {})
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    host = (urlparse(url).hostname or "").lower()
    if any(host == h or host.endswith("." + h) for h in VERIFY_EXEMPT_HOSTS):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    diagnostic = {
        "key": key,
        "name": name,
        "url": url,
        "success": False,
        "status_code": None,
        "content_type": "Unknown",
        "response_time_sec": None,
        "size_bytes": 0,
        "line_count": 0,
        "data_format": "N/A",
        "sample_preview": [],
        "error": None
    }

    extracted_params = {}

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as response:
            elapsed = round(time.time() - start_time, 3)
            status = response.status
            content_type = response.headers.get("Content-Type", "Unknown")
            raw_bytes = response.read()

            try:
                body_text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                body_text = raw_bytes.decode("latin-1", errors="replace")

            lines = body_text.splitlines()
            non_empty_lines = [line for line in lines if line.strip()]

            diagnostic["success"] = True
            diagnostic["status_code"] = status
            diagnostic["content_type"] = content_type
            diagnostic["response_time_sec"] = elapsed
            diagnostic["size_bytes"] = len(raw_bytes)
            diagnostic["line_count"] = len(lines)
            diagnostic["data_format"] = classify_format(content_type, body_text)
            diagnostic["sample_preview"] = non_empty_lines[:8]

            parsed = extract_parameters(key, content_type, body_text)
            if parsed and "error" in parsed and len(parsed) == 1:
                diagnostic["parse_error"] = parsed["error"]
            elif parsed:
                extracted_params.update(parsed)
            else:
                diagnostic["parse_error"] = "Fetched, but no values could be parsed (page layout may have changed)"

    except urllib.error.HTTPError as e:
        diagnostic["status_code"] = e.code
        diagnostic["error"] = f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        diagnostic["error"] = f"URL Connection Error: {e.reason}"
    except TimeoutError:
        diagnostic["error"] = "Connection timed out (20s limit)"
    except Exception as e:
        diagnostic["error"] = f"Fetch failed: {str(e)}"

    return diagnostic, extracted_params


def extract_numeric_teleconnections(summary_parameters):
    tc = {
        "enso": None,
        "iod": None,
        "mjo_phase": None,
        "mjo_amplitude": None,
        "cpac850_trade_winds": None,
        "soi": None,
        "olr": None,
        "ocean_heat_content": None
    }

    if "enso_nino34_anom" in summary_parameters:
        try:
            tc["enso"] = float(summary_parameters["enso_nino34_anom"]["value"])
        except (ValueError, KeyError):
            pass

    if "iod_dmi_index" in summary_parameters:
        try:
            tc["iod"] = float(summary_parameters["iod_dmi_index"]["value"])
        except (ValueError, KeyError):
            pass

    if "mjo_phase_amp" in summary_parameters:
        try:
            val_str = summary_parameters["mjo_phase_amp"]["value"]
            match = re.search(r'Phase\s+(\d+)\s*\(Amp\s+([\d.]+)\)', val_str)
            if match:
                tc["mjo_phase"] = int(match.group(1))
                tc["mjo_amplitude"] = float(match.group(2))
        except (ValueError, KeyError):
            pass

    if "cpac850_wind_index" in summary_parameters:
        try:
            tc["cpac850_trade_winds"] = float(summary_parameters["cpac850_wind_index"]["value"])
        except (ValueError, KeyError):
            pass

    if "soi_index" in summary_parameters:
        try:
            tc["soi"] = float(summary_parameters["soi_index"]["value"])
        except (ValueError, KeyError):
            pass

    if "olr_index" in summary_parameters:
        try:
            tc["olr"] = float(summary_parameters["olr_index"]["value"])
        except (ValueError, KeyError):
            pass

    if "pacific_heat_content" in summary_parameters:
        try:
            tc["ocean_heat_content"] = float(summary_parameters["pacific_heat_content"]["value"])
        except (ValueError, KeyError):
            pass

    return tc


def extract_dams_data(summary_parameters):
    dams = {
        "combined_chao_phraya_storage_pct": None,
        "bhumibol": {
            "current_storage_pct": None,
            "status": "N/A"
        },
        "sirikit": {
            "current_storage_pct": None,
            "status": "N/A"
        },
        "kwai_noi": {
            "current_storage_pct": None,
            "status": "N/A"
        },
        "pasak": {
            "current_storage_pct": None,
            "status": "N/A"
        },
        "srinagarind": {
            "current_storage_pct": None,
            "status": "N/A"
        },
        "vajiralongkorn": {
            "current_storage_pct": None,
            "status": "N/A"
        }
    }

    if "chao_phraya_4dam_combined" in summary_parameters:
        try:
            val_str = summary_parameters["chao_phraya_4dam_combined"]["value"].replace("%", "")
            dams["combined_chao_phraya_storage_pct"] = float(val_str)
        except (ValueError, KeyError):
            pass

    dam_mapping = [
        ("dam_bhumibol", "bhumibol"),
        ("dam_sirikit", "sirikit"),
        ("dam_kwai_noi", "kwai_noi"),
        ("dam_pasak", "pasak"),
        ("dam_srinagarind", "srinagarind"),
        ("dam_vajiralongkorn", "vajiralongkorn")
    ]

    for summary_key, target_key in dam_mapping:
        if summary_key in summary_parameters:
            try:
                val_str = summary_parameters[summary_key]["value"].replace("%", "")
                pct = float(val_str)
                dams[target_key]["current_storage_pct"] = pct
                dams[target_key]["status"] = f"{pct:.1f}%"
            except (ValueError, KeyError):
                pass

    # Optional flow details (inflow, release, spill, level) from the ThaiWater analyst API.
    for target_key in dams:
        if isinstance(dams[target_key], dict):
            fp = summary_parameters.get(f"dam_flow_{target_key}")
            if isinstance(fp, dict):
                dams[target_key]["flow"] = {k: fp.get(k) for k in
                                            ("inflow", "released", "spilled", "level_m", "storage_mcm", "storage_pct_calc", "as_of")}

    return dams


def extract_paknam_gauge(summary_parameters):
    """
    Extracts live sea level gauge telemetry (Station GLF001) and predictive tide bounds (Station N03)
    from Chao Phraya Estuary / Fort Phrachulachomklao. Strictly reports data as unavailable
    if the endpoint fetch fails.
    """
    paknam = {
        "water_level_msl": None,
        "tide_state": "Data Unavailable",
        "surge_risk": "N/A",
        "buffer_remaining_meters": None,
        "flood_wall_threshold_msl": 1.80,
        "period": None,
        "status": "DATA_UNAVAILABLE",
        "message": "Pak Nam tide gauge endpoint is not available."
    }

    if "chao_phraya_estuary_realtime_level" in summary_parameters:
        try:
            param_rt = summary_parameters["chao_phraya_estuary_realtime_level"]
            val_str = param_rt["value"]
            level_val = float(val_str)
            threshold = 1.80
            buffer_rem = round(threshold - level_val, 3)

            if level_val >= 1.50:
                surge_risk = "HIGH"
            elif level_val >= 1.00:
                surge_risk = "MODERATE"
            else:
                surge_risk = "LOW"

            tide_state_str = "Live Gauge Telemetry"
            if "chao_phraya_estuary_predicted_tide" in summary_parameters:
                tide_state_str = summary_parameters["chao_phraya_estuary_predicted_tide"]["value"]

            paknam = {
                "water_level_msl": level_val,
                "tide_state": tide_state_str,
                "surge_risk": surge_risk,
                "buffer_remaining_meters": buffer_rem,
                "flood_wall_threshold_msl": threshold,
                "period": param_rt.get("period"),
                "status": "SUCCESS",
                "message": "Pak Nam tide gauge telemetry active."
            }
        except (ValueError, KeyError):
            pass

    return paknam


def compute_zones(tc):
    enso = tc.get("enso") if tc.get("enso") is not None else 0.0
    iod = tc.get("iod") if tc.get("iod") is not None else 0.0
    mjo_phase = tc.get("mjo_phase")
    mjo_amp = tc.get("mjo_amplitude") if tc.get("mjo_amplitude") is not None else 0.0

    if mjo_phase in [3, 4, 5]:
        mjo_effect_base = 8.5 * mjo_amp
    elif mjo_phase in [1, 2, 7, 8]:
        mjo_effect_base = -6.0 * mjo_amp
    else:
        mjo_effect_base = 1.5 * mjo_amp

    enso_effect_base = -12.0 * enso
    iod_effect_base = -10.0 * iod

    zone_definitions = [
        {
            "id": "ZONE01",
            "en": "Bangkok Metropolitan & Delta",
            "basin": "Lower Chao Phraya Basin",
            "lat": 13.75,
            "lon": 100.50,
            "sens_enso": 1.0,
            "sens_iod": 0.8,
            "sens_mjo": 1.1,
            "mechanics": "Lower basin urban flood vulnerability driven by upstream river discharge and tidal backflow."
        },
        {
            "id": "ZONE02",
            "en": "Ping River Headwaters (Bhumibol)",
            "basin": "Upper Chao Phraya Basin",
            "lat": 17.24,
            "lon": 98.97,
            "sens_enso": 1.1,
            "sens_iod": 1.0,
            "sens_mjo": 0.9,
            "mechanics": "Northern mountain catchments feeding Bhumibol Reservoir."
        },
        {
            "id": "ZONE03",
            "en": "Nan River Headwaters (Sirikit)",
            "basin": "Upper Chao Phraya Basin",
            "lat": 17.76,
            "lon": 100.56,
            "sens_enso": 1.1,
            "sens_iod": 0.9,
            "sens_mjo": 1.0,
            "mechanics": "Northeastern headwater catchments feeding Sirikit Dam reservoir storage."
        },
        {
            "id": "ZONE04",
            "en": "Pasak River Basin & Ayutthaya",
            "basin": "Pasak Basin / Chao Phraya Confluence",
            "lat": 14.80,
            "lon": 101.10,
            "sens_enso": 0.9,
            "sens_iod": 1.1,
            "sens_mjo": 1.0,
            "mechanics": "Mid-basin flood risk zone and agricultural buffer for Greater Bangkok."
        },
        {
            "id": "ZONE05",
            "en": "Mae Klong Basin & Kanchanaburi",
            "basin": "Mae Klong River Basin",
            "lat": 14.02,
            "lon": 99.53,
            "sens_enso": 1.0,
            "sens_iod": 1.2,
            "sens_mjo": 0.8,
            "mechanics": "Western headwater system providing freshwater push for Bangkok salinity suppression."
        },
        {
            "id": "ZONE06",
            "en": "Eastern Seaboard & Bang Pakong",
            "basin": "Bang Pakong / Eastern Gulf Basin",
            "lat": 13.00,
            "lon": 101.20,
            "sens_enso": 0.8,
            "sens_iod": 0.9,
            "sens_mjo": 1.2,
            "mechanics": "Eastern Gulf coastal monsoon surge region."
        }
    ]

    zones = []
    for zd in zone_definitions:
        mjo_eff = round(mjo_effect_base * zd["sens_mjo"], 1)
        enso_eff = round(enso_effect_base * zd["sens_enso"], 1)
        iod_eff = round(iod_effect_base * zd["sens_iod"], 1)
        net_anom = round(mjo_eff + enso_eff + iod_eff, 1)

        zones.append({
            "id": zd["id"],
            "en": zd["en"],
            "basin": zd["basin"],
            "lat": zd["lat"],
            "lon": zd["lon"],
            "mjo_effect": mjo_eff,
            "enso_effect": enso_eff,
            "iod_effect": iod_eff,
            "net_anomaly": net_anom,
            "mechanics": zd["mechanics"]
        })

    return zones


def load_previous():
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def hours_since(iso_str):
    if not iso_str:
        return None
    try:
        d = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).total_seconds() / 3600.0
    except ValueError:
        return None


def param_matches(name, patterns):
    return any(name == p or (p.endswith("_") and name.startswith(p)) for p in patterns)


def carry_forward(prev, health, params):
    """Re-use the last good value of slow-changing sources that failed this run (labelled, age-capped)."""
    if not prev:
        return
    prev_params = prev.get("summary_parameters") or {}
    prev_ts = prev.get("updated_at_utc") or prev.get("timestamp_utc")
    for key, (max_hours, patterns) in CARRY_RULES.items():
        if key not in health or health[key]["parsed"]:
            continue
        oldest = None
        for name, val in prev_params.items():
            if not isinstance(val, dict) or name in params or not param_matches(name, patterns):
                continue
            origin = val.get("carried_from_utc") or prev_ts
            age = hours_since(origin)
            if age is None or age > max_hours:
                continue
            carried = dict(val)
            carried["carried_from_utc"] = origin
            params[name] = carried
            if oldest is None or origin < oldest:
                oldest = origin
        if oldest:
            health[key]["carried"] = True
            health[key]["carried_from_utc"] = oldest


def main():
    print(f"Fetching live weather, climate, and hydrological telemetry from {len(ENDPOINTS)} endpoints...")
    prev = load_previous()
    results = []
    all_summary_parameters = {}
    health = {}

    for ep in ENDPOINTS:
        print(f" -> Fetching {ep['name']} ({ep['url']})")
        diag, params = fetch_endpoint(ep)
        results.append(diag)
        health[ep["key"]] = {
            "fetched": bool(diag["success"]),
            # the tide only counts as parsed when the live level is present (not just the prediction)
            "parsed": bool(params) and (ep["key"] != "chao_phraya_tide" or "chao_phraya_estuary_realtime_level" in params),
            "optional": ep["key"] in OPTIONAL_SOURCES,
            "carried": False,
            "carried_from_utc": None,
            "error": diag.get("error") or diag.get("parse_error"),
        }
        if params:
            all_summary_parameters.update(params)

    successful = sum(1 for r in results if r["success"])
    failed = len(results) - successful
    core = [k for k in health if k not in OPTIONAL_SOURCES]
    parsed_count = sum(1 for k in core if health[k]["parsed"])      # core sources only

    for key, h in health.items():
        print(f"    {key:18s} fetched={h['fetched']!s:5} parsed={h['parsed']!s:5} {h['error'] or ''}")

    if parsed_count == 0:
        print("ERROR: no source could be parsed. Leaving the existing data.json untouched.", file=sys.stderr)
        return 1

    river_param = all_summary_parameters.pop("thaiwater_river_stations", None)
    river_stations = (river_param or {}).get("stations") or {}
    carry_forward(prev, health, all_summary_parameters)
    usable_count = sum(1 for k in core if health[k]["parsed"] or health[k]["carried"])

    is_live = successful > 0
    status_code = "SUCCESS" if is_live else "FETCH_FAILED"
    timestamp_str = datetime.now(timezone.utc).isoformat()

    teleconnections = extract_numeric_teleconnections(all_summary_parameters)
    dams = extract_dams_data(all_summary_parameters)
    paknam_gauge = extract_paknam_gauge(all_summary_parameters)
    zones = compute_zones(teleconnections)

    output_payload = {
        "timestamp_utc": timestamp_str,
        "updated_at_utc": timestamp_str,
        "is_live": is_live,
        "status": status_code,
        "total_endpoints": len(ENDPOINTS),
        "successful_count": successful,
        "failed_count": failed,
        "core_endpoints": len(core),
        "parsed_count": parsed_count,
        "usable_count": usable_count,
        "source_health": health,
        "dams_missing": [k for k in DAM_KEYS if f"dam_{k}" not in all_summary_parameters],
        "river_stations": river_stations,
        "river_stations_missing": [c for c in RIVER_STATION_CODES if c not in river_stations],
        "teleconnections": teleconnections,
        "dams": dams,
        "paknam_gauge": paknam_gauge,
        "summary_parameters": all_summary_parameters,
        "results": results,
        "zones": zones
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(f"Completed fetch. {parsed_count}/{len(core)} core sources parsed "
          f"({usable_count} usable incl. carried forward). Saved to '{OUTPUT_FILE}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
