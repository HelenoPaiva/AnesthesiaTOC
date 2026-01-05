#!/usr/bin/env python3
"""
Build unified TOC data.json from Crossref by ISSN.

- Reads sources.json
- For each journal ISSN, queries Crossref for recent works
- Normalizes into a single list and writes data.json
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import urllib.parse

import requests


CROSSREF_API = "https://api.crossref.org/works"
USER_AGENT = os.getenv(
    "CROSSREF_UA",
    "AnesTOC-Dashboard/1.0 (mailto:example@example.com)"
)

# Crossref is much happier if you provide a real mailto:
# In GitHub Actions, set secret CROSSREF_MAILTO and the workflow injects it.


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
    # Prefer published-online, then published-print, then created, then issued.
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
    # Crossref title can include odd spacing
    return re.sub(r"\s+", " ", t[0]).strip()


def to_item(journal_name: str, journal_short: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    doi = raw.get("DOI", "")
    url = raw.get("URL") or (f"https://doi.org/{doi}" if doi else "")
    return {
        "journal": journal_name,
        "journal_short": journal_short,
        "title": clean_title(raw),
        "authors": join_authors(raw),
        "published": pick_date(raw),
        "doi": doi,
        "url": url,
        "type": raw.get("type", ""),
        "publisher": raw.get("publisher", ""),
        "source": "crossref",
    }


def crossref_query_by_issn(issn: str, rows: int = 25) -> List[Dict[str, Any]]:
    # Filter by ISSN; sort by "published" desc tends to be okay, but Crossref varies by journal.
    params = {
        "filter": f"issn:{issn}",
        "sort": "published",
        "order": "desc",
        "rows": str(rows),
        # Some endpoints behave better with select, but we keep full to not miss fields.
    }

    headers = {"User-Agent": USER_AGENT}
    r = requests.get(CROSSREF_API, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    payload = r.json()
    return payload.get("message", {}).get("items", []) or []


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
        issn = s["issn"]

        try:
            items = crossref_query_by_issn(issn, rows=30)
        except Exception as e:
            print(f"[WARN] Failed for {name} ({issn}): {e}", file=sys.stderr)
            continue

        for raw in items:
            item = to_item(name, short, raw)
            # Skip empty titles (rare but happens)
            if not item["title"]:
                continue
            unified.append(item)

    # De-duplicate by DOI (best unique key)
    seen = set()
    deduped = []
    for it in unified:
        key = it.get("doi") or it.get("url") or (it["journal_short"] + "|" + it["title"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # Sort by date desc (missing dates go last)
    def sort_key(it: Dict[str, Any]):
        d = it.get("published")
        return (d is not None, d or "0000-00-00")

    deduped.sort(key=sort_key, reverse=True)

    out = {"generated_at": iso_now(), "items": deduped}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(deduped)} items -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
