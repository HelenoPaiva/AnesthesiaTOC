#!/usr/bin/env python3
"""
Build journal_metrics.json using SCImago Journal Rank (SJR).

- Reads sources.json (journals with a single ISSN)
- Downloads SCImago journal rankings export
- Detects the latest year present in the dataset
- Extracts SJR values for the ISSNs in sources.json
- Writes journal_metrics.json (journal-level metrics only)

Failure behavior:
- If fetch/parse fails, exits non-zero and DOES NOT write output.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple

import requests


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SOURCES_PATH = os.path.join(ROOT, "sources.json")
OUT_PATH = os.path.join(ROOT, "journal_metrics.json")

# SCImago export endpoint (often labeled out=xls but is semicolon-delimited text)
SJR_EXPORT_URL = "https://www.scimagojr.com/journalrank.php?out=xls"

# A browser-like UA helps with sites that block generic clients
UA = os.getenv(
    "SJR_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

TIMEOUT = 60


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_issn(raw: str) -> str:
    """
    Normalize ISSN into ####-#### (uppercase), removing spaces.
    Accepts input with/without hyphen.
    """
    s = (raw or "").strip().upper().replace(" ", "")
    s = s.replace("–", "-")  # en-dash just in case
    if re.fullmatch(r"\d{4}-\d{3}[\dX]", s):
        return s
    if re.fullmatch(r"\d{7}[\dX]", s):
        return s[:4] + "-" + s[4:]
    return s


def fetch_scimago_export() -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/plain,text/csv,application/octet-stream,*/*",
        "Referer": "https://www.scimagojr.com/journalrank.php",
    }
    r = requests.get(SJR_EXPORT_URL, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def detect_columns(fieldnames) -> Tuple[str, str, str, str]:
    """
    Return column names for: year, issn, sjr, title.
    SCImago export column names can vary slightly.
    """
    if not fieldnames:
        raise ValueError("No columns detected in SCImago export")

    cols = {c.strip(): c for c in fieldnames}

    # Common expected names:
    year_col = None
    for candidate in ["Year", "year"]:
        if candidate in cols:
            year_col = cols[candidate]
            break

    issn_col = None
    for candidate in ["Issn", "ISSN", "Issn ", "issn"]:
        if candidate in cols:
            issn_col = cols[candidate]
            break

    sjr_col = None
    for candidate in ["SJR", "Sjr", "sjr"]:
        if candidate in cols:
            sjr_col = cols[candidate]
            break

    title_col = None
    for candidate in ["Title", "title", "Journal", "Source Title"]:
        if candidate in cols:
            title_col = cols[candidate]
            break

    if not (year_col and issn_col and sjr_col and title_col):
        raise ValueError(
            f"Missing required columns. Detected: {fieldnames[:20]}"
        )

    return year_col, issn_col, sjr_col, title_col


def parse_sjr_float(value: str) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # SCImago sometimes uses comma as decimal separator in some exports; accept both
    s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def main() -> int:
    if not os.path.exists(SOURCES_PATH):
        print(f"[ERROR] sources.json not found at {SOURCES_PATH}", file=sys.stderr)
        return 2

    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        sources = json.load(f)

    # Single ISSN per journal (per your decision)
    wanted_issns = []
    for s in sources:
        issn = s.get("issn")
        if isinstance(issn, list):
            # You decided: single ISSN per journal; take first defensively
            issn = issn[0] if issn else ""
        wanted_issns.append(normalize_issn(str(issn)))

    wanted_set = set([x for x in wanted_issns if x])

    # Fetch SCImago export (DO NOT write output until successful parse)
    text = fetch_scimago_export()

    # Parse semicolon-delimited text
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    year_col, issn_col, sjr_col, title_col = detect_columns(reader.fieldnames)

    # First pass: detect latest year
    latest_year = None
    rows_cache = []

    for row in reader:
        rows_cache.append(row)
        y_raw = (row.get(year_col) or "").strip()
        try:
            y = int(y_raw)
        except Exception:
            continue
        if latest_year is None or y > latest_year:
            latest_year = y

    if latest_year is None:
        raise ValueError("Could not detect latest year in SCImago export")

    # Second pass: collect SJR for wanted ISSNs from latest year only
    by_issn: Dict[str, Dict[str, Any]] = {}
    matched = 0

    for row in rows_cache:
        y_raw = (row.get(year_col) or "").strip()
        try:
            y = int(y_raw)
        except Exception:
            continue
        if y != latest_year:
            continue

        issn_raw = (row.get(issn_col) or "").strip()
        if not issn_raw:
            continue

        # SCImago sometimes provides multiple ISSNs in one field separated by comma
        issns = [normalize_issn(x) for x in issn_raw.split(",")]

        sjr_val = parse_sjr_float(row.get(sjr_col))
        if sjr_val is None:
            continue

        title_source = (row.get(title_col) or "").strip()

        for issn in issns:
            if not issn or issn not in wanted_set:
                continue

            # If duplicate entries appear, keep the max SJR (safe and stable)
            existing = by_issn.get(issn)
            if existing is None or float(existing.get("sjr", 0)) < sjr_val:
                by_issn[issn] = {
                    "sjr": sjr_val,
                    "title_source": title_source,
                }

    matched = len(by_issn)

    out = {
        "generated_at": iso_now(),
        "source_name": "SCImago Journal Rank (SJR)",
        "source_note": "SJR is treated as an annual journal-level metric; the dashboard uses the latest year available in the SCImago export at update time.",
        "sjr_year": latest_year,
        "coverage": {
            "matched": matched,
            "total_sources": len(sources),
        },
        "by_issn": by_issn,
    }

    # Write output only after everything succeeded
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote journal_metrics.json (year={latest_year}, matched={matched}/{len(sources)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
