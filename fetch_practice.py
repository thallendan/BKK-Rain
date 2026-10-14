#!/usr/bin/env python3
"""
fetch_practice.py

Fetches live weather, climate, and hydrological data from 8 endpoints.
Parses current scalar parameters (including Chao Phraya and Mae Klong upstream
dams influencing Bangkok and Samut Prakan water supply and salinity control)
and saves standardized output strictly to 'data_practice.json'.
"""

import json
import ssl
import time
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone

OUTPUT_FILE = "data_practice.json"

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
    }
]


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

    # Normalize line breaks and html entities
    clean_html = html_text.replace("&nbsp;", " ").replace("<tr", "\n<tr")

    parsed_dams = {}

    for line in clean_html.splitlines():
        # Strip all HTML tags from the individual line
        plain_text = re.sub(r"<[^>]+>", " ", line)
        plain_text_lower = " ".join(plain_text.split()).lower()

        if not plain_text_lower:
            continue

        for dam_key, dam_meta in target_dams.items():
            if dam_key not in parsed_dams and any(kw in plain_text_lower for kw in dam_meta["keywords"]):
                # Extract all floating point / integer numbers from the plain line
                matches = re.findall(r'[-+]?\d+(?:,\d{3})*(?:\.\d+)?', plain_text)
                numbers = []
                for m in matches:
                    try:
                        numbers.append(float(m.replace(",", "")))
                    except ValueError:
                        pass

                # HII Number sequence per line:
                # [0: Cap MCM, 1: LastYear MCM, 2: PresentStorage MCM, 3: PresentStorage %, 4: Usable MCM, 5: Usable %]
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

    # Calculate Chao Phraya 4-Dam Combined Storage Metric
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


def main():
    print(f"Fetching and parsing current data from {len(ENDPOINTS)} endpoints...")
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

    output_payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "total_endpoints": len(ENDPOINTS),
        "successful_count": successful,
        "failed_count": failed,
        "summary_parameters": all_summary_parameters,
        "results": results
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(f"Completed. {successful}/{len(ENDPOINTS)} fetched successfully. Standardized output saved strictly to {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
