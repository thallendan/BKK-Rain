import json
import re
from datetime import datetime
import requests

# User-specified BoM Live Telemetry Endpoints
MJO_URL = "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt"
IOD_NINO_URL = "https://www.bom.gov.au/clim_data/IDCK000072/rnino_3.4.txt"

# Standard browser headers to bypass BoM runner IP blocks
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def parse_mjo(raw_text):
    """Parses the latest MJO line from rmm.74toRealtime.txt"""
    lines = [line.strip() for line in raw_text.strip().split("\n") if line.strip()]
    data_lines = [l for l in lines if not l.startswith("year") and not l.startswith("name")]
    
    if not data_lines:
        raise ValueError("No valid MJO data lines found")
    
    # Parse last line (latest day)
    parts = re.split(r"\s+", data_lines[-1])
    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    rmm1, rmm2 = float(parts[3]), float(parts[4])
    phase = int(parts[5])
    amplitude = float(parts[6])
    
    return {
        "date": f"{year}-{month:02d}-{day:02d}",
        "rmm1": rmm1,
        "rmm2": rmm2,
        "phase": phase,
        "amplitude": amplitude,
    }


def parse_nino_iod(raw_text):
    """Parses latest Niño 3.4 / SST data from rnino_3.4.txt"""
    lines = [line.strip() for line in raw_text.strip().split("\n") if line.strip()]
    data_lines = [l for l in lines if re.match(r"^\d{4}", l)]
    
    if not data_lines:
        raise ValueError("No valid Niño SST data lines found")
    
    parts = re.split(r"\s+", data_lines[-1])
    # Extract latest temperature anomaly value
    nino34_anomaly = float(parts[-1]) if len(parts) > 1 else float(parts[0])
    
    return {
        "nino34_anomaly": nino34_anomaly
    }


def main():
    telemetry = {
        "is_live": False,
        "last_attempt": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "mjo": None,
        "nino34": None,
    }

    try:
        # Fetch MJO Index
        mjo_res = requests.get(MJO_URL, headers=HEADERS, timeout=15)
        mjo_res.raise_for_status()
        mjo_data = parse_mjo(mjo_res.text)

        # Fetch Niño / SST Anomaly Index
        nino_res = requests.get(IOD_NINO_URL, headers=HEADERS, timeout=15)
        nino_res.raise_for_status()
        nino_data = parse_nino_iod(nino_res.text)

        # Update telemetry object if both succeed
        telemetry["is_live"] = True
        telemetry["mjo"] = mjo_data
        telemetry["nino34"] = nino_data

        print("SUCCESS: Live telemetry successfully fetched from BoM.")

    except Exception as e:
        print(f"FETCH FAILED: {str(e)}")
        telemetry["error"] = str(e)

    # Save to data.json
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(telemetry, f, indent=2)


if __name__ == "__main__":
    main()
