#!/usr/bin/env python3
"""Build the Anesthesia TOC dashboard's data.json (version 2.0.0).

Drop-in replacement for scripts/build_data.py. Requires Python 3.9+ and requests.
Run as before: python scripts/build_data.py

Compatibility
-------------
* Reads the existing sources.json; keeps the existing item fields and category keys.
* Keeps the existing environment variables and their retrieval/budget defaults.
* Writes ONLY data.json. The PubMed cache lives in meta.pubmed_cache inside that
  file, so the existing GitHub Actions `git add data.json` persists it automatically.
* "Randomized Control Trials" remains the legacy category KEY, to avoid breaking
  existing JavaScript filters. category_label provides the corrected display text.

Corrections
-----------
* Display dates use publication fields, never deposit/index/create timestamps.
  Partial dates remain partial (YYYY or YYYY-MM); missing days are NOT invented.
* AOP is conservative: current, recently checked PubMed status or an online date
  plus a demonstrably future print date. Missing print metadata alone is not AOP.
* Generic clinical trials are not assumed randomized. Document-type safeguards
  precede trial keywords; protocols and uncertain records remain Unclassified.
* Positive AND negative PubMed lookups persist. Publication types are refreshed
  periodically; API failures do not erase previously obtained enrichment.
* Failed sources retain cached records when available; large unexplained drops,
  entirely failed/empty harvests, and malformed inputs cannot replace the last feed.
* Output is validated and replaced atomically. API keys never enter JSON or logs.

New optional environment variables (defaults shown)
---------------------------------------------------
NCBI_API_KEY / PUBMED_API_KEY             optional; read from environment only
PUBMED_NOT_FOUND_RETRY_DAYS=7             retry unmatched/ambiguous DOIs
PUBMED_ERROR_RETRY_HOURS=6               retry transport/response failures
PUBMED_REFRESH_DAYS=30                   refresh PubMed publication types/status
AOP_REFRESH_DAYS=7                       shorter refresh/expiry for AOP status
EFETCH_RECORD_BUDGET=500                 maximum detail records fetched per build
PUBMED_CACHE_MAX_ENTRIES=20000           bound the persisted enrichment cache
PUBMED_CACHE_RETENTION_DAYS=90           retain cache for temporarily absent items
HTTP_MAX_ATTEMPTS=3                      retries for temporary errors
HTTP_TIMEOUT_SECONDS=45                  read timeout (connect timeout is 10 s)
Repeated connection failures open a per-build circuit breaker to limit outage time.
CROSSREF_SLEEP_SECONDS=0.2               minimum spacing of Crossref requests
MIN_SOURCE_SUCCESS_RATIO=0.5             abort below this harvest success fraction
MIN_RETAINED_FRACTION=0.5                protect against major unexplained drops
ALLOW_LARGE_DROP=0                       explicit override for intentional resets
TOC_ROOT                                optional directory containing sources.json

Retrieval is still a bounded recent sample, NOT an exhaustive journal archive.
The 200-record per-source and 3000-record global defaults are unchanged. The global
cap remains chronological; source-level counts in meta.sources expose truncation.
Automated categories and AOP labels are not independently validated evidence.

Primary documentation used for the implementation:
https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/
https://www.ncbi.nlm.nih.gov/books/NBK25497/
https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/180101/el-PublicationStatus.html
https://www.ncbi.nlm.nih.gov/mesh/68016449
"""

from __future__ import annotations

import calendar
import copy
import html
import json
import math
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

import requests

VERSION = "2.0.0"
CROSSREF_API = "https://api.crossref.org/works"
NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

CAT_META = "Meta-analysis"
CAT_RCT = "Randomized Control Trials"  # Legacy frontend key. Do not rename alone.
CAT_OBS = "Observational Studies"
CAT_GUIDE = "Guideline / Consensus"
CAT_REVIEW = "Review (Narrative / Systematic)"
CAT_EDITORIAL = "Editorial / Letter / Commentary"
CAT_UNK = "Unclassified"
DASHBOARD_CATEGORIES = [CAT_META, CAT_RCT, CAT_OBS, CAT_GUIDE, CAT_REVIEW, CAT_EDITORIAL]
CATEGORY_LABELS = {k: k for k in DASHBOARD_CATEGORIES + [CAT_UNK]}
CATEGORY_LABELS[CAT_RCT] = "Randomized controlled trials"
PUBLICATION_FIELDS = ("published-online", "published-print", "published", "issued")


class BuildError(RuntimeError):
    """An unsafe/incomplete build; leave the previous data.json untouched."""


class APIError(RuntimeError):
    """A sanitized API error; never includes a secret-bearing request URL."""


class AmbiguousDOI(APIError):
    """More than one PubMed record matched; do not select the first blindly."""


def log(message: str, warning: bool = False) -> None:
    print(("[WARN] " if warning else "[INFO] ") + message, file=sys.stderr)
    if warning and os.getenv("GITHUB_ACTIONS") == "true":
        escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::warning::" + escaped)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_now() -> str:
    return utc_now().isoformat()


def parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except ValueError:
        return None


def age_days(value: Any, now: datetime) -> float:
    dt = parse_timestamp(value)
    if dt is None:
        return float("inf")
    # Future timestamps are not trusted as a reason to avoid refresh indefinitely.
    if dt > now:
        return float("inf")
    return (now - dt).total_seconds() / 86400.0


def env_number(name: str, default: Any, low: float, high: float, integer: bool = True) -> Any:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw) if integer else float(raw)
    except ValueError as exc:
        raise BuildError(f"{name} must be a number.") from exc
    if not math.isfinite(value) or not low <= value <= high:
        raise BuildError(f"{name} must be between {low} and {high}.")
    return value


