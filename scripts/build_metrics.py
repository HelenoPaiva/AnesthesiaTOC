#!/usr/bin/env python3
"""
Build journal_metrics.json using SJR (SCImago Journal Rank).

GitHub Actions often gets 403 from scimagojr.com. To keep this reliable:
- Try SCImago export first
- Fallback to a public GitHub mirror CSV dataset (reliable for Actions)
- If both fail: DO NOT overwrite existing journal_metrics.json
  - If an old file exists -> exit 0 (no-op success)
  - If no old file exists -> exit 1 (so you notice on first setup)

Inputs:
- sources.json (journals with a single ISSN each)

Output:
- journal_metrics.json (journal-level SJR only; no quartile)
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List

import requests


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SOURCES_PATH = os.path.join(ROOT, "sources.json")
OUT_PATH = os.path.join(ROOT, "journal_metrics.json")

# Primary (may 403 in GitHub Actions)
SJR_EXPORT_URL = "https://www.scimagojr.com/journalrank.php?out=xls"

# Fallback mirror (public dataset hosted on GitHub; far less likely to be blocked)
# Source: Michael-E-Rose/SCImagoJournalRankIndicators (all.csv)
SJR_FALLBACK_URL = "https://raw.githubusercontent.com/Michael-E-Rose/SCImagoJournalRankIndicators/master/all.csv"

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
    Normalize ISSN into ####-#### uppercase.
    Accepts with/without hyphen.
    """
    s = (raw or "").strip().upper().replace(" ", "")
    s = s.replace("–", "-")
    if re.fullmatch(r"\d{4}-\d{3}[\dX]", s):
        return s
    if re.fullmatch(r"\d{7}[\dX]", s):
        return s[:4] + "-" + s[4:]
    return s


def looks_like_html(text: str) -> bool:
    t = (text or "").lstrip().lower()
    return t.startswith("<!doctype") or t.startswith("<html") or "<html" in t[:2000]


def fetch_text(url: str, headers: Dict[str, str]) -> str:
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def fetch_scimago_export() -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/plain,text/csv,application/octet-stream,*/*",
        "Referer": "https://www.scimagojr.com/journalrank.php",
    }
    return fetch_text(SJR_EXPORT_URL, headers=headers)


def fetch_fallback_csv() -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/csv,text/plain,*/*",
    }
    return fetch_text(SJR_FALLBACK_URL, headers=headers)


def sniff_delimiter(sample: str) -> str:
    # SCImago export is usually ';' separated; GitHub CSV is ',' separated
    if sample.count(";") > sample.count(","):
        return ";"
    return ","


def detect_columns(fieldnames: List[str]) -> Tuple[str, str, str, str]:
    """
    Detect columns for: year, issn, sjr, title.
    Handles both SCImago export and fallback CSV variants.
    """
    if not fieldnames:
        raise ValueError("No columns detected")

    norm = {c.strip().lower(): c for c in fieldnames}

    def pick(*names: str) -> Optional[str]:
        for n in names:
            if n in norm:
                return norm[n]
        return None

    year_col = pick("year")
    issn_col = pick("issn")
    sjr_col = pick("sjr")
    title_col = pick("title", "journal", "source title", "sourcetitle")

    if not (year_col and issn_col and sjr_col and title_col):
        raise ValueError(f"Missing required columns. Got: {fieldnames[:30]}")
    return year_col, issn_col, sjr_col, title_col


def parse_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None


def load_sources() -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], set]:
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        sources = json.load(f)

    # Map ISSN -> source record (short, name)
    issn_to_source: Dict[str, Dict[str, Any]] = {}
    wanted = set()

    for s in sources:
        issn = s.get("issn")
        if isinstance(issn, list):
            issn = issn[0] if issn else ""
        issn_norm = normalize_issn(str(issn))
        if not issn_norm:
            continue
        wanted.add(issn_norm)
        issn_to_source[issn_norm] = {
            "short": s.get("short") or s.get("name"),
            "name": s.get("name"),
            "issn": issn_norm,
        }

    return sources, issn_to_source, wanted


def build_from_text(csv_text: str, wanted_issns: set) -> Tuple[int, Dict[str, Dict[str, Any]]]:
    if looks_like_html(csv_text):
        raise ValueError("Received HTML instead of CSV (likely blocked)")

    delim = sniff_delimiter(csv_text[:4000])
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=delim)
    year_col, issn_col, sjr_col, title_col = detect_columns(reader.fieldnames or [])

    rows = list(reader)

    latest_year: Optional[int] = None
    for r in rows:
        try:
            y = int((r.get(year_col) or "").strip())
        except Exception:
            continue
        if latest_year is None or y > latest_year:
            latest_year = y

    if latest_year is None:
        raise ValueError("Could not detect latest year")

    by_issn: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        try:
            y = int((r.get(year_col) or "").strip())
        except Exception:
            continue
        if y != latest_year:
            continue

        issn_raw = (r.get(issn_col) or "").strip()
        if not issn_raw:
            continue

        # Some datasets use comma-separated ISSNs
        issns = [normalize_issn(x) for x in issn_raw.split(",")]

        sjr_val = parse_float(r.get(sjr_col))
        if sjr_val is None:
            continue

        title_source = (r.get(title_col) or "").strip()

        for issn in issns:
            if issn in wanted_issns:
                # keep max if duplicates
                cur = by_issn.get(issn)
                if cur is None or float(cur.get("sjr", 0.0)) < sjr_val:
                    by_issn[issn] = {"sjr": sjr_val, "title_source": title_source}

    return latest_year, by_issn


def main() -> int:
    if not os.path.exists(SOURCES_PATH):
        print(f"[ERROR] sources.json not found at {SOURCES_PATH}", file=sys.stderr)
        return 2

    sources, issn_to_source, wanted = load_sources()
    have_old = os.path.exists(OUT_PATH)

    # Try primary, then fallback
    last_err = None
    for attempt, label in [(fetch_scimago_export, "SCImago export"), (fetch_fallback_csv, "Fallback GitHub CSV")]:
        try:
            text = attempt()
            latest_year, by_issn = build_from_text(text, wanted)

            out = {
                "generated_at": iso_now(),
                "source_name": "SCImago Journal Rank (SJR)",
                "source_note": "SJR is treated as an annual journal-level metric; the dashboard uses the latest year available in the upstream dataset at update time.",
                "sjr_year": latest_year,
                "coverage": {
                    "matched": len(by_issn),
                    "total_sources": len(sources),
                },
                "by_issn": by_issn,
            }

            # Write only after success
            with open(OUT_PATH, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False, indent=2)

            print(f"[OK] {label}: wrote journal_metrics.json (year={latest_year}, matched={len(by_issn)}/{len(sources)})")
            return 0

        except Exception as e:
            last_err = f"{label} failed: {e}"
            print(f"[WARN] {last_err}", file=sys.stderr)

    # Both failed: do not overwrite; decide exit code
    if have_old:
        print("[WARN] Metrics update skipped (kept existing journal_metrics.json).", file=sys.stderr)
        return 0  # green run, no-op
    else:
        print("[ERROR] Metrics update failed and no previous journal_metrics.json exists.", file=sys.stderr)
        print(f"[ERROR] Last error: {last_err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
