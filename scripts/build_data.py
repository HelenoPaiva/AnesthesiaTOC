#!/usr/bin/env python3
"""
Build unified TOC data.json from Crossref by ISSN, with PubMed enrichment
(PMID + Publication Types) and Ahead-of-Print (AOP) detection.

Pagination strategy (simple & robust for static hosting):
- Pull N recent items per journal (default 200) from Crossref
- Merge + dedupe (DOI/URL)
- Sort by date desc
- Cap output to a global max (default 3000)

Classification strategy (reproducible, reviewer-friendly):
1) Prefer PubMed Publication Types (when PMID available)
2) If PubMed missing/unhelpful, attempt title-based heuristics
3) Else: Unclassified

Output fields added:
- pmid, pubmed_url
- pubmed_publication_types (raw)
- category (one of the dashboard categories or "Unclassified")
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
from xml.etree import ElementTree as ET


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

# PubMed DOI->PMID lookups budget (avoid hammering NCBI)
PMID_LOOKUP_BUDGET = int(os.getenv("PMID_LOOKUP_BUDGET", "120"))
PMID_SLEEP_SECONDS = float(os.getenv("PMID_SLEEP_SECONDS", "0.34"))

# PubMed efetch batching (publication types)
EFETCH_BATCH_SIZE = int(os.getenv("EFETCH_BATCH_SIZE", "100"))
EFETCH_SLEEP_SECONDS = float(os.getenv("EFETCH_SLEEP_SECONDS", "0.34"))

# Dashboard categories (requested consolidated schema)
CAT_META = "Meta-analysis"
CAT_RCT = "Randomized Control Trials"
CAT_OBS = "Observational Studies"
CAT_GUIDE = "Guideline / Consensus"
CAT_REVIEW = "Review (Narrative / Systematic)"
CAT_EDITORIAL = "Editorial / Letter / Commentary"
CAT_UNK = "Unclassified"

DASHBOARD_CATEGORIES = [CAT_META, CAT_RCT, CAT_OBS, CAT_GUIDE, CAT_REVIEW, CAT_EDITORIAL]


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


def pick_date(item: Dict[str, Any]) -> Optional[str]:
    """
    Choose a sensible publication date and avoid future 'issue/cover' dates.
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

    # if everything is in the future, pick earliest (still consistent)
    return min(candidates)


def crossref_query_by_issn(issn: str, rows: int) -> List[Dict[str, Any]]:
    """
    Pull up to `rows` recent works. Crossref allows up to 1000 rows.
    """
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


def efetch_publication_types(pmids: List[str]) -> Dict[str, List[str]]:
    """
    Return mapping pmid -> list of PublicationType values using PubMed efetch (XML).
    """
    out: Dict[str, List[str]] = {}
    if not pmids:
        return out

    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": "AnesTOC-Dashboard",
    }
    if NCBI_EMAIL:
        params["email"] = NCBI_EMAIL
    headers = {"User-Agent": USER_AGENT}

    r = requests.get(NCBI_EFETCH, params=params, headers=headers, timeout=45)
    r.raise_for_status()

    root = ET.fromstring(r.text)

    for art in root.findall(".//PubmedArticle"):
        pmid_el = art.find(".//MedlineCitation/PMID")
        if pmid_el is None or not pmid_el.text:
            continue
        pmid = pmid_el.text.strip()
        pts = []
        for pt in art.findall(".//MedlineCitation/Article/PublicationTypeList/PublicationType"):
            if pt.text:
                pts.append(pt.text.strip())
        out[pmid] = pts

    # Some responses can include PubmedBookArticle; include defensively
    for art in root.findall(".//PubmedBookArticle"):
        pmid_el = art.find(".//BookDocument/PMID")
        if pmid_el is None or not pmid_el.text:
            continue
        pmid = pmid_el.text.strip()
        pts = []
        for pt in art.findall(".//BookDocument/PublicationTypeList/PublicationType"):
            if pt.text:
                pts.append(pt.text.strip())
        out[pmid] = pts

    return out


# --------- classification ---------

PUBMED_TYPE_TO_CATEGORY = {
    # Meta
    "Meta-Analysis": CAT_META,

    # RCT / trials (grouped)
    "Randomized Controlled Trial": CAT_RCT,
    "Clinical Trial": CAT_RCT,
    "Clinical Trial, Phase I": CAT_RCT,
    "Clinical Trial, Phase II": CAT_RCT,
    "Clinical Trial, Phase III": CAT_RCT,
    "Clinical Trial, Phase IV": CAT_RCT,
    "Controlled Clinical Trial": CAT_RCT,

    # Observational (grouped: cohort + case-control under observational)
    "Observational Study": CAT_OBS,
    "Cohort Studies": CAT_OBS,
    "Case-Control Studies": CAT_OBS,
    "Cross-Sectional Studies": CAT_OBS,

    # Guidelines / consensus
    "Practice Guideline": CAT_GUIDE,
    "Guideline": CAT_GUIDE,
    "Consensus Development Conference": CAT_GUIDE,
    "Consensus Development Conference, NIH": CAT_GUIDE,

    # Reviews (grouped: systematic + narrative)
    "Systematic Review": CAT_REVIEW,
    "Review": CAT_REVIEW,

    # Editorial / correspondence (grouped)
    "Editorial": CAT_EDITORIAL,
    "Comment": CAT_EDITORIAL,
    "Letter": CAT_EDITORIAL,
}


