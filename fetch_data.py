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
"""

import json
import ssl
import time
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone

OUTPUT_FILE = "data.json"

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
        "name": "Chao Phraya Estuary Sea Level & Tide (Fort Phrachulachomklao)",
        "url": "https://tiwrmdev.hii.or.th/v3/sealevel"
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

    if cp_cap_sum > 0:
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
            return parse_chao_phraya_tide(body_text)
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

    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
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
            if parsed:
                extracted_params.update(parsed)

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


def main():
    print(f"Fetching live weather, climate, and hydrological telemetry from {len(ENDPOINTS)} endpoints...")
    results = []
    all_summary_parameters = {}

    for ep in ENDPOINTS:
        print(f" -> Fetching {ep['name']} ({ep['url']})")
        diag, params = fetch_endpoint(ep)
        results.append(diag)
        if params:
            all_summary_parameters.update(params)

    successful = sum(1 for r in results if r["success"])
    failed = len(results) - successful

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
        "teleconnections": teleconnections,
        "dams": dams,
        "paknam_gauge": paknam_gauge,
        "summary_parameters": all_summary_parameters,
        "results": results,
        "zones": zones
    }

    if not is_live:
        output_payload["error_message"] = "Failed to connect to live climate and hydrological data endpoints."

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(f"Completed fetch. {successful}/{len(ENDPOINTS)} endpoints updated. Saved strictly to '{OUTPUT_FILE}'.")


if __name__ == "__main__":
    main()
