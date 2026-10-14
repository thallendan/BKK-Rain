#!/usr/bin/env python3
"""
fetch_dam_practice.py
Fetches the complete raw HTML from the Thailand Dam Storage endpoint
(https://tiwrmdev.hii.or.th/DATA/REPORT/php/rid_dam_1.php?lang=en), saves the
raw file to 'dam_dump.html', and extracts all table structures to 'dam_structure.json'
for precise inspection without making assumptions about table layout.
"""

from html.parser import HTMLParser
import json
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DAM_URL = "https://tiwrmdev.hii.or.th/DATA/REPORT/php/rid_dam_1.php?lang=en"


class DetailedTableParser(HTMLParser):

  def __init__(self):
    super().__init__()
    self.tables = []
    self.current_table = []
    self.current_row = []
    self.current_cell = []
    self.in_table = False
    self.in_tr = False
    self.in_cell = False

  def handle_starttag(self, tag, attrs):
    tag = tag.lower()
    if tag == "table":
      self.in_table = True
      self.current_table = []
    elif tag == "tr" and self.in_table:
      self.in_tr = True
      self.current_row = []
    elif tag in ("td", "th") and self.in_tr:
      self.in_cell = True
      self.current_cell = []

  def handle_endtag(self, tag):
    tag = tag.lower()
    if tag == "table":
      self.in_table = False
      if self.current_table:
        self.tables.append(self.current_table)
    elif tag == "tr" and self.in_table:
      self.in_tr = False
      if self.current_row:
        self.current_table.append(self.current_row)
    elif tag in ("td", "th") and self.in_tr:
      self.in_cell = False
      cell_text = " ".join("".join(self.current_cell).split())
      self.current_row.append(cell_text)

  def handle_data(self, data):
    if self.in_cell:
      self.current_cell.append(data)


def main():
  print(f"Fetching complete dam report from {DAM_URL}...")

  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          "AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/120.0.0.0 Safari/537.36"
      )
  }

  req = urllib.request.Request(DAM_URL, headers=headers)
  ctx = ssl.create_default_context()

  try:
    with urllib.request.urlopen(req, context=ctx, timeout=20) as response:
      raw_bytes = response.read()

      try:
        html_content = raw_bytes.decode("utf-8")
      except UnicodeDecodeError:
        html_content = raw_bytes.decode("latin-1", errors="replace")

      # 1. Save raw HTML dump
      with open("dam_dump.html", "w", encoding="utf-8") as f:
        f.write(html_content)
      print(
          " -> Raw HTML saved to 'dam_dump.html' ("
          f"{len(raw_bytes):,} bytes)."
      )

      # 2. Extract structural table data
      parser = DetailedTableParser()
      parser.feed(html_content)

      tables_extracted = parser.tables

      # Identify rows mentioning target Chao Phraya / Mae Klong dams
      target_keywords = [
          "bhumibol",
          "sirikit",
          "kwai",
          "kwae",
          "pasak",
          "pa sak",
          "srinagarind",
          "vajiralongkorn",
          "chao phraya",
          "ping",
          "nan",
      ]

      relevant_rows = []
      for t_idx, table in enumerate(tables_extracted):
        for r_idx, row in enumerate(table):
          row_str = " ".join(row).lower()
          if any(kw in row_str for kw in target_keywords):
            relevant_rows.append({
                "table_index": t_idx,
                "row_index": r_idx,
                "cell_count": len(row),
                "cells": row,
            })

      dump_summary = {
          "timestamp_utc": datetime.now(timezone.utc).isoformat(),
          "url": DAM_URL,
          "total_tables_found": len(tables_extracted),
          "tables_structure": [
              {
                  "table_index": idx,
                  "row_count": len(tbl),
                  "header_row_sample": tbl[0] if tbl else [],
              }
              for idx, tbl in enumerate(tables_extracted)
          ],
          "matched_chao_phraya_dams": relevant_rows,
          "all_tables_data": tables_extracted,
      }

      with open("dam_structure.json", "w", encoding="utf-8") as f:
        json.dump(dump_summary, f, indent=2, ensure_ascii=False)

      print(
          f" -> Parsed {len(tables_extracted)} table(s). Identified"
          f" {len(relevant_rows)} rows matching Chao Phraya basin dams."
      )
      print(" -> Inspection structure saved to 'dam_structure.json'.")

  except Exception as e:
    print(f"Error fetching dam report: {e}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
  main()
