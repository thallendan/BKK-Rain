#!/usr/bin/env python3
"""fetch_practice.py

Fetches live climate and hydrological data from 8 endpoints, parses current key scalar
parameters (ENSO, MJO, IOD, CPAC850, SOI, OLR, Pacific Heat Content, and Thailand Dam Storage),
and saves the standardized JSON structure strictly to 'data_practice.json'.
"""

from html.parser import HTMLParser
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Standardized Target Output File
OUTPUT_FILE = "data_practice.json"

ENDPOINTS = [
    {
        "key": "enso",
        "name": "ENSO (Niño Indices)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/sstoi.indices",
    },
    {
        "key": "mjo",
        "name": "MJO (RMM Index)",
        "url": "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt",
    },
    {
        "key": "iod",
        "name": "IOD (Indian Ocean Dipole)",
        "url": "https://psl.noaa.gov/data/timeseries/month/data/dmi.had.long.data",
    },
    {
        "key": "cpac850",
        "name": "CPAC850 (Zonal Wind Anomaly)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/cpac850",
    },
    {
        "key": "soi",
        "name": "Tahiti-Darwin Sea Pressure Difference (SOI)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/soi",
    },
    {
        "key": "olr",
        "name": "Outgoing Longwave Radiation (OLR)",
        "url": "https://www.cpc.ncep.noaa.gov/data/indices/olr",
    },
    {
        "key": "heat_content",
        "name": "Pacific Heat Content Index",
        "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/ocean/index/heat_content_index.txt",
    },
    {
        "key": "dam_storage",
        "name": "Thailand Dam Storage (HII/RID)",
        "url": "https://tiwrmdev.hii.or.th/DATA/REPORT/php/rid_dam_1.php?lang=en",
    },
]


class DamHTMLTableParser(HTMLParser):
  """Standard library HTML parser fallback for extracting reservoir storage tables."""

  def __init__(self):
    super().__init__()
    self.in_tr = False
    self.in_td = False
    self.current_row = []
    self.rows = []

  def handle_starttag(self, tag, attrs):
    if tag.lower() in ("tr",):
      self.in_tr = True
      self.current_row = []
    elif tag.lower() in ("td", "th") and self.in_tr:
      self.in_td = True

  def handle_endtag(self, tag):
    if tag.lower() in ("tr",):
      self.in_tr = False
      if self.current_row:
        self.rows.append(self.current_row)
    elif tag.lower() in ("td", "th"):
      self.in_td = False

  def handle_data(self, data):
    if self.in_td:
      text = data.strip()
      if text:
        self.current_row.append(text)


def parse_dam_storage(html_text):
  """Parses HTML table from HII/RID dam report using BeautifulSoup or HTMLParser."""
  rows = []
  try:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_text, "html.parser")
    for tr in soup.find_all("tr"):
      cells = [
          td.get_text(strip=True) for td in tr.find_all(["td", "th"]) if td
      ]
      if cells:
        rows.append(cells)
  except ImportError:
    parser = DamHTMLTableParser()
    parser.feed(html_text)
    rows = parser.rows

  extracted_dams = {}
  targets = {
      "bhumibol": ["bhumibol", "ภูมิพล"],
      "sirikit": ["sirikit", "สิริกิติ์"],
      "pasak": ["pasak", "ป่าสัก"],
      "kwae_noi": ["kwae", "แควน้อย"],
  }

  for row in rows:
    row_str = " ".join(row).lower()
    for key, keywords in targets.items():
      if any(kw in row_str for kw in keywords):
        numbers = re.findall(r"\d+(?:\.\d+)?", row_str)
        if numbers:
          extracted_dams[key] = {"raw_row": row, "numbers": numbers}

  param_results = {}

  if "bhumibol" in extracted_dams:
    nums = extracted_dams["bhumibol"]["numbers"]
    param_results["thailand_dam_bhumibol"] = {
        "label": "Bhumibol Dam Storage",
        "value": f"{nums[-1]}%",
        "unit": "% capacity",
        "period": "Latest Report",
        "description": "Chao Phraya Ping River Upper Basin Storage",
    }

  if "sirikit" in extracted_dams:
    nums = extracted_dams["sirikit"]["numbers"]
    param_results["thailand_dam_sirikit"] = {
        "label": "Sirikit Dam Storage",
        "value": f"{nums[-1]}%",
        "unit": "% capacity",
        "period": "Latest Report",
        "description": "Chao Phraya Nan River Upper Basin Storage",
    }

  if not param_results:
    param_results["thailand_dam_status"] = {
        "label": "Thailand Dam Storage Status",
        "value": f"{len(rows)} Rows",
        "unit": "HTML Table Rows Extracted",
        "period": "Latest",
        "description": "RID Large Reservoir Storage Table",
    }

  return param_results