@dataclass(frozen=True)
class Settings:
    rows_per_journal: int = 200
    global_max_items: int = 3000
    pmid_lookup_budget: int = 120
    pmid_sleep: float = 0.34
    efetch_batch_size: int = 100
    efetch_sleep: float = 0.34
    efetch_record_budget: int = 500
    not_found_retry_days: float = 7
    error_retry_hours: float = 6
    refresh_days: float = 30
    aop_refresh_days: float = 7
    cache_max_entries: int = 20000
    cache_retention_days: float = 90
    http_attempts: int = 3
    timeout: float = 45
    crossref_sleep: float = 0.2
    min_source_success_ratio: float = 0.5
    min_retained_fraction: float = 0.5
    allow_large_drop: bool = False
    user_agent: str = "AnesTOC-Dashboard/2.0"
    email: str = ""
    crossref_email: str = ""
    api_key: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            rows_per_journal=env_number("CROSSREF_ROWS_PER_JOURNAL", 200, 1, 1000),
            global_max_items=env_number("GLOBAL_MAX_ITEMS", 3000, 1, 1000000),
            pmid_lookup_budget=env_number("PMID_LOOKUP_BUDGET", 120, 0, 100000),
            pmid_sleep=env_number("PMID_SLEEP_SECONDS", 0.34, 0, 120, False),
            efetch_batch_size=env_number("EFETCH_BATCH_SIZE", 100, 1, 200),
            efetch_sleep=env_number("EFETCH_SLEEP_SECONDS", 0.34, 0, 120, False),
            efetch_record_budget=env_number("EFETCH_RECORD_BUDGET", 500, 0, 100000),
            not_found_retry_days=env_number("PUBMED_NOT_FOUND_RETRY_DAYS", 7, 0, 3650, False),
            error_retry_hours=env_number("PUBMED_ERROR_RETRY_HOURS", 6, 0, 87600, False),
            refresh_days=env_number("PUBMED_REFRESH_DAYS", 30, 1, 3650, False),
            aop_refresh_days=env_number("AOP_REFRESH_DAYS", 7, 1, 365, False),
            cache_max_entries=env_number("PUBMED_CACHE_MAX_ENTRIES", 20000, 1, 1000000),
            cache_retention_days=env_number("PUBMED_CACHE_RETENTION_DAYS", 90, 1, 3650, False),
            http_attempts=env_number("HTTP_MAX_ATTEMPTS", 3, 1, 8),
            timeout=env_number("HTTP_TIMEOUT_SECONDS", 45, 1, 300, False),
            crossref_sleep=env_number("CROSSREF_SLEEP_SECONDS", 0.2, 0, 120, False),
            min_source_success_ratio=env_number("MIN_SOURCE_SUCCESS_RATIO", 0.5, 0, 1, False),
            min_retained_fraction=env_number("MIN_RETAINED_FRACTION", 0.5, 0, 1, False),
            allow_large_drop=os.getenv("ALLOW_LARGE_DROP", "0").lower() in {"1", "true", "yes"},
            user_agent=os.getenv("CROSSREF_UA", "AnesTOC-Dashboard/2.0").strip(),
            email=os.getenv("NCBI_EMAIL", "").strip(),
            crossref_email=os.getenv("CROSSREF_MAILTO", "").strip(),
            api_key=(os.getenv("NCBI_API_KEY") or os.getenv("PUBMED_API_KEY") or "").strip(),
        )


# ---------------------------- Dates and identifiers ----------------------------

@dataclass(frozen=True)
class PartialDate:
    text: str
    first: date
    last: date
    precision: str


def date_parts(parts: Any) -> Optional[PartialDate]:
    """Validate dates and preserve their real precision instead of inventing Jan 1."""
    if not isinstance(parts, (list, tuple)) or not 1 <= len(parts) <= 3:
        return None
    if any(isinstance(v, bool) or not re.fullmatch(r"\d+", str(v)) for v in parts):
        return None
    try:
        values = [int(v) for v in parts]
        y = values[0]
        m = values[1] if len(values) >= 2 else 1
        d = values[2] if len(values) == 3 else 1
        first = date(y, m, d)
        if len(values) == 3:
            return PartialDate(first.isoformat(), first, first, "day")
        if len(values) == 2:
            return PartialDate(f"{y:04d}-{m:02d}", first, date(y, m, calendar.monthrange(y, m)[1]), "month")
        return PartialDate(f"{y:04d}", first, date(y, 12, 31), "year")
    except (TypeError, ValueError, OverflowError):
        return None


def parse_partial_date(value: Any) -> Optional[PartialDate]:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", value):
        return None
    return date_parts(value.split("-"))


def safe_get(d: Dict[str, Any], path: List[str], default: Any = None) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def extract_ymd(item: Dict[str, Any], field: str) -> Optional[str]:
    """Legacy helper name; returns YYYY, YYYY-MM, or YYYY-MM-DD at source precision."""
    parts = safe_get(item, [field, "date-parts"])
    parsed = date_parts(parts[0]) if isinstance(parts, list) and parts else None
    return parsed.text if parsed else None


