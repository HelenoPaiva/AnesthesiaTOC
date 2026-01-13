#!/usr/bin/env python3
"""Build unified TOC data.json.

Crossref provides recent works; PubMed provides publication types.
The dashboard category is derived from PubMed PublicationType metadata when available.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, date as _date
from typing import Any, Dict, List, Optional, Union

import requests


CROSSREF_API = "https://api.crossref.org/works"
NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


USER_AGENT = os.getenv(
    "CROSSREF_UA",
    "AnesTOC-Dashboard/1.0 (mailto:example@example.com)",
)

NCBI_EMAIL = os.getenv("NCBI_EMAIL", "")

# How many recent works to fetch per journal from Crossref
CROSSREF_ROWS_PER_JOURNAL = int(os.getenv("CROSSREF_ROWS_PER_JOURNAL", "200"))
# Hard cap on total items written to data.json (after dedupe and sorting)
GLOBAL_MAX_ITEMS = int(os.getenv("GLOBAL_MAX_ITEMS", "3000"))

# PubMed DOI->PMID lookup budget (avoid hammering NCBI)
PMID_LOOKUP_BUDGET = int(os.getenv("PMID_LOOKUP_BUDGET", "200"))
PMID_SLEEP_SECONDS = float(os.getenv("PMID_SLEEP_SECONDS", "0.34"))

# PubMed efetch batch size (NCBI supports larger, but keep modest)
EFETCH_BATCH_SIZE = int(os.getenv("EFETCH_BATCH_SIZE", "100"))
EFETCH_SLEEP_SECONDS = float(os.getenv("EFETCH_SLEEP_SECONDS", "0.34"))


# Final dashboard categories (canonical)
DASHBOARD_CATEGORIES: List[str] = [
    "Systematic Review",
    "Meta-analysis",
    "Randomized Controlled Trial",
    "Observational Study",
    "Cohort Study",
    "Case-Control Study",
    "Guideline / Consensus",
    "Narrative Review",
    "Editorial / Commentary",
]


# PubMed Publication Type -> dashboard category mapping (agreed table + common extras)
PUBMED_PT_MAP: Dict[str, str] = {
    "Systematic Review": "Systematic Review",
    "Meta-Analysis": "Meta-analysis",
    "Randomized Controlled Trial": "Randomized Controlled Trial",
    "Clinical Trial": "Randomized Controlled Trial",
    "Controlled Clinical Trial": "Randomized Controlled Trial",
    "Pragmatic Clinical Trial": "Randomized Controlled Trial",
    "Clinical Trial, Phase I": "Randomized Controlled Trial",
    "Clinical Trial, Phase II": "Randomized Controlled Trial",
    "Clinical Trial, Phase III": "Randomized Controlled Trial",
    "Clinical Trial, Phase IV": "Randomized Controlled Trial",
    "Observational Study": "Observational Study",
    "Cohort Studies": "Cohort Study",
    "Case-Control Studies": "Case-Control Study",
    "Practice Guideline": "Guideline / Consensus",
    "Guideline": "Guideline / Consensus",
    "Consensus Development Conference": "Guideline / Consensus",
    "Consensus Development Conference, NIH": "Guideline / Consensus",
    "Review": "Narrative Review",
    "Editorial": "Editorial / Commentary",
    "Comment": "Editorial / Commentary",
    # Common extras
    "Letter": "Editorial / Commentary",
    "News": "Editorial / Commentary",
}


# Tie-break priority (highest wins)
CATEGORY_PRIORITY: List[str] = [
    "Meta-analysis",
    "Systematic Review",
    "Randomized Controlled Trial",
    "Guideline / Consensus",
    "Cohort Study",
    "Case-Control Study",
    "Observational Study",
    "Narrative Review",
    "Editorial / Commentary",
]


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
    names: List[str] = []
    for a in authors[:10]:
        family = a.get("family")
        given = a.get("given")
        if family and given:
            names.append(f"{family} {given[0]}. ".strip())
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


def pick_date(item: Dict[str, Any]) -> Optional[str]:
    """Choose a sensible publication date and avoid future 'issue/cover' dates."""
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

    # if everything is in the future, pick earliest (still consistent)
    return min(candidates)


def crossref_query_by_issn(issn: str, rows: int) -> List[Dict[str, Any]]:
    """Pull up to `rows` recent works. Crossref allows up to 1000 rows."""
    rows = max(1, min(int(rows), 1000))
    params = {
        "filter": f"issn:{issn}",
        "sort": "published",
        "order": "desc",
        "rows": str(rows),
    }
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(CROSSREF_API, params=params, headers=headers, timeout=45)
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
    r = requests.get(NCBI_ESEARCH, params=params, headers=headers, timeout=45)
    r.raise_for_status()
    ids = r.json().get("esearchresult", {}).get("idlist", [])
    return ids[0] if ids else None


def efetch_pubmed_publication_types(pmids: List[str]) -> Dict[str, List[str]]:
    """Return {pmid: [PublicationType, ...]} for provided pmids."""
    out: Dict[str, List[str]] = {}
    if not pmids:
        return out

    headers = {"User-Agent": USER_AGENT}
    for i in range(0, len(pmids), EFETCH_BATCH_SIZE):
        chunk = pmids[i : i + EFETCH_BATCH_SIZE]
        params = {
            "db": "pubmed",
            "id": ",".join(chunk),
            "retmode": "xml",
            "tool": "AnesTOC-Dashboard",
        }
        if NCBI_EMAIL:
            params["email"] = NCBI_EMAIL

        r = requests.get(NCBI_EFETCH, params=params, headers=headers, timeout=45)
        r.raise_for_status()

        root = ET.fromstring(r.text)
        for article in root.findall(".//PubmedArticle"):
            pmid_el = article.find(".//MedlineCitation/PMID")
            if pmid_el is None or not pmid_el.text:
                continue
            pmid = pmid_el.text.strip()
            pts: List[str] = []
            for pt in article.findall(".//PublicationTypeList/PublicationType"):
                if pt.text:
                    pts.append(pt.text.strip())
            out[pmid] = pts

        time.sleep(EFETCH_SLEEP_SECONDS)

    return out


def pubmed_types_to_category(pubmed_types: List[str]) -> Optional[str]:
    """Map PubMed Publication Types -> one dashboard category (deterministic)."""
    if not pubmed_types:
        return None

    mapped = set()
    for pt in pubmed_types:
        cat = PUBMED_PT_MAP.get(pt)
        if cat:
            mapped.add(cat)

    if not mapped:
        return None

    for cat in CATEGORY_PRIORITY:
        if cat in mapped:
            return cat
    return None


def to_item(journal: str, short: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    doi = raw.get("DOI", "") or ""
    url = raw.get("URL") or (f"https://doi.org/{doi}" if doi else "")

    online = extract_ymd(raw, "published-online")
    pprint = extract_ymd(raw, "published-print")
    issued = extract_ymd(raw, "issued")

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
        "title": clean_title(raw),
        "authors": join_authors(raw),
        "published": pick_date(raw),
        "doi": doi,
        "url": url,
        "aop": aop,
        "source": "crossref",
        # PubMed enrichment fields (filled later when possible)
        "pmid": None,
        "pubmed_url": None,
        "pubmed_publication_types": [],
        "category": None,
    }


def main() -> int:
    # Support both repo layouts:
    #   - scripts/build_data.py  -> sources.json in parent
    #   - build_data.py at root  -> sources.json alongside
    here = os.path.abspath(os.path.dirname(__file__))
    root = here if os.path.exists(os.path.join(here, "sources.json")) else os.path.abspath(os.path.join(here, ".."))
    sources_path = os.path.join(root, "sources.json")
    out_path = os.path.join(root, "data.json")

    with open(sources_path, "r", encoding="utf-8") as f:
        sources = json.load(f)

    unified: List[Dict[str, Any]] = []
    for s in sources:
        name = s["name"]
        short = s.get("short", name)

        issn: Union[str, List[str]] = s.get("issn", "")
        if isinstance(issn, list):
            issn = issn[0] if issn else ""
        if not issn:
            continue

        try:
            items = crossref_query_by_issn(str(issn), rows=CROSSREF_ROWS_PER_JOURNAL)
        except Exception as e:
            print(f"[WARN] Crossref failed for {name} ({issn}): {e}", file=sys.stderr)
            continue

        for raw in items:
            item = to_item(name, short, raw)
            if item["title"]:
                unified.append(item)

    # Deduplicate by DOI (fallback: URL)
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in unified:
        key = (it.get("doi") or "").lower() or it.get("url") or ""
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # Sort by publication date desc
    deduped.sort(
        key=lambda x: (x.get("published") is not None, x.get("published") or ""),
        reverse=True,
    )

    # PubMed enrichment (budgeted DOI->PMID)
    lookups = 0
    pmids_to_fetch: List[str] = []
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
                pmids_to_fetch.append(pmid)
            lookups += 1
            time.sleep(PMID_SLEEP_SECONDS)
        except Exception as e:
            print(f"[WARN] PubMed DOI->PMID lookup failed for {doi}: {e}", file=sys.stderr)
            lookups += 1
            time.sleep(PMID_SLEEP_SECONDS)

    # Publication types via efetch (only for resolved PMIDs)
    pubtypes_by_pmid = efetch_pubmed_publication_types(pmids_to_fetch)
    for it in deduped:
        pmid = it.get("pmid")
        if not pmid:
            continue
        pts = pubtypes_by_pmid.get(pmid, [])
        it["pubmed_publication_types"] = pts
        it["category"] = pubmed_types_to_category(pts)

    # Hard cap output size
    if len(deduped) > GLOBAL_MAX_ITEMS:
        deduped = deduped[:GLOBAL_MAX_ITEMS]

    out = {
        "generated_at": iso_now(),
        "items": deduped,
        "meta": {
            "rows_per_journal": CROSSREF_ROWS_PER_JOURNAL,
            "global_max_items": GLOBAL_MAX_ITEMS,
            "pmid_lookup_budget": PMID_LOOKUP_BUDGET,
            "efetch_batch_size": EFETCH_BATCH_SIZE,
            "category_priority": CATEGORY_PRIORITY,
            "categories": DASHBOARD_CATEGORIES,
        },
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(
        f"Wrote {len(deduped)} items -> data.json "
        f"(rows/journal={CROSSREF_ROWS_PER_JOURNAL}, cap={GLOBAL_MAX_ITEMS}, pmid_budget={PMID_LOOKUP_BUDGET})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