def parse_enso_sstoi(body_text):
  """Extracts latest Niño 3.4 SST Anomaly from NOAA sstoi.indices."""
  lines = [
      line.strip()
      for line in body_text.splitlines()
      if line.strip() and not line.startswith("YR")
  ]
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
            "description": "Equatorial Pacific Ocean SST Anomaly",
        }
    }
  return None


def parse_mjo_rmm(body_text):
  """Extracts latest MJO Phase & Amplitude from BOM rmm.74toRealtime.txt."""
  lines = [
      line.strip()
      for line in body_text.splitlines()
      if line.strip() and not line.startswith("RMM") and not line.startswith("year")
  ]
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
                "description": "Madden-Julian Oscillation Index",
            }
        }
  return None


def parse_iod_dmi(body_text):
  """Extracts latest Indian Ocean Dipole Mode Index (DMI) from NOAA PSL."""
  lines = [
      line.strip()
      for line in body_text.splitlines()
      if line.strip() and len(line.split()) > 2
  ]
  months = [
      "Jan",
      "Feb",
      "Mar",
      "Apr",
      "May",
      "Jun",
      "Jul",
      "Aug",
      "Sep",
      "Oct",
      "Nov",
      "Dec",
  ]
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
                    "description": "Equatorial Indian Ocean SST Gradient",
                }
            }
        except ValueError:
          continue
  return None


def parse_table_latest_value(body_text, param_key, label, unit, desc):
  """Generic monthly table parser for NOAA tabular text files."""
  lines = [
      line.strip()
      for line in body_text.splitlines()
      if line.strip() and line[:4].isdigit()
  ]
  months = [
      "Jan",
      "Feb",
      "Mar",
      "Apr",
      "May",
      "Jun",
      "Jul",
      "Aug",
      "Sep",
      "Oct",
      "Nov",
      "Dec",
  ]
  for line in reversed(lines):
    line_clean = re.sub(r"(\d)-", r"\1 -", line)
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
                    "description": desc,
                }
            }
        except ValueError:
          continue
  return None


def parse_pacific_heat_content(body_text):
  """Extracts Equatorial Pacific Upper 300m Heat Content Anomaly."""
  lines = [
      line.strip()
      for line in body_text.splitlines()
      if line.strip() and line[0].isdigit()
  ]
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
                "description": (
                    "Equatorial Subsurface Ocean Heat Anomaly (160E-80W)"
                ),
            }
        }
      except ValueError:
        continue
  return None


def extract_parameters(endpoint_key, content_type, body_text):
  """Routes response payload to specific parameter extraction functions."""
  try:
    if endpoint_key == "enso":
      return parse_enso_sstoi(body_text)
    elif endpoint_key == "mjo":
      return parse_mjo_rmm(body_text)
    elif endpoint_key == "iod":
      return parse_iod_dmi(body_text)
    elif endpoint_key == "cpac850":
      return parse_table_latest_value(
          body_text,
          "cpac850_wind_index",
          "CPAC 850mb Trade Wind Index",
          "m/s anomaly",
          "Central Pacific Zonal Wind Anomaly",
      )
    elif endpoint_key == "soi":
      return parse_table_latest_value(
          body_text,
          "soi_index",
          "Southern Oscillation Index (SOI)",
          "index",
          "Tahiti-Darwin Sea Level Pressure Anomaly",
      )
    elif endpoint_key == "olr":
      return parse_table_latest_value(
          body_text,
          "olr_index",
          "Outgoing Longwave Radiation (OLR)",
          "W/m² anomaly",
          "Equatorial Pacific Cloudiness Proxy",
      )
    elif endpoint_key == "heat_content":
      return parse_pacific_heat_content(body_text)
    elif endpoint_key == "dam_storage":
      return parse_dam_storage(body_text)
  except Exception as e:
    return {"error": f"Parsing failed: {str(e)}"}
  return None


def classify_format(content_type: str, body_text: str) -> str:
  """Classifies data format."""
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
    return "JSON Structured Data"
  lines = [
      line.strip()
      for line in body_text.splitlines()
      if line.strip() and not line.strip().startswith("#")
  ]
  if lines and len(lines[0].split()) >= 3:
    return "Fixed-Width / Space-Delimited ASCII Table"
  return "Plain Text / Unformatted"


def fetch_endpoint(endpoint: dict) -> tuple[dict, dict]:
  """Executes HTTP request, captures diagnostics, and parses scalar parameters."""
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
      "error": None,
  }

  extracted_params = {}

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
    diagnostic["error"] = "Connection timed out (15s limit)"
  except Exception as e:
    diagnostic["error"] = f"Fetch failed: {str(e)}"

  return diagnostic, extracted_params


def main():
  print(
      f"Fetching and parsing current data from {len(ENDPOINTS)} endpoints..."
  )
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
      "results": results,
  }

  with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(output_payload, f, indent=2, ensure_ascii=False)

  print(
      f"Completed. {successful}/{len(ENDPOINTS)} fetched successfully."
      f" Standardized output saved strictly to {OUTPUT_FILE}."
  )


if __name__ == "__main__":
  main()
