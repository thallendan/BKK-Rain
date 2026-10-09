from datetime import datetime, timezone
import json
import re
import urllib.request

# Target Telemetry Endpoints
URL_ENSO = "https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices"
URL_MJO = (
    "https://www.bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt"
)
URL_IOD = (
    "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.data"
)

# Standard browser User-Agent header to prevent 403 Forbidden blocks
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_text(url):
  """Fetches raw text content from remote HTTP endpoints with custom headers."""
  try:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as response:
      return response.read().decode("utf-8", errors="ignore")
  except Exception as e:
    print(f"Error fetching from {url}: {e}")
    return None


def parse_enso():
  """Parses latest Niño 3.4 SST Anomaly from NOAA CPC sstoi.indices."""
  raw_text = fetch_text(URL_ENSO)
  if not raw_text:
    return None

  lines = [l.strip() for l in raw_text.strip().split("\n") if l.strip()]
  if not lines:
    return None

  # Format: YEAR MON NINO1+2 ANOM NINO3 ANOM NINO3.4 ANOM NINO4 ANOM
  last_line = lines[-1].split()
  try:
    # Index 7 corresponds to NINO3.4 ANOM column
    return float(last_line[7])
  except (ValueError, IndexError):
    print("Failed to parse Niño 3.4 anomaly from sstoi.indices")
    return None


def parse_mjo():
  """Parses latest MJO phase and amplitude from BoM rmm.74toRealtime.txt."""
  raw_text = fetch_text(URL_MJO)
  if not raw_text:
    return None, None

  lines = [l.strip() for l in raw_text.strip().split("\n") if l.strip()]
  data_lines = [
      l for l in lines if not l.startswith("year") and not l.startswith("name")
  ]
  if not data_lines:
    return None, None

  last_line = data_lines[-1].split()
  try:
    phase = int(last_line[5])
    amp = float(last_line[6])
    return phase, amp
  except (ValueError, IndexError):
    print("Failed to parse MJO phase/amplitude from rmm.74toRealtime.txt")
    return None, None


def parse_iod():
  """Parses latest Dipole Mode Index (IOD) from NOAA PSL dmi.had.long.data."""
  raw_text = fetch_text(URL_IOD)
  if not raw_text:
    return None

  lines = [l.strip() for l in raw_text.strip().split("\n") if l.strip()]
  # Filter lines starting with a 4-digit year
  year_rows = [
      l for l in lines if l.split()[0].isdigit() and len(l.split()[0]) == 4
  ]

  for row in reversed(year_rows):
    tokens = row.split()[1:]  # skip year token
    # Extract the last valid non-missing value (> -900)
    valid_vals = [float(v) for v in tokens if float(v) > -900.0]
    if valid_vals:
      return valid_vals[-1]

  print("Failed to parse valid IOD value from dmi.had.long.data")
  return None


