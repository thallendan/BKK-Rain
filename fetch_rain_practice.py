#!/usr/bin/env python3
"""
fetch_rain_practice.py

Fetches live 24-hour rainfall telemetry from ThaiWater v3 public API:
https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24

Saves:
1. 'rain_dump.json' - Full raw JSON response payload.
2. 'rain_structure.json' - Structural summary, station counts, field names,
   and sample record for inspection.
"""

import json
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

RAIN_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24"


def main():
    print(f"Fetching 24h rainfall telemetry from {RAIN_URL}...")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*"
    }

    req = urllib.request.Request(RAIN_URL, headers=headers)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as response:
            raw_bytes = response.read()
            body_text = raw_bytes.decode("utf-8", errors="replace")

            # 1. Save raw JSON dump
            data = json.loads(body_text)
            with open("rain_dump.json", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f" -> Raw JSON saved to 'rain_dump.json' ({len(raw_bytes):,} bytes).")

            # 2. Extract structural overview
            rain_records = []
            if isinstance(data, dict):
                rain_records = data.get("rain24", {}).get("data", [])
                if not rain_records and "data" in data:
                    rain_records = data.get("data", [])
            elif isinstance(data, list):
                rain_records = data

            sample_record = rain_records[0] if rain_records else {}
            
            # Analyze fields in sample record
            sample_station = sample_record.get("station", {}) if isinstance(sample_record, dict) else {}
            
            structure_summary = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "endpoint_url": RAIN_URL,
                "total_stations_returned": len(rain_records),
                "root_keys": list(data.keys()) if isinstance(data, dict) else "Root is Array",
                "sample_record_keys": list(sample_record.keys()) if isinstance(sample_record, dict) else [],
                "sample_station_keys": list(sample_station.keys()) if isinstance(sample_station, dict) else [],
                "sample_record_preview": sample_record
            }

            with open("rain_structure.json", "w", encoding="utf-8") as f:
                json.dump(structure_summary, f, indent=2, ensure_ascii=False)

            print(f" -> Parsed {len(rain_records)} rainfall stations.")
            print(" -> Inspection structure saved to 'rain_structure.json'.")

    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.reason}", file=sys.stderr)
    except Exception as e:
        print(f"Error fetching rain telemetry: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
