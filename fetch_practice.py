#!/usr/bin/env python3
"""
fetch_practice.py
Tests live network connectivity and data retrieval for weather and hydrological
data endpoints relevant to Thailand rainfall and basin management. Writes output
strictly based on real server responses to practice_results.json without mock data.
"""

import json
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# 8 Weather & Hydrological Data Endpoints
ENDPOINTS = [
    {
        "name": "ENSO (Niño Indices)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices",
    },
    {
        "name": "MJO (RMM Index)",
        "url": "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt",
    },
    {
        "name": "IOD (Indian Ocean Dipole)",
        "url": "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.data",
    },
    {
        "name": "CPAC850 (Zonal Wind Anomaly)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/cpac850",
    },
    {
        "name": "Tahiti-Darwin Sea Pressure Difference (SOI)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/soi",
    },
    {
        "name": "Outgoing Longwave Radiation (OLR)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/olr",
    },
    {
        "name": "Pacific Heat Content Index",
        "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/ocean/index/heat_content_index.txt",
    },
    {
        "name": "Thailand Dam Storage (HII/RID)",
        "url": "https://tiwrmdev.hii.or.th/DATA/REPORT/php/rid_dam_1.php?lang=en",
    },
]


def classify_format(content_type: str, body_text: str) -> str:
    """Detects data structural format from HTTP content-type and payload content."""
    text_head = body_text.strip()[:300].lower()

    if (
        "text/html" in content_type.lower()
        or "<html" in text_head
        or "<!doctype html" in text_head
    ):
        return "HTML Document (Web Report)"

    if "application/json" in content_type.lower() or (
        text_head.startswith("{") or text_head.startswith("[")
    ):
        try:
            json.loads(body_text)
            return "JSON Structured Data"
        except json.JSONDecodeError:
            pass

    lines = [
        line.strip()
        for line in body_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return "Empty Text"

    sample_line = lines[0]
    if "," in sample_line:
        return "CSV (Comma-Separated Values)"
    elif "\t" in sample_line:
        return "TSV (Tab-Separated Values)"
    elif len(sample_line.split()) >= 3:
        return "Fixed-Width / Space-Delimited ASCII Table"

    return "Plain Text / Unformatted"


def fetch_endpoint(endpoint: dict) -> dict:
    """Executes live GET request and captures operational metadata."""
    url = endpoint["url"]
    name = endpoint["name"]
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

    result = {
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
        "error": None,
    }

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=15) as response:
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

            result["success"] = True
            result["status_code"] = status
            result["content_type"] = content_type
            result["response_time_sec"] = elapsed
            result["size_bytes"] = len(raw_bytes)
            result["line_count"] = len(lines)
            result["data_format"] = classify_format(content_type, body_text)
            result["sample_preview"] = non_empty_lines[
                :8
            ]  # First 8 non-empty sample lines

    except urllib.error.HTTPError as e:
        result["status_code"] = e.code
        result["error"] = f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        result["error"] = f"URL Connection Error: {e.reason}"
    except TimeoutError:
        result["error"] = "Connection timed out (15s limit)"
    except Exception as e:
        result["error"] = f"Fetch failed: {str(e)}"

    return result


def main():
    print(
        f"Testing live fetch connectivity across {len(ENDPOINTS)} weather endpoints..."
    )
    results = []

    for ep in ENDPOINTS:
        print(f" -> Fetching {ep['name']} ({ep['url']})")
        res = fetch_endpoint(ep)
        results.append(res)

    successful = sum(1 for r in results if r["success"])
    failed = len(results) - successful

    output_payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "total_endpoints": len(ENDPOINTS),
        "successful_count": successful,
        "failed_count": failed,
        "results": results,
    }

    with open("practice_results.json", "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2, ensure_ascii=False)

    print(
        f"Completed. {successful}/{len(ENDPOINTS)} endpoints fetched successfully. Saved to practice_results.json."
    )


if __name__ == "__main__":
    main()