def compute_thailand_hydrology(enso_val, iod_val, mjo_phase, mjo_amp):
  """Computes regional monsoon teleconnection vectors for Thai river basins."""
  mjo_forcing_map = {
      1: -10.0,
      2: -5.0,
      3: 15.0,
      4: 35.0,
      5: 28.0,
      6: 5.0,
      7: -15.0,
      8: -20.0,
  }
  base_mjo_pct = mjo_forcing_map.get(mjo_phase, 0.0) * min(mjo_amp, 2.0)
  enso_pct = -20.0 * enso_val
  iod_pct = 15.0 * iod_val

  zones = [
      {
          "id": "ZONE01",
          "en": "Chao Phraya Basin & Delta (Bangkok Gate)",
          "basin": "Central Plains & Greater Bangkok",
          "lat": 14.20,
          "lon": 100.50,
          "mjo_effect": round(base_mjo_pct * 1.1, 1),
          "enso_effect": round(enso_pct, 1),
          "iod_effect": round(iod_pct, 1),
          "risk": (
              "High Flood" if (base_mjo_pct + enso_pct) > 15 else "Normal"
          ),
          "mechanics": (
              f"MJO Phase {mjo_phase} drives tropical convective moisture across"
              " the Gulf of Thailand into the Chao Phraya delta."
          ),
      },
      {
          "id": "ZONE02",
          "en": "Ping River Headwaters (Bhumibol Dam)",
          "basin": "Northern Highlands (Chiang Mai/Tak)",
          "lat": 18.79,
          "lon": 98.98,
          "mjo_effect": round(base_mjo_pct * 0.85, 1),
          "enso_effect": round(enso_pct * 0.95, 1),
          "iod_effect": round(iod_pct, 1),
          "risk": "Moderate Flood",
          "mechanics": (
              "Orographic lifting along the Tenasserim Range amplifies monsoon"
              " flows into Bhumibol Reservoir."
          ),
      },
      {
          "id": "ZONE03",
          "en": "Mun-Chi Confluence (Mekong Basin)",
          "basin": "Northeast Plateau (Ubon Ratchathani)",
          "lat": 15.23,
          "lon": 104.85,
          "mjo_effect": round(base_mjo_pct * 1.2, 1),
          "enso_effect": round(enso_pct * 1.1, 1),
          "iod_effect": round(iod_pct, 1),
          "risk": "Severe Flood",
          "mechanics": (
              "Monsoon troughing creates strong moisture convergence over"
              " Eastern Isan."
          ),
      },
      {
          "id": "ZONE04",
          "en": "Andaman Coast & Western Ranges",
          "basin": "Southern West Coast (Phuket/Ranong)",
          "lat": 8.20,
          "lon": 98.30,
          "mjo_effect": round(base_mjo_pct * 1.35, 1),
          "enso_effect": round(enso_pct * 1.1, 1),
          "iod_effect": round(iod_pct * 1.2, 1),
          "risk": "High Surge",
          "mechanics": (
              "Direct onshore squall lines triggered by active Indian Ocean"
              " convective waves."
          ),
      },
      {
          "id": "ZONE05",
          "en": "Eastern Seaboard Coast",
          "basin": "Rayong & Chonburi Marine",
          "lat": 12.80,
          "lon": 101.25,
          "mjo_effect": round(base_mjo_pct * 0.95, 1),
          "enso_effect": round(enso_pct * 0.9, 1),
          "iod_effect": round(iod_pct, 1),
          "risk": "Moderate Flood",
          "mechanics": (
              "Moisture convergence along Gulf of Thailand coastal boundaries."
          ),
      },
      {
          "id": "ZONE06",
          "en": "Nan River Catchment (Sirikit Dam)",
          "basin": "Upper North Catchment (Uttaradit)",
          "lat": 17.62,
          "lon": 100.09,
          "mjo_effect": round(base_mjo_pct * 0.9, 1),
          "enso_effect": round(enso_pct, 1),
          "iod_effect": round(iod_pct, 1),
          "risk": "Moderate Flood",
          "mechanics": (
              "Monsoonal rainfall contributing directly to northern storage"
              " reservoirs."
          ),
      },
  ]
  return zones


def main():
  enso_val = parse_enso()
  mjo_phase, mjo_amp = parse_mjo()
  iod_val = parse_iod()

  utc_now = datetime.now(timezone.utc).isoformat()

  # Strict Check: If any of the 3 key fetches fail, output a failure JSON
  if (
      enso_val is None
      or mjo_phase is None
      or mjo_amp is None
      or iod_val is None
  ):
    payload = {
        "is_live": False,
        "status": "FETCH_FAILED",
        "error_message": (
            "Failed to fetch live climate telemetry from one or more"
            " endpoints. Data has not been updated."
        ),
        "updated_at_utc": utc_now,
        "teleconnections": None,
        "zones": [],
    }
    print("CRITICAL WARNING: Data fetch failed for one or more endpoints.")
  else:
    zones = compute_thailand_hydrology(enso_val, iod_val, mjo_phase, mjo_amp)
    payload = {
        "is_live": True,
        "status": "OK",
        "updated_at_utc": utc_now,
        "teleconnections": {
            "enso": enso_val,
            "iod": iod_val,
            "mjo_phase": mjo_phase,
            "mjo_amplitude": mjo_amp,
        },
        "zones": zones,
    }
    print("SUCCESS: All 3 climate endpoints successfully fetched and synced.")

  with open("data.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)


if __name__ == "__main__":
  main()