def select_publication_date(raw: Dict[str, Any], today: Optional[date] = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Earliest explicit publication event; generic publication fields are fallbacks.

    For overlapping partial-date ranges choose the most precise explicit event.
    Future-only dates are retained in publication_dates but not used as the displayed
    publication date. created/indexed/deposited are never publication fallbacks.
    """
    today = today or utc_now().date()
    parsed = {f: parse_partial_date(extract_ymd(raw, f)) for f in PUBLICATION_FIELDS}
    eligible = [(f, parsed[f]) for f in PUBLICATION_FIELDS[:2]
                if parsed[f] and parsed[f].first <= today]
    if eligible:
        earliest = min(eligible, key=lambda x: x[1].first)
        overlap = [x for x in eligible if x[1].first <= earliest[1].last]
        rank = {"day": 0, "month": 1, "year": 2}
        field, pd = min(overlap, key=lambda x: (rank[x[1].precision], x[1].first, x[0] != "published-online"))
        return pd.text, field, pd.precision
    for field in PUBLICATION_FIELDS[2:]:
        pd = parsed[field]
        if pd and pd.first <= today:
            return pd.text, field, pd.precision
    return None, None, None


def pick_date(raw: Dict[str, Any], today: Optional[date] = None) -> Optional[str]:
    return select_publication_date(raw, today)[0]


def clean_doi(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = html.unescape(value).strip()
    if re.match(r"^https?://(?:dx\.)?doi\.org/", value, re.I):
        value = unquote(re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I))
    value = re.sub(r"^doi:\s*", "", value, flags=re.I).strip()
    if re.fullmatch(r'10\.\d{4,9}/[^\s"<>]+', value, re.I):
        return value
    return ""


def normalize_doi(value: Any) -> str:
    return clean_doi(value).lower()


def safe_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if any(ord(ch) < 32 for ch in value):
        return ""
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else ""
    except ValueError:
        return ""


def item_key(item: Dict[str, Any]) -> str:
    doi = normalize_doi(item.get("doi"))
    return "doi:" + doi if doi else "url:" + safe_url(item.get("url")) if safe_url(item.get("url")) else ""


def normalize_issn(value: Any) -> str:
    text = re.sub(r"[\s-]", "", str(value or "")).upper()
    if not re.fullmatch(r"\d{7}[\dX]", text):
        raise BuildError(f"Invalid ISSN format: {value!r}.")
    total = sum(int(text[i]) * (8 - i) for i in range(7)) + (10 if text[7] == "X" else int(text[7]))
    if total % 11:
        raise BuildError(f"Invalid ISSN check digit: {value!r}.")
    return text[:4] + "-" + text[4:]


def clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"</?[A-Za-z][^>]*>", "", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def clean_title(raw: Dict[str, Any]) -> str:
    titles = raw.get("title") or []
    return clean_text(titles[0] if isinstance(titles, list) and titles else titles if isinstance(titles, str) else "")


def join_authors(raw: Dict[str, Any]) -> str:
    authors = raw.get("author") or []
    if not isinstance(authors, list):
        return ""
    names = []
    for author in authors[:10]:
        if not isinstance(author, dict):
            continue
        family, given = clean_text(author.get("family")), clean_text(author.get("given"))
        name = (f"{family} {given[0]}." if given else family) if family else clean_text(author.get("name"))
        if name:
            names.append(name)
    return ", ".join(names) + (" et al." if names and len(authors) > 10 else "")


# ---------------------------- Conservative classification ----------------------

PUBMED_TYPE_TO_CATEGORY = {
    "Meta-Analysis": CAT_META,
    "Randomized Controlled Trial": CAT_RCT,
    "Observational Study": CAT_OBS,
    "Practice Guideline": CAT_GUIDE,
    "Guideline": CAT_GUIDE,
    "Consensus Development Conference": CAT_GUIDE,
    "Consensus Development Conference, NIH": CAT_GUIDE,
    "Systematic Review": CAT_REVIEW,
    "Review": CAT_REVIEW,
    "Editorial": CAT_EDITORIAL,
    "Comment": CAT_EDITORIAL,
    "Letter": CAT_EDITORIAL,
    "Published Erratum": CAT_EDITORIAL,
    "Retraction of Publication": CAT_EDITORIAL,
    "Expression of Concern": CAT_EDITORIAL,
}
# Deliberately absent: generic/phase-specific Clinical Trial, Controlled Clinical
# Trial, and MeSH topic descriptors such as Cohort Studies. None proves an RCT.
EDITORIAL_RE = re.compile(
    r"^(?:(?:a|an)\s+)?(?:letter\b|editorial\b|commentary\b|correspondence\b|"
    r"reply\b|response to\b|comment on\b|author(?:s['’]?)? reply\b|correction\b|erratum\b|retraction\b)"
    r"|\b(?:a commentary|letter to the editor|in reply)\b", re.I)
PROTOCOL_RE = re.compile(
    r"\b(?:(?:study|trial|review)\s+protocol|statistical analysis plan|"
    r"rationale and design|design and rationale)\b"
    r"|^(?:(?:a|an)\s+)?protocol\s*(?:for\b|of\b|:)"
    r"|:\s*(?:(?:a|an)\s+)?protocol\b", re.I)
META_RE = re.compile(r"\bmeta[- ]?analys(?:is|es)\b", re.I)
REVIEW_RE = re.compile(
    r"\b(?:systematic|narrative|scoping|rapid|umbrella|integrative|literature)\s+review\b"
    r"|^(?:a |an )?review\b|:\s*(?:a |an )?review\b", re.I)
GUIDE_RE = re.compile(r"\b(?:guidelines?|consensus|position statement|practice advisory)\b", re.I)
NONRANDOM_RE = re.compile(r"\bnon[- ]?randomi[sz]ed\b|\bnot\s+randomi[sz]ed\b", re.I)
SECONDARY_RE = re.compile(r"\b(?:secondary|post[- ]hoc|pooled)\s+analys(?:is|es)\b", re.I)
OBS_RE = re.compile(r"\b(?:observational|cohort|case[- ]control|cross[- ]sectional)\b"
                    r"|\bretrospective\s+(?:study|analysis|review)\b|\bregistry[- ]based\s+study\b", re.I)
RCT_RE = re.compile(r"\brandomi[sz]ed\b.{0,140}\b(?:trial|study)\b"
                    r"|\b(?:trial|study)\b.{0,100}\brandomi[sz]ed\b|\brct\b", re.I)


def normalized_title(title: str) -> str:
    return re.sub(r"[\u2010-\u2015\u2212]", "-", clean_text(title))


def category_from_pubmed_types(pub_types: List[str]) -> Optional[str]:
    pts = {p.casefold() for p in pub_types if isinstance(p, str)}
    if "clinical trial protocol" in pts:
        return CAT_UNK
    mapped = {cat for pt, cat in PUBMED_TYPE_TO_CATEGORY.items() if pt.casefold() in pts}
    for category in [CAT_EDITORIAL, CAT_GUIDE, CAT_META, CAT_REVIEW, CAT_RCT, CAT_OBS]:
        if category in mapped:
            return category
    return None


def title_classification(title: str) -> Tuple[Optional[str], str]:
    title = normalized_title(title)
    if EDITORIAL_RE.search(title):
        return CAT_EDITORIAL, "correspondence_or_editorial_title"
    if PROTOCOL_RE.search(title):
        return CAT_UNK, "protocol_or_analysis_plan_not_trial_results"
    if META_RE.search(title):
        return CAT_META, "meta_analysis_title"
    if REVIEW_RE.search(title):
        return CAT_REVIEW, "review_title"
    if GUIDE_RE.search(title):
        return CAT_GUIDE, "guideline_or_consensus_title"
    if OBS_RE.search(title):
        return CAT_OBS, "observational_design_title"
    if SECONDARY_RE.search(title) and re.search(r"\btrials?\b", title, re.I):
        return CAT_UNK, "secondary_analysis_not_assumed_new_randomized_trial"
    if NONRANDOM_RE.search(title):
        return CAT_UNK, "explicitly_nonrandomized"
    if RCT_RE.search(title):
        return CAT_RCT, "explicit_randomized_trial_title"
    return None, "no_supported_category"


def category_from_title(title: str) -> Optional[str]:
    return title_classification(title)[0]


def classify(pub_types: Optional[List[str]], title: str) -> Tuple[str, str, str]:
    """PubMed first, with safeguards against protocol/document-type mislabeling."""
    pts = pub_types or []
    pc = category_from_pubmed_types(pts)
    tc, reason = title_classification(title)
    if pc in {CAT_EDITORIAL, CAT_UNK}:
        return pc, "pubmed", "publication_type"
    if tc == CAT_EDITORIAL or (tc == CAT_UNK and reason == "protocol_or_analysis_plan_not_trial_results"):
        return tc, "title_heuristic", reason
    # Do not apply an indexed RCT label blindly to an explicit secondary document
    # or an explicitly nonrandomized report; expose the conservative decision.
    if pc == CAT_RCT and tc in {CAT_META, CAT_REVIEW, CAT_GUIDE, CAT_OBS, CAT_UNK}:
        return tc, "title_heuristic", reason
    if pc:
        return pc, "pubmed", "publication_type"
    if tc:
        return tc, "title_heuristic", reason
    return CAT_UNK, "unclassified", reason


def choose_category(pub_types: Optional[List[str]], title: str) -> str:
    return classify(pub_types, title)[0]


# ---------------------------- HTTP and upstream parsing ------------------------

class APIClient:
    """Sequential requests with a shared NCBI throttle, bounded retry and redaction."""
    def __init__(self, settings: Settings, session: Optional[requests.Session] = None):
        self.settings = settings
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": settings.user_agent})
        self.next_request = {"crossref": 0.0, "pubmed": 0.0}

    def _wait(self, service: str) -> None:
        now = time.monotonic()
        delay = self.next_request[service] - now
        if delay > 0:
            time.sleep(delay)
        cfg = self.settings
        interval = cfg.crossref_sleep if service == "crossref" else max(
            0.11 if cfg.api_key else 0.34, cfg.pmid_sleep, cfg.efetch_sleep)
        self.next_request[service] = time.monotonic() + interval

    def request(self, url: str, params: Dict[str, Any], parser: Callable[[requests.Response], Any], service: str) -> Any:
        last_error = "upstream request failed"
        for attempt in range(self.settings.http_attempts):
            self._wait(service)
            retry_after = None
            try:
                response = self.session.get(url, params=params, timeout=(10, self.settings.timeout))
                code = response.status_code
                if code in {408, 429, 500, 502, 503, 504}:
                    last_error = f"{service} HTTP {code}"
                    header = response.headers.get("Retry-After", "")
                    try:
                        retry_after = float(header)
                    except (ValueError, TypeError):
                        try:
                            dt = parsedate_to_datetime(header)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            retry_after = max(0.0, (dt - utc_now()).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                elif not 200 <= code < 300:
                    raise APIError(f"{service} HTTP {code}")
                else:
                    try:
                        return parser(response)
                    except (ValueError, TypeError, KeyError, ET.ParseError):
                        last_error = f"{service} returned malformed or incomplete data"
            except requests.RequestException:
                last_error = f"{service} connection or timeout error"
            if attempt + 1 < self.settings.http_attempts:
                delay = max(2.0 ** attempt, retry_after or 0.0)
                if not math.isfinite(delay) or delay > 120:
                    # Do not ignore a long Retry-After and retry prematurely.
                    raise APIError(f"{service} requested an extended retry delay")
                time.sleep(delay)
        raise APIError(last_error)

    def crossref(self, issn: str, rows: int) -> List[Dict[str, Any]]:
        params = {"filter": f"issn:{issn}", "sort": "published", "order": "desc", "rows": rows}
        if self.settings.crossref_email:
            params["mailto"] = self.settings.crossref_email

        def parse(response: requests.Response) -> List[Dict[str, Any]]:
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
                raise ValueError("invalid Crossref envelope")
            items = payload["message"].get("items")
            if not isinstance(items, list) or any(not isinstance(x, dict) for x in items):
                raise ValueError("missing item list")
            return items
        return self.request(CROSSREF_API, params, parse, "crossref")

    def _ncbi_params(self) -> Dict[str, Any]:
        params = {"db": "pubmed", "tool": "AnesTOC-Dashboard"}
        if self.settings.email:
            params["email"] = self.settings.email
        if self.settings.api_key:
            params["api_key"] = self.settings.api_key
        return params

    def doi_to_pmid(self, doi: str) -> Optional[str]:
        doi = normalize_doi(doi)
        if not doi:
            return None
        params = self._ncbi_params()
        params.update({"term": f'"{doi}"[AID]', "retmode": "json", "retmax": 2})

        def parse(response: requests.Response) -> Dict[str, Any]:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("invalid ESearch envelope")
            result = payload.get("esearchresult")
            if payload.get("error") or not isinstance(result, dict) or result.get("errorlist"):
                raise ValueError("ESearch error")
            ids = result.get("idlist")
            if not isinstance(ids, list) or any(not str(x).isdigit() for x in ids):
                raise ValueError("invalid ESearch identifiers")
            count = int(result.get("count", len(ids)))
            if count < len(ids) or (count > 0 and not ids):
                raise ValueError("incomplete ESearch identifiers")
            return {"ids": ids, "count": count}
        result = self.request(NCBI_ESEARCH, params, parse, "pubmed")
        if result["count"] > 1 or len(result["ids"]) > 1:
            raise AmbiguousDOI("multiple PubMed matches for this DOI")
        return str(result["ids"][0]) if result["ids"] else None

    def pubmed_details(self, pmids: List[str]) -> Dict[str, Dict[str, Any]]:
        if not pmids:
            return {}
        params = self._ncbi_params()
        params.update({"id": ",".join(pmids), "retmode": "xml"})
        return self.request(NCBI_EFETCH, params, lambda r: parse_pubmed_xml(r.text), "pubmed")


def parse_pubmed_xml(text: str) -> Dict[str, Dict[str, Any]]:
    root = ET.fromstring(text)
    if root.tag != "PubmedArticleSet" or root.find(".//ERROR") is not None:
        raise ValueError("invalid PubMed XML response")
    records: Dict[str, Dict[str, Any]] = {}
    for node in root:
        if node.tag == "PubmedArticle":
            doc, data = node.find("MedlineCitation"), node.find("PubmedData")
            type_path = "Article/PublicationTypeList/PublicationType"
            doi_path = "Article/ELocationID"
        elif node.tag == "PubmedBookArticle":
            doc, data = node.find("BookDocument"), node.find("PubmedBookData")
            type_path, doi_path = "PublicationTypeList/PublicationType", "ELocationID"
        else:
            continue
        if doc is None:
            continue
        pmid = (doc.findtext("PMID") or "").strip()
        if not pmid.isdigit():
            continue
        pts = list(dict.fromkeys(clean_text("".join(e.itertext())) for e in doc.findall(type_path)))
        dois = []
        for el in doc.findall(doi_path):
            if el.get("EIdType", "").lower() == "doi":
                dois.append(normalize_doi("".join(el.itertext())))
        status = ""
        if data is not None:
            # CURRENT PublicationStatus, not a historical aheadofprint event.
            status = (data.findtext("PublicationStatus") or "").strip().lower()
            for el in data.findall("ArticleIdList/ArticleId"):
                if el.get("IdType", "").lower() == "doi":
                    dois.append(normalize_doi("".join(el.itertext())))
        records[pmid] = {"publication_types": [p for p in pts if p],
                         "publication_status": status,
                         "dois": sorted(set(d for d in dois if d))}
    return records


# ---------------------------- AOP and record assembly --------------------------

def infer_aop(item: Dict[str, Any], entry: Optional[Dict[str, Any]] = None,
              now: Optional[datetime] = None, freshness_days: float = 7) -> Tuple[bool, str, str]:
    now = now or utc_now()
    today = now.date()
    dates = item.get("publication_dates") or {}
    online, printed = parse_partial_date(dates.get("published-online")), parse_partial_date(dates.get("published-print"))
    if printed and printed.last <= today:
        return False, "crossref", "print_publication_date_reached"
    entry = entry or {}
    status = entry.get("publication_status", "")
    fresh = age_days(entry.get("details_checked_at"), now) < freshness_days
    if status in {"ppublish", "epublish", "ecollection"}:
        return False, "pubmed", "final_publication_status"
    if status == "aheadofprint" and fresh:
        return True, "pubmed", "current_recently_checked_aheadofprint_status"
    if online and online.last <= today and printed and printed.first > today:
        return True, "crossref_inference", "online_publication_before_future_print_date"
    return False, "unknown", "insufficient_current_status_evidence"


def to_item(journal: str, short: str, raw: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or utc_now()
    doi = clean_doi(raw.get("DOI"))
    url = safe_url(raw.get("URL")) or (f"https://doi.org/{doi}" if doi else "")
    published, pub_source, precision = select_publication_date(raw, now.date())
    item = {
        "journal": journal, "journal_short": short, "title": clean_title(raw),
        "authors": join_authors(raw), "published": published, "doi": doi, "url": url,
        "source": "crossref", "crossref_type": clean_text(raw.get("type")),
        "publication_date_source": pub_source, "publication_date_precision": precision,
        "publication_dates": {f: extract_ymd(raw, f) for f in PUBLICATION_FIELDS if extract_ymd(raw, f)},
        "crossref_checked_at": now.isoformat(), "source_stale": False,
        "pubmed_publication_types": [], "category": CAT_UNK,
    }
    item["aop"], item["aop_source"], item["aop_reason"] = infer_aop(item, now=now)
    return item


def sort_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key(item: Dict[str, Any]) -> Tuple[int, int, str]:
        pd = parse_partial_date(item.get("published"))
        return (0 if pd else 1, -pd.first.toordinal() if pd else 0, item_key(item))
    return sorted(items, key=key)


def deduplicate(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in items:
        key = item_key(item)
        if not key or not item.get("title"):
            continue
        existing = out.get(key)
        if existing is None or (existing.get("source_stale") and not item.get("source_stale")):
            out[key] = item
    return list(out.values())


# ---------------------------- Configuration and safe previous data -------------

def load_sources(path: Path) -> List[Dict[str, Any]]:
    try:
        sources = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise BuildError("Cannot read a valid sources.json.") from exc
    if not isinstance(sources, list) or not sources:
        raise BuildError("sources.json must be a nonempty list.")
    result, seen = [], set()
    for source in sources:
        if not isinstance(source, dict) or not clean_text(source.get("name")):
            raise BuildError("Every source must have a nonempty name.")
        name = clean_text(source["name"])
        short = clean_text(source.get("short") or name)
        if not short or short.casefold() in seen:
            raise BuildError(f"Duplicate or empty source abbreviation: {short!r}.")
        seen.add(short.casefold())
        raw_issns = source.get("issn")
        if not isinstance(raw_issns, list):
            raw_issns = [raw_issns]
        issns = list(dict.fromkeys(normalize_issn(x) for x in raw_issns))
        if not issns:
            raise BuildError(f"No ISSN supplied for {name}.")
        result.append({"name": name, "short": short, "issns": issns})
    return result


def load_previous(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"items": [], "meta": {}}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuildError("Existing data.json cannot be read; refusing to overwrite it.") from exc
    if not isinstance(previous, dict) or not isinstance(previous.get("items"), list):
        raise BuildError("Existing data.json has an unexpected schema; refusing to overwrite it.")
    if any(not isinstance(i, dict) for i in previous["items"]):
        raise BuildError("Existing data.json has invalid article entries; refusing to overwrite it.")
    if not isinstance(previous.get("meta", {}), dict):
        raise BuildError("Existing data.json has invalid metadata; refusing to overwrite it.")
    return previous


def cached_source_items(previous: Dict[str, Any], source: Dict[str, Any]) -> List[Dict[str, Any]]:
    matched = []
    for old in previous["items"]:
        if old.get("journal_short") != source["short"] and old.get("journal") != source["name"]:
            continue
        item = copy.deepcopy(old)
        item.update(journal=source["name"], journal_short=source["short"], source_stale=True)
        item.setdefault("crossref_checked_at", previous.get("generated_at"))
        # Legacy dates could have been metadata timestamps. Do not recertify them
        # as publication dates while an upstream failure prevents repair.
        if item.get("publication_date_source") not in PUBLICATION_FIELDS:
            item.update(published=None, publication_date_source=None, publication_date_precision=None)
        if item_key(item) and item.get("title"):
            matched.append(item)
    return matched


def harvest(sources: List[Dict[str, Any]], previous: Dict[str, Any], client: APIClient,
            cfg: Settings, now: datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    all_items, reports = [], []
    consecutive_errors = 0
    circuit_open = False
    for source in sources:
        fresh, errors = [], []
        old = cached_source_items(previous, source)
        successes = 0
        for issn in source["issns"]:
            if circuit_open:
                errors.append(f"{issn}: requests suspended after repeated Crossref failures")
                continue
            try:
                raw_items = client.crossref(issn, cfg.rows_per_journal)
                consecutive_errors = 0
                if not raw_items:
                    errors.append(f"{issn}: empty Crossref response")
                    continue
                parsed = [to_item(source["name"], source["short"], raw, now) for raw in raw_items]
                usable = deduplicate(parsed)
                if not usable:
                    errors.append(f"{issn}: no usable records in Crossref response")
                    continue
                successes += 1
                fresh.extend(usable)
            except APIError as exc:
                errors.append(f"{issn}: {exc}")
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    circuit_open = True
        fresh = sort_items(deduplicate(fresh))[:cfg.rows_per_journal]
        suspicious_drop = (len(old) >= 20 and len(fresh) < len(old) * cfg.min_retained_fraction
                           and not cfg.allow_large_drop)
        if suspicious_drop and fresh:
            errors.append("unusually small source harvest; retaining missing cached records")
        degraded = bool(errors) or not fresh
        retained = sort_items(deduplicate(fresh + (old if degraded else [])))[:cfg.rows_per_journal]
        status = "ok" if not degraded else "partial" if fresh else "cached" if old else "unavailable"
        if degraded:
            log(f"{source['short']}: {status}; " + "; ".join(errors), warning=True)
        all_items.extend(retained)
        reports.append({
            "name": source["name"], "short": source["short"], "issns": source["issns"],
            "status": status, "successful_issns": successes, "records_fetched": len(fresh),
            "records_before_global_cap": len(retained), "records_retained": 0,
            "errors": errors,
        })
    successful = sum(r["records_fetched"] > 0 for r in reports)
    if successful == 0:
        raise BuildError("No source returned usable records. Previous data.json has been preserved.")
    if successful / len(sources) < cfg.min_source_success_ratio:
        raise BuildError("Too few sources returned usable records. Previous data.json has been preserved.")
    return sort_items(deduplicate(all_items)), reports


# ---------------------------- Persistent PubMed enrichment ---------------------

CACHE_FIELDS = {
    "pmid", "lookup_status", "lookup_checked_at", "publication_types", "publication_status",
    "details_checked_at", "details_attempted_at", "details_error_at", "dois", "match_verified",
    "last_seen_at",
}


def seed_cache(previous: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    cache = {}
    stored = previous.get("meta", {}).get("pubmed_cache", {})
    if isinstance(stored, dict):
        for key, value in stored.items():
            doi = normalize_doi(key)
            if doi and isinstance(value, dict):
                entry = {k: copy.deepcopy(v) for k, v in value.items() if k in CACHE_FIELDS}
                if not str(entry.get("pmid", "")).isdigit():
                    entry.pop("pmid", None)
                pts = entry.get("publication_types", [])
                entry["publication_types"] = [p for p in pts if isinstance(p, str)] if isinstance(pts, list) else []
                cache[doi] = entry
    for item in previous["items"]:
        doi = normalize_doi(item.get("doi"))
        if not doi:
            continue
        entry = cache.setdefault(doi, {})
        pmid = str(item.get("pmid") or "")
        if pmid.isdigit() and not entry.get("pmid"):
            entry.update(pmid=pmid, lookup_status="matched", match_verified=False)
        pts = item.get("pubmed_publication_types")
        if isinstance(pts, list) and not entry.get("publication_types"):
            entry["publication_types"] = [p for p in pts if isinstance(p, str)]
        entry.setdefault("last_seen_at", previous.get("generated_at"))
    return cache


def lookup_due(entry: Dict[str, Any], cfg: Settings, now: datetime) -> bool:
    if entry.get("pmid"):
        return False
    status = entry.get("lookup_status")
    interval = cfg.error_retry_hours / 24 if status == "error" else cfg.not_found_retry_days
    return age_days(entry.get("lookup_checked_at"), now) >= interval


def details_due(entry: Dict[str, Any], cfg: Settings, now: datetime) -> bool:
    if not entry.get("pmid"):
        return False
    if age_days(entry.get("details_error_at"), now) < cfg.error_retry_hours / 24:
        return False
    interval = cfg.aop_refresh_days if entry.get("publication_status") == "aheadofprint" else cfg.refresh_days
    return age_days(entry.get("details_checked_at"), now) >= interval


def balanced_lookup_queue(items: List[Dict[str, Any]], cache: Dict[str, Dict[str, Any]],
                          cfg: Settings, now: datetime) -> List[str]:
    """Reserve room for both never-queried records and older due retries."""
    due = [normalize_doi(i.get("doi")) for i in items if normalize_doi(i.get("doi"))]
    due = list(dict.fromkeys(d for d in due if lookup_due(cache[d], cfg, now)))
    new = [d for d in due if not cache[d].get("lookup_checked_at")]
    retried = [d for d in due if cache[d].get("lookup_checked_at")]
    retried.sort(key=lambda d: str(cache[d].get("lookup_checked_at") or ""))
    reserve = cfg.pmid_lookup_budget // 2
    selected = new[:cfg.pmid_lookup_budget - reserve] + retried[:reserve]
    used = set(selected)
    selected.extend(d for d in new + retried if d not in used)
    return selected[:cfg.pmid_lookup_budget]


def enrich(items: List[Dict[str, Any]], cache: Dict[str, Dict[str, Any]], client: APIClient,
           cfg: Settings, now: datetime) -> Dict[str, int]:
    stamp = now.isoformat()
    stats = {"doi_lookups": 0, "lookup_errors": 0, "details_requested": 0,
             "details_errors": 0, "doi_mismatches": 0, "linked_items": 0,
             "lookup_skipped_circuit_open": 0, "details_skipped_circuit_open": 0}
    for item in items:
        doi = normalize_doi(item.get("doi"))
        if doi:
            cache.setdefault(doi, {})["last_seen_at"] = stamp
    queue = balanced_lookup_queue(items, cache, cfg, now)
    consecutive_errors = 0
    pubmed_circuit_open = False
    for position, doi in enumerate(queue):
        entry = cache[doi]
        stats["doi_lookups"] += 1
        entry["lookup_checked_at"] = stamp
        try:
            pmid = client.doi_to_pmid(doi)
            consecutive_errors = 0
            entry["lookup_status"] = "matched" if pmid else "not_found"
            if pmid:
                entry.update(pmid=pmid, match_verified=False)
        except AmbiguousDOI:
            consecutive_errors = 0
            entry["lookup_status"] = "ambiguous"
            log(f"Ambiguous PubMed match for {doi}; no link assigned.", warning=True)
        except APIError as exc:
            entry["lookup_status"] = "error"
            stats["lookup_errors"] += 1
            log(f"PubMed lookup for {doi}: {exc}", warning=True)
            consecutive_errors += 1
            if consecutive_errors >= 5:
                pubmed_circuit_open = True
                stats["lookup_skipped_circuit_open"] = len(queue) - position - 1
                log("PubMed requests suspended after repeated failures; cached enrichment will be used.", warning=True)
                break

    active_dois = list(dict.fromkeys(normalize_doi(i.get("doi")) for i in items if normalize_doi(i.get("doi"))))
    due_dois = [d for d in active_dois if details_due(cache[d], cfg, now)]
    # Oldest never-checked records first, then overdue AOP, then ordinary refresh.
    due_dois.sort(key=lambda d: (0 if not cache[d].get("details_checked_at") else
                                1 if cache[d].get("publication_status") == "aheadofprint" else 2,
                                str(cache[d].get("details_checked_at") or "")))
    pmids = list(dict.fromkeys(str(cache[d]["pmid"]) for d in due_dois))[:cfg.efetch_record_budget]
    failed_batches = 0
    for start in range(0, len(pmids), cfg.efetch_batch_size):
        if pubmed_circuit_open or failed_batches >= 3:
            stats["details_skipped_circuit_open"] = len(pmids) - start
            log("Remaining PubMed detail requests deferred; cached enrichment retained.", warning=True)
            break
        batch = pmids[start:start + cfg.efetch_batch_size]
        stats["details_requested"] += len(batch)
        batch_set = set(batch)
        related = [d for d in active_dois if str(cache[d].get("pmid")) in batch_set]
        for doi in related:
            cache[doi]["details_attempted_at"] = stamp
        try:
            details = client.pubmed_details(batch)
            failed_batches = 0
            missing = len(batch_set - set(details))
            if missing:
                log(f"PubMed omitted {missing} requested record(s); previous enrichment retained for them.", warning=True)
        except APIError as exc:
            details = {}
            failed_batches += 1
            log(f"PubMed detail batch failed: {exc}; cached enrichment retained.", warning=True)
        for doi in related:
            entry = cache[doi]
            got = details.get(str(entry["pmid"]))
            if got is None:
                entry["details_error_at"] = stamp
                stats["details_errors"] += 1
                continue
            reported_dois = got.get("dois") or []
            if reported_dois and doi not in reported_dois:
                # An explicit identifier conflict is not a transient API failure.
                # Remove the wrong association, rather than preserving a bad link.
                for key in ("pmid", "publication_types", "publication_status", "details_checked_at", "dois"):
                    entry.pop(key, None)
                entry.update(lookup_status="mismatch", lookup_checked_at=stamp, match_verified=False)
                stats["doi_mismatches"] += 1
                log(f"PubMed DOI mismatch for {doi}; association removed.", warning=True)
                continue
            entry.update(publication_types=got.get("publication_types", []),
                         publication_status=got.get("publication_status", ""),
                         dois=reported_dois, match_verified=bool(reported_dois),
                         details_checked_at=stamp)
            entry.pop("details_error_at", None)

    for item in items:
        doi = normalize_doi(item.get("doi"))
        entry = cache.get(doi, {})
        pmid = entry.get("pmid")
        if pmid:
            item.update(pmid=str(pmid), pubmed_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        pubmed_match_verified=bool(entry.get("match_verified")))
            stats["linked_items"] += 1
        else:
            for key in ("pmid", "pubmed_url", "pubmed_match_verified"):
                item.pop(key, None)
        item["pubmed_publication_types"] = entry.get("publication_types", []) if pmid else []
        item["pubmed_publication_status"] = entry.get("publication_status") if pmid else None
        item["pubmed_checked_at"] = entry.get("details_checked_at") if pmid else None
        item["category"], item["category_source"], item["category_reason"] = classify(
            item["pubmed_publication_types"], item.get("title", ""))
        item["category_label"] = CATEGORY_LABELS[item["category"]]
        item["aop"], item["aop_source"], item["aop_reason"] = infer_aop(item, entry, now, cfg.aop_refresh_days)
    return stats


def prune_cache(cache: Dict[str, Dict[str, Any]], active_dois: set, cfg: Settings,
                now: datetime) -> Dict[str, Dict[str, Any]]:
    # Always retain active-item entries; the configured bound applies to inactive
    # history when the number of current items itself exceeds the bound.
    eligible = [d for d, e in cache.items() if d in active_dois or age_days(e.get("last_seen_at"), now) <= cfg.cache_retention_days]
    eligible.sort(key=lambda d: (d not in active_dois, -((parse_timestamp(cache[d].get("last_seen_at")) or
                    datetime.min.replace(tzinfo=timezone.utc)) - datetime.min.replace(tzinfo=timezone.utc)).total_seconds(), d))
    keep = eligible[:max(cfg.cache_max_entries, len(active_dois))]
    return {d: cache[d] for d in sorted(keep)}


# ---------------------------- Validation, writing and entry point --------------

def validate_output(output: Dict[str, Any]) -> None:
    if not output.get("items"):
        raise BuildError("Refusing to publish an empty dataset.")
    seen = set()
    for item in output["items"]:
        key = item_key(item)
        if not key or key in seen or not item.get("title") or not safe_url(item.get("url")):
            raise BuildError("Generated dataset contains an invalid or duplicate record.")
        seen.add(key)
        if item.get("published") is not None and not parse_partial_date(item["published"]):
            raise BuildError("Generated dataset contains an invalid publication date.")
        if type(item.get("aop")) is not bool or item.get("category") not in CATEGORY_LABELS:
            raise BuildError("Generated dataset contains an incompatible category/AOP field.")
    # Ensures serializability and rejects NaN/Infinity before touching the old file.
    json.dumps(output, ensure_ascii=False, allow_nan=False)


def atomic_write(path: Path, output: Dict[str, Any]) -> None:
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".data-", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            json.dump(output, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def resolve_root() -> Path:
    override = os.getenv("TOC_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    script_dir = Path(__file__).resolve().parent
    for candidate in (script_dir.parent, script_dir, Path.cwd()):
        if (candidate / "sources.json").is_file():
            return candidate
    raise BuildError("sources.json was not found. Place this script in the repository's scripts directory.")


def build(root: Path, cfg: Optional[Settings] = None, client: Optional[APIClient] = None,
          now: Optional[datetime] = None) -> Dict[str, Any]:
    cfg = cfg or Settings.from_env()
    now = now or utc_now()
    root = Path(root)
    out_path = root / "data.json"
    sources = load_sources(root / "sources.json")
    previous = load_previous(out_path)
    client = client or APIClient(cfg)
    items, reports = harvest(sources, previous, client, cfg, now)
    candidate_count = len(items)
    items = items[:cfg.global_max_items]  # Do not spend PubMed requests on discarded records.
    configured_shorts = {s["short"] for s in sources}
    configured_names = {s["name"] for s in sources}
    old_comparable = [i for i in previous["items"] if i.get("journal_short") in configured_shorts
                      or i.get("journal") in configured_names]
    baseline = min(cfg.global_max_items, len(deduplicate(old_comparable)))
    if (baseline >= 20 and len(items) < baseline * cfg.min_retained_fraction and not cfg.allow_large_drop):
        raise BuildError("Unexplained dataset collapse; previous data.json preserved. Use ALLOW_LARGE_DROP=1 only for an intentional reset.")
    if not items:
        raise BuildError("No usable records; previous data.json preserved.")

    # Preserve existing DOI spelling/case because browser bookmarks use it as a key.
    previous_by_key = {item_key(i): i for i in previous["items"] if item_key(i)}
    for item in items:
        old = previous_by_key.get(item_key(item), {})
        if clean_doi(old.get("doi")) and normalize_doi(old["doi"]) == normalize_doi(item.get("doi")):
            item["doi"] = old["doi"]

    cache = seed_cache(previous)
    stats = enrich(items, cache, client, cfg, now)
    active = {normalize_doi(i.get("doi")) for i in items if normalize_doi(i.get("doi"))}
    cache = prune_cache(cache, active, cfg, now)
    for report in reports:
        report["records_retained"] = sum(i["journal_short"] == report["short"] for i in items)
        report["cached_records_retained"] = sum(i["journal_short"] == report["short"] and
                                                i.get("source_stale", False) for i in items)
        log(f"{report['short']}: {report['records_fetched']} fetched, {report['records_retained']} in final feed ({report['status']}).")
    degraded = any(r["status"] != "ok" for r in reports) or any(
        stats[k] for k in ("lookup_errors", "details_errors", "doi_mismatches"))
    output = {
        "generated_at": now.isoformat(), "items": items,
        "meta": {
            "schema_version": 2, "build_version": VERSION,
            "rows_per_journal": cfg.rows_per_journal, "global_max_items": cfg.global_max_items,
            "pmid_lookup_budget": cfg.pmid_lookup_budget, "categories": DASHBOARD_CATEGORIES,
            "category_labels": CATEGORY_LABELS, "category_rules_version": VERSION,
            "publication_date_policy": "earliest_explicit_publication_with_generic_publication_fallback",
            "partial_dates": "preserve_source_precision", "aop_policy": "conservative_current_evidence",
            "build_status": "degraded" if degraded else "ok", "candidate_items": candidate_count,
            "truncated_by_global_cap": max(0, candidate_count - len(items)),
            "sources": reports, "pubmed_enrichment": stats, "pubmed_cache": cache,
        },
    }
    validate_output(output)
    atomic_write(out_path, output)
    log(f"Wrote {len(items)} records to data.json; {len(sources)} configured sources; "
        f"{stats['linked_items']} PubMed links; {stats['doi_lookups']} DOI lookups; "
        f"status={output['meta']['build_status']}.")
    return output


def main() -> int:
    try:
        build(resolve_root())
        return 0
    except (BuildError, APIError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("[ERROR] File-system operation failed; no successful replacement was confirmed.", file=sys.stderr)
        return 1
    except Exception as exc:
        # Avoid logging arbitrary exceptions that might contain an API-key URL.
        print(f"[ERROR] Unexpected {type(exc).__name__}; build aborted. Inspect the code before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
