#!/usr/bin/env python3
"""
Build unified TOC data.json from Crossref by ISSN, with PubMed enrichment
and Ahead-of-Print (AOP) detection.

Key behaviors:
- Supports single or multiple ISSNs per journal
- Deduplicates by DOI
- Never displays future publication dates
- Marks articles as Ahead of Print when appropriate
- Adds PMID / PubMed links when available
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, date as _date
from typing import Any, Dict, List, Optional, Union

import requests


CROSSREF_API = "https://api.crossref.org/works"
NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

USER_AGENT = os.getenv(
    "CROSSREF_UA",
    "AnesTOC-Dashboard/1.0 (mailto:example@example.com)",
)

NCBI_EMAIL = os.getenv("NCBI_EMAIL", "")

PMID_LOOKUP_BUDGET = int(os.getenv("PMID_LOOKUP_BUDGET", "80"))
PMID_SLEEP_SECONDS = float(os.getenv("PMID_SLEEP_SECONDS", "0.34"))


# ---------------- utilities ----------------

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
    if len(authors) > 10:
        return ", ".join(names) + " et al."
    return ", ".join(names)


def clean_title(item: Dict[str, Any]) -> str:
    t = item.get("title") or []
    if not t:
        return ""
    return re.sub(r"\s+", " ", t[0]).strip()


def extract_ymd(item: Dict[str, Any], field: str) -> Optional[str]:
    parts = safe_get(item, [field, "date-parts"])
    if not (isinstance(parts, list) and parts and isinstance(parts[0], list)):
        return None
    try:
        y = parts[0][0]
        m = parts[0][1] if len(parts[0]) >= 2 else 1
        d = parts[0][2] if len(parts[0]) >= 3 else 1
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return None


# ---------------- DATE SELECTION (NO FUTURE DATES) ----------------

def pick_date(item: Dict[str, Any]) -> Optional[str]:
    """
    Choose a sensible publication date.

    Strategy:
    - Collect candidate dates from multiple Crossref fields
    - Discard dates in the future (UTC)
    - Pick the most recent non-future date
    - If all candidates are future, fall back to the earliest
    """

    fields = [
        "published-online",
        "published-print",
        "issued",
        "created",
        "indexed",
        "deposited",
    ]

    candidates: List[str] = []
    for f in fields:
        ymd = extract_ymd(item, f)
        if ymd:
            candidates.append(ymd)

    if not candidates:
        return None

    today = _date.today().isoformat()
    non_future = [c for c in candidates if c <= today]

    if non_future:
        return max(non_future)

    return min(candidates)


# ---------------- API helpers ----------------

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
    return r.json().get("message", {}).get("items", []) or []


def doi_to_pmid(doi: str) -> Optional[str]:
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
    ids = r.json().get("esearchresult", {}).get("idlist", [])
    return ids[0] if ids else None


def to_item(journal: str, short: str, tier: int, raw: Dict[str, Any]) -> Dict[str, Any]:
    doi = raw.get("DOI", "") or ""
    url = raw.get("URL") or (f"https://doi.org/{doi}" if doi else "")

    online = extract_ymd(raw, "published-online")
    pprint = extract_ymd(raw, "published-print")
    issued = extract_ymd(raw, "issued")

    # Ahead-of-print logic
    aop = False
    if online:
        later = [d for d in [pprint, issued] if d]
        if not later:
            aop = True
        elif min(later) > online:
            aop = True

    return {
        "journal": journal,
        "journal_short": short,
        "tier": tier,
        "title": clean_title(raw),
        "authors": join_authors(raw),
        "published": pick_date(raw),
        "doi": doi,
        "url": url,
        "aop": aop,
        "source": "crossref",
    }


# ---------------- main ----------------

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

        for issn in issns:
            try:
                items = crossref_query_by_issn(issn)
            except Exception as e:
                print(f"[WARN] Crossref failed for {name} ({issn}): {e}", file=sys.stderr)
                continue

            for raw in items:
                item = to_item(name, short, tier, raw)
                if item["title"]:
                    unified.append(item)

    # Deduplicate by DOI
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in unified:
        key = (it.get("doi") or "").lower() or it["url"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # Sort by publication date desc
    deduped.sort(key=lambda x: (x["published"] is not None, x["published"] or ""), reverse=True)

    # PubMed enrichment
    lookups = 0
    for it in deduped:
        if lookups >= PMID_LOOKUP_BUDGET:
            break
        doi = it.get("doi")
        if not doi:
            continue
        try:
            pmid = doi_to_pmid(doi)
            if pmid:
                it["pmid"] = pmid
                it["pubmed_url"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            lookups += 1
            time.sleep(PMID_SLEEP_SECONDS)
        except Exception as e:
            print(f"[WARN] PubMed lookup failed for {doi}: {e}", file=sys.stderr)
            lookups += 1
            time.sleep(PMID_SLEEP_SECONDS)

    out = {
        "generated_at": iso_now(),
        "items": deduped,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(deduped)} items -> data.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
