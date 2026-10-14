import json
import urllib.request
from datetime import datetime, timezone

URL = "https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices"
OUTPUT_FILE = "data_practice.json"

def fetch_sstoi_data():
    req = urllib.request.Request(
        URL, 
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            if response.status != 200:
                return {
                    "is_live": False,
                    "status": "DATA_FAILED",
                    "error": f"HTTP status code {response.status}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "records": []
                }
            
            content = response.read().decode("utf-8")
            lines = content.strip().splitlines()
            
            records = []
            for line in lines:
                line_str = line.strip()
                if not line_str or line_str.startswith("YEAR"):
                    continue
                
                tokens = line_str.split()
                # Ensure line has required columns (YEAR, MON, + 8 SST/Anomaly columns)
                if len(tokens) >= 10 and tokens[0].isdigit():
                    try:
                        row_data = {
                            "year": int(tokens[0]),
                            "month": int(tokens[1]),
                            "nino12_sst": float(tokens[2]),
                            "nino12_anom": float(tokens[3]),
                            "nino3_sst": float(tokens[4]),
                            "nino3_anom": float(tokens[5]),
                            "nino4_sst": float(tokens[6]),
                            "nino4_anom": float(tokens[7]),
                            "nino34_sst": float(tokens[8]),
                            "nino34_anom": float(tokens[9]),
                        }
                        records.append(row_data)
                    except ValueError:
                        continue

            if not records:
                return {
                    "is_live": False,
                    "status": "DATA_FAILED",
                    "error": "No valid numeric records parsed from source content",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "records": []
                }

            return {
                "is_live": True,
                "status": "DATA_OK",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data_source": URL,
                "total_records": len(records),
                "latest_record": records[-1],
                "records": records
            }

    except Exception as e:
        return {
            "is_live": False,
            "status": "DATA_FAILED",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "records": []
        }

if __name__ == "__main__":
    result = fetch_sstoi_data()
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"Fetch completed. Status: {result['status']}. Written to {OUTPUT_FILE}.")
