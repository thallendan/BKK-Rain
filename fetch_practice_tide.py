#!/usr/bin/env python3
"""
fetch_practice_tide.py

Standalone practice script to test fetching and parsing live sea level telemetry
and daily predictive tide extremes for the Chao Phraya River Estuary 
(Fort Phrachulachomklao, Samut Prakan / Bangkok Region) from HII Thaiwater.

Endpoint: https://tiwrmdev.hii.or.th/v3/sealevel
Output: data_practice_tide.json
"""

import json
import ssl
import time
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone

OUTPUT_FILE = "data_practice_tide.json"
TARGET_URL = "https://tiwrmdev.hii.or.th/v3/sealevel"


def parse_chao_phraya_tide(html_text):
    """
    Extracts real-time sea level gauge telemetry (GLF001) and daily 
    predictive tide bounds (N03) for Fort Phrachulachomklao.
    """
    params = {}
    clean_html = html_text.replace("&nbsp;", " ")

    # 1. Real-time Gauge Telemetry (Station GLF001 - Fort Phrachulachomklao)
    glf_match = re.search(r'<tr[^>]*rel="GLF001"[^>]*>(.*?)</tr>', clean_html, re.DOTALL | re.IGNORECASE)
    if glf_match:
        row_content = glf_match.group(1)
        td_values = re.findall(r'<td[^>]*>(.*?)</td>', row_content, re.DOTALL | re.IGNORECASE)
        td_clean = [re.sub(r'<[^>]+>', '', td).strip() for td in td_values]

        # Row layout: [Source/Status, Station Name, Date, Time, Level (m MSL)]
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

        # Row layout: [Station Name, High Tide Level (Time), Low Tide Level (Time), ...]
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

    return params


def fetch_tide_endpoint():
    start_time = time.time()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    req = urllib.request.Request(TARGET_URL, headers=headers)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    diagnostic = {
        "key": "chao_phraya_tide",
        "name": "Chao Phraya Estuary Sea Level & Tide (Fort Phrachulachomklao)",
        "url": TARGET_URL,
        "success": False,
        "status_code": None,
        "content_type": "Unknown",
        "response_time_sec": None,
        "size_bytes": 0,
        "line_count": 0,
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

            diagnostic["success"] = True
            diagnostic["status_code"] = status
            diagnostic["content_type"] = content_type
            diagnostic["response_time_sec"] = elapsed
            diagnostic["size_bytes"] = len(raw_bytes)
            diagnostic["line_count"] = len(lines)

            parsed = parse_chao_phraya_tide(body_text)
            if parsed:
                extracted_params.update(parsed)
            else:
                diagnostic["error"] = "HTTP request succeeded, but target station rows (GLF001/N03) were not found."

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
    print(f"Testing tide data fetch from: {TARGET_URL}")
    diag, params = fetch_tide_endpoint()

    output_payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "diagnostic": diag,
        "extracted_parameters": params
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    if diag["success"]:
        print(f"Success! Saved diagnostic and parameters to '{OUTPUT_FILE}'.")
        print(f"Parsed Parameters: {list(params.keys())}")
    else:
        print(f"Fetch failed: {diag['error']}")


if __name__ == "__main__":
    main()