TITLE_RULES: List[tuple[str, str]] = [
    # Meta-analysis (highest priority)
    (CAT_META, r"\bmeta[- ]analysis\b|\bnetwork meta[- ]analysis\b|\bmetaanalysis\b"),

    # RCT / trials
    (CAT_RCT, r"\brandomi[sz]ed\b|\brandomi[sz]ation\b|\bcontrolled trial\b|\brct\b|\btrial\b"),

    # Guideline / Consensus
    (CAT_GUIDE, r"\bpractice guideline\b|\bguideline(s)?\b|\bconsensus\b|\bposition statement\b|\brecommendations\b"),

    # Reviews (systematic/narrative grouped)
    (CAT_REVIEW, r"\bsystematic review\b|\breview\b|\bscoping review\b|\brapid review\b"),

    # Editorial / Letter / Commentary
    (CAT_EDITORIAL, r"\beditorial\b|\bcommentary\b|\bcorrespondence\b|\bletter\b|\bcomment\b|\breply\b"),

    # Observational (cohort/case-control grouped here)
    (CAT_OBS, r"\bobservational\b|\bcohort\b|\bcase[- ]control\b|\bcross[- ]sectional\b|\bregistry\b"),
]


def category_from_pubmed_types(pub_types: List[str]) -> Optional[str]:
    if not pub_types:
        return None

    # Direct mapping with precedence by importance
    # Meta > RCT > Guide > Review > Editorial > Observational
    precedence = [CAT_META, CAT_RCT, CAT_GUIDE, CAT_REVIEW, CAT_EDITORIAL, CAT_OBS]

    mapped: List[str] = []
    for pt in pub_types:
        cat = PUBMED_TYPE_TO_CATEGORY.get(pt)
        if cat:
            mapped.append(cat)

    if not mapped:
        return None

    for cat in precedence:
        if cat in mapped:
            return cat
    return mapped[0]


def category_from_title(title: str) -> Optional[str]:
    if not title:
        return None
    t = title.lower()
    for cat, pattern in TITLE_RULES:
        if re.search(pattern, t, flags=re.IGNORECASE):
            return cat
    return None


def choose_category(pub_types: Optional[List[str]], title: str) -> str:
    pub_types = pub_types or []
    cat = category_from_pubmed_types(pub_types)
    if cat:
        return cat

    # PubMed often returns generic types like "Journal Article"; try title rules before Unclassified
    cat2 = category_from_title(title)
    if cat2:
        return cat2

    return CAT_UNK


# --------- item assembly ---------

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

    title = clean_title(raw)

    return {
        "journal": journal,
        "journal_short": short,
        "title": title,
        "authors": join_authors(raw),
        "published": pick_date(raw),
        "doi": doi,
        "url": url,
        "aop": aop,
        "source": "crossref",
        # PubMed enrichment fields (populated later if available)
        "pmid": None,
        "pubmed_url": None,
        "pubmed_publication_types": [],
        "category": None,
    }


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
        reverse=True
    )

    # PubMed enrichment step 1: DOI -> PMID (budgeted)
    pmids_to_fetch: List[str] = []
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
                pmids_to_fetch.append(pmid)
            lookups += 1
            time.sleep(PMID_SLEEP_SECONDS)
        except Exception as e:
            print(f"[WARN] PubMed lookup failed for {doi}: {e}", file=sys.stderr)
            lookups += 1
            time.sleep(PMID_SLEEP_SECONDS)

    # PubMed enrichment step 2: efetch Publication Types for collected PMIDs
    pmid_to_types: Dict[str, List[str]] = {}
    if pmids_to_fetch:
        # De-duplicate while keeping order
        seen_pm = set()
        pmids_unique = []
        for p in pmids_to_fetch:
            if p not in seen_pm:
                seen_pm.add(p)
                pmids_unique.append(p)

        batch_size = max(1, min(EFETCH_BATCH_SIZE, 200))
        for i in range(0, len(pmids_unique), batch_size):
            batch = pmids_unique[i:i + batch_size]
            try:
                got = efetch_publication_types(batch)
                pmid_to_types.update(got)
                time.sleep(EFETCH_SLEEP_SECONDS)
            except Exception as e:
                print(f"[WARN] PubMed efetch failed for PMIDs batch starting {batch[0]}: {e}", file=sys.stderr)
                time.sleep(EFETCH_SLEEP_SECONDS)

    # Assign pubmed_publication_types + final category (PubMed-first, then title)
    for it in deduped:
        pmid = it.get("pmid")
        pub_types = pmid_to_types.get(pmid, []) if pmid else []
        it["pubmed_publication_types"] = pub_types

        # Choose category with fallback title heuristics before Unclassified
        it["category"] = choose_category(pub_types, it.get("title") or "")

        # Normalize None fields (keep JSON clean)
        if not pmid:
            it.pop("pmid", None)
            it.pop("pubmed_url", None)

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
            "categories": DASHBOARD_CATEGORIES,
        },
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(
        f"Wrote {len(deduped)} items -> data.json "
        f"(rows/journal={CROSSREF_ROWS_PER_JOURNAL}, cap={GLOBAL_MAX_ITEMS}, "
        f"pmid_budget={PMID_LOOKUP_BUDGET})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
