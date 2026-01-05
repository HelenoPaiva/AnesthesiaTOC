#!/usr/bin/env python3
"""
Build unified TOC data.json from Crossref by ISSN, and optionally add PubMed links.

- Reads sources.json (each source: name, short, issn (str or [str]), optional tier)
- Queries Crossref for recent works per ISSN
- Normalizes into a unified list and writes data.json
- Enriches items with PMID + pubmed_url using NCBI E-utilities (DOI -> PMID)

Notes:
- PubMed enrichment is rate-limited and budgeted to avoid slow runs.
- Set env var PMID_LOOKUP_BUDGET to adjust how many DOI->PMID lookups per run (default 120).
- Provide a polite mailto in CROSSREF_UA and/or NCBI_EMAIL via GitHub Actions secrets if you want.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import requests


CROSSREF_API = "https://api.crossref.org/works"
NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

# Crossref (and NCBI) behave better if you include a mailto.
# In GitHub Actions you can set:
# - CROSSREF_UA = "AnesTOC-Dashboard/1.0 (mailto:you@example.com)"
USER_AGENT = os.getenv(
    "CROSSREF_UA",
    "AnesTOC-Dashboard/1.0 (mailto:example@example.com)",
)

# Optional: use the same email for NCBI; if unset, NCBI still works.
NCBI_EMAIL = os.getenv("NCBI_EMAIL", "")

# PubMed lookup limits (to keep the workflow fast + polite)
PMID_LOOKUP_BUDGET = int(os.getenv("PMID_LOOKUP_BUDGET", "120"))
PMID_SLEEP_SECONDS = float(os.getenv("PMID_SLEEP_SECONDS", "0.34"))  # ~3 req/sec


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_get(d: Dict[str, Any], path: List[str], default=None):
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def join_authors(item: Dict[str, Any]) -> str:
    authors = item.get("author") or []
    names = []
    for a in authors[:10]:
        family = a.get("family")
        given = a.get("given")
        if family and given:
            names.append(f"{family} {given[0]}.")
        elif family:
            names.append(family)
    if not names:
        return ""
    s = ", ".join(names)
    if len(authors) > 10:
        s += " et al."
    return s


def pick_date(item: Dict[str, Any]) -> Optional[str]:
    # Prefer published-online, then published-print, then issued, then created.
    date_paths = [
        ["published-online", "date-parts"],
        ["published-print", "date-parts"],
        ["issued", "date-parts"],
        ["created", "date-parts"],
    ]
    for path in date_paths:
        parts = safe_get(item, path)
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            y = parts[0][0]
            m = parts[0][1] if len(parts[0]) >= 2 else 1
            d = parts[0][2] if len(parts[0]) >= 3 else 1
            try:
                return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            except Exception:
                continue
    return None


def clean_title(item: Dict[str, Any]) -> str:
    t = item.get("title") or []
    if not t:
        return ""
    return re.sub(r"\s+", " ", t[0]).strip()


def crossref_query_by_issn(issn: str, rows: int = 30) -> List[Dict[str, Any]]:
    params = {
        "filter": f"issn:{issn}",
        "sort": "published",
        "order": "desc",
        "rows": str(rows),
    }
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(CROSSREF_API, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    payload = r.json()
    return payload.get("message", {}).get("items", []) or []


def to_item(journal_name: str, journal_short: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    doi = raw.get("DOI", "") or ""
    url = raw.get("URL") or (f"https://doi.org/{doi}" if doi else "")
    return {
        "journal": journal_name,
        "journal_short": journal_short,
        "title": clean_title(raw),
        "authors": join_authors(raw),
        "published": pick_date(raw),
        "doi": doi,
        "url": url,
        "type": raw.get("type", "") or "",
        "publisher": raw.get("publisher", "") or "",
        "source": "crossref",
    }


def doi_to_pmid(doi: str) -> Optional[str]:
    """
    Map DOI -> PMID using NCBI E-utilities (esearch).
    Returns PMID as string, or None if not found.
    """
    if not doi:
        return None

    params = {
        "db": "pubmed",
        "term": f"{doi}[doi]",
        "retmode": "json",
        "tool": "AnesTOC-Dashboard",
    }
    if NCBI_EMAIL:
        params["email"] = NCBI_EMAIL

    headers = {"User-Agent": USER_AGENT}
    r = requests.get(NCBI_ESEARCH, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()
    idlist = (((data or {}).get("esearchresult") or {}).get("idlist")) or []
    return idlist[0] if idlist else None


def main() -> int:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    sources_path = os.path.join(root, "sources.json")
    out_path = os.path.join(root, "data.json")

    with open(sources_path, "r", encoding="utf-8") as f:
        sources = json.load(f)

    unified: List[Dict[str, Any]] = []

    for s in sources:
        name = s["name"]
        short = s.get("short", name)
        tier = int(s.get("tier", 0))

        issns: Union[str, List[str]] = s["issn"]
        if isinstance(issns, str):
            issns = [issns]

        items: List[Dict[str, Any]] = []
        for issn in issns:
            try:
                items.extend(crossref_query_by_issn(issn, rows=30))
            except Exception as e:
                print(f"[WARN] Crossref failed for {name} ({issn}): {e}", file=sys.stderr)

        for raw in items:
            item = to_item(name, short, raw)
            if not item["title"]:
                continue
            item["tier"] = tier
            unified.append(item)

    # De-duplicate by DOI (best unique key)
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in unified:
        key = (it.get("doi") or "").lower().strip() or it.get("url") or (it["journal_short"] + "|" + it["title"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # Sort by date desc (missing dates go last)
    def sort_key(it: Dict[str, Any]):
        d = it.get("published")
        return (d is not None, d or "0000-00-00")

    deduped.sort(key=sort_key, reverse=True)

    # --- PubMed enrichment (budgeted) ---
    lookups_used = 0
    for it in deduped:
        if lookups_used >= PMID_LOOKUP_BUDGET:
            break

        doi = (it.get("doi") or "").strip()
        if not doi:
            continue

        try:
            pmid = doi_to_pmid(doi)
            if pmid:
                it["pmid"] = pmid
                it["pubmed_url"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            lookups_used += 1
            time.sleep(PMID_SLEEP_SECONDS)
        except Exception as e:
            # Don't fail the whole run because PubMed had a hiccup
            print(f"[WARN] PubMed lookup failed for DOI {doi}: {e}", file=sys.stderr)
            lookups_used += 1
            time.sleep(PMID_SLEEP_SECONDS)

    out = {"generated_at": iso_now(), "items": deduped}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(deduped)} items -> {out_path} (PubMed lookups used: {lookups_used}/{PMID_LOOKUP_BUDGET})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
