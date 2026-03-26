import argparse
import csv
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


SCHEMA_FIELDS: List[str] = [
    "url",
    "source",
    "title",
    "opportunity_number",
    "agency",
    "cfda",
    "synopsis",
    "description",
    "posted_date",
    "closing_date",
    "funding_instrument",
    "eligibility",
    "tags",
]


def _detect_source(url: str) -> str:
    u = url.lower()
    if "grants.gov" in u:
        return "grants.gov"
    if "nsf.gov" in u or "nsf" in u:
        return "nsf.gov"
    return "unknown"


def _fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "foa-ingest/0.1 (contact: local-script)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        # Some FOA URLs might be PDFs or JSON endpoints. Keep the failure explicit.
        raise RuntimeError(f"URL did not return HTML (Content-Type: {content_type or 'unknown'}).")

    return resp.text


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _parse_date(raw: str) -> Optional[str]:
    """
    Returns ISO date string YYYY-MM-DD when possible, else None.
    """
    raw = _clean_text(raw)
    if not raw:
        return None

    # Remove common time-of-day fragments.
    raw = re.sub(r"\b\d{1,2}:\d{2}\s*(am|pm)\b", "", raw, flags=re.I).strip()

    # ISO-like first.
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d.isoformat()
        except ValueError:
            pass

    # Common US formats.
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue

    # Sometimes the page has extra words like "Closing Date: March 1, 2026".
    # Try to find a date substring within the text.
    m = re.search(
        r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4})",
        raw,
        flags=re.I,
    )
    if m:
        candidate = _clean_text(m.group(1))
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue

    return None


def _extract_meta_content(soup: BeautifulSoup, selectors: List[Tuple[str, Dict[str, str]]]) -> Optional[str]:
    for _, attrs in selectors:
        # meta property / name.
        m = soup.find("meta", attrs=attrs)
        if m and m.get("content"):
            return _clean_text(m.get("content", ""))
    return None


def _first_text_matching(soup: BeautifulSoup, label: str, max_lines: int = 200) -> Optional[str]:
    """
    Finds a line starting with `label` (case-insensitive) and returns the remainder.
    Works best when soup.get_text("\n") preserves label lines.
    """
    label_re = re.compile(rf"^{re.escape(label)}\s*[:\-]?\s*(.+)$", flags=re.I)
    lines = [l.strip() for l in soup.get_text("\n", strip=True).splitlines() if l.strip()]
    for line in lines[:max_lines]:
        m = label_re.match(line)
        if m:
            return _clean_text(m.group(1))
    return None


def _extract_by_patterns(soup: BeautifulSoup, patterns: List[str], max_lines: int = 5000) -> Optional[str]:
    """
    patterns are regexes with one capturing group for the value.
    """
    lines = [l.strip() for l in soup.get_text("\n", strip=True).splitlines() if l.strip()]
    for line in lines[:max_lines]:
        for pat in patterns:
            m = re.match(pat, line, flags=re.I)
            if m and m.groups():
                return _clean_text(m.group(1))
    return None


def extract_foa_fields(url: str) -> Dict[str, Any]:
    source = _detect_source(url)
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    title = _extract_meta_content(
        soup,
        [
            ("og:title", {"property": "og:title"}),
            ("twitter:title", {"name": "twitter:title"}),
            ("title", {"name": "title"}),
        ],
    )
    if not title:
        # Try common labeled title fields first.
        title = _extract_by_patterns(
            soup,
            [
                r"Opportunity Title\s*[:\-]?\s*(.+)",
                r"Funding Opportunity Title\s*[:\-]?\s*(.+)",
                r"Announcement Title\s*[:\-]?\s*(.+)",
            ],
        )
    if not title:
        # Fall back to the first h1.
        h1 = soup.find("h1")
        if h1:
            title = _clean_text(h1.get_text(" ", strip=True))
    if not title:
        title = _clean_text(soup.title.get_text(" ", strip=True)) if soup.title else None

    description = _extract_meta_content(soup, [("description", {"name": "description"})])

    # Try to get a synopsis-like field from labels.
    synopsis = None
    for label in ["Synopsis", "Overview", "Summary"]:
        synopsis = _first_text_matching(soup, label, max_lines=500)
        if synopsis:
            break

    # Label/pattern extraction for structured-ish pages.
    opportunity_number = _extract_by_patterns(
        soup,
        [
            r"Funding Opportunity Number\s*[:\-]?\s*(.+)",
            r"Opportunity Number\s*[:\-]?\s*(.+)",
            r"FOA Number\s*[:\-]?\s*(.+)",
        ],
    )
    agency = _extract_by_patterns(
        soup,
        [
            r"Agency\s*[:\-]?\s*(.+)",
            r"Federal Agency\s*[:\-]?\s*(.+)",
            r"Funding Agency\s*[:\-]?\s*(.+)",
        ],
    )
    cfda = _extract_by_patterns(
        soup,
        [
            r"CFDA\s*[:\-]?\s*(.+)",
            r"Catalog of Federal Domestic Assistance\s*[:\-]?\s*(.+)",
        ],
    )
    funding_instrument = _extract_by_patterns(
        soup,
        [
            r"Funding Instrument\s*[:\-]?\s*(.+)",
            r"Assistance Listing\s*[:\-]?\s*(.+)",
            r"Type of Award\s*[:\-]?\s*(.+)",
        ],
    )
    eligibility = _extract_by_patterns(
        soup,
        [
            r"Eligibility\s*[:\-]?\s*(.+)",
            r"Who May Submit\s*[:\-]?\s*(.+)",
            r"Applicants\s*[:\-]?\s*(.+)",
        ],
    )

    posted_date_raw = _extract_by_patterns(
        soup,
        [
            r"Posted Date\s*[:\-]?\s*(.+)",
            r"Posted\s*[:\-]?\s*(.+)",
            r"Release Date\s*[:\-]?\s*(.+)",
        ],
    )
    closing_date_raw = _extract_by_patterns(
        soup,
        [
            r"Closing Date\s*[:\-]?\s*(.+)",
            r"Close Date\s*[:\-]?\s*(.+)",
            r"Deadline\s*[:\-]?\s*(.+)",
            r"Due Date\s*[:\-]?\s*(.+)",
            r"Application Deadline\s*[:\-]?\s*(.+)",
        ],
    )

    posted_date = _parse_date(posted_date_raw or "")
    closing_date = _parse_date(closing_date_raw or "")

    # Build a description fallback to ensure something meaningful exists.
    if not description and synopsis:
        description = synopsis

    record: Dict[str, Any] = {
        "url": url,
        "source": source,
        "title": title,
        "opportunity_number": opportunity_number,
        "agency": agency,
        "cfda": cfda,
        "synopsis": synopsis,
        "description": description,
        "posted_date": posted_date,
        "closing_date": closing_date,
        "funding_instrument": funding_instrument,
        "eligibility": eligibility,
        "tags": [],  # filled by rule_based_tags()
    }

    record["tags"] = rule_based_tags(record)
    return record


def rule_based_tags(record: Dict[str, Any]) -> List[str]:
    """
    Deterministic, rule-based tagging.
    No ML, no randomness.
    """
    title = (record.get("title") or "").lower()
    desc = (record.get("description") or "").lower()
    synopsis = (record.get("synopsis") or "").lower()
    blob = " ".join([title, desc, synopsis]).strip()

    tags: List[str] = []

    # Source tags.
    source = (record.get("source") or "").lower()
    if "grants.gov" in source:
        tags.append("source:grants_gov")
    if "nsf.gov" in source:
        tags.append("source:nsf_gov")

    # Urgency tag.
    closing_iso = record.get("closing_date")
    if closing_iso:
        try:
            closing_dt = datetime.strptime(closing_iso, "%Y-%m-%d").date()
            days_left = (closing_dt - date.today()).days
            if days_left < 0:
                tags.append("deadline:passed")
            elif days_left <= 30:
                tags.append("deadline:closing_soon")
        except ValueError:
            pass

    # Keyword taxonomy (edit to match your needs).
    keyword_map: Dict[str, List[str]] = {
        "topic:ai_ml": ["artificial intelligence", "machine learning", "ai ", "ml "],
        "topic:cybersecurity": ["cyber", "cybersecurity", "secure coding", "malware"],
        "topic:health_biomed": ["health", "biomedical", "biomed", "medical", "clinical"],
        "topic:education": ["education", "educational", "learning", "teacher training"],
        "topic:energy": ["energy", "renewable", "solar", "wind", "grid", "battery"],
        "topic:climate_environment": ["climate", "environment", "ecosystem", "carbon", "sustainability"],
        "audience:academic": ["university", "college", "faculty", "academic", "research institution"],
        "audience:small_business": ["small business", "sbir", "sttr", "entrepreneur"],
        "audience:nonprofit": ["nonprofit", "non-profit", "foundation", "ng o", "non-governmental"],
        "format:conference_workshop": ["conference", "workshop", "symposium", "seminar"],
    }

    for tag, keywords in keyword_map.items():
        if any(k in blob for k in keywords):
            tags.append(tag)

    # If we couldn't identify anything, still provide a deterministic tag.
    if not tags:
        tags.append("topic:unknown")

    # Deterministic ordering (stable across runs).
    return sorted(set(tags))


def write_outputs(record: Dict[str, Any], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "foa.json")
    csv_path = os.path.join(out_dir, "foa.csv")

    # JSON: keep arrays as arrays.
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    # CSV: flatten tags to a single string column for easy spreadsheet use.
    csv_record = dict(record)
    tags = csv_record.get("tags") or []
    if isinstance(tags, list):
        csv_record["tags"] = ";".join(tags)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SCHEMA_FIELDS)
        writer.writeheader()
        writer.writerow({k: csv_record.get(k) for k in SCHEMA_FIELDS})


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest one FOA URL and produce foa.json + foa.csv.")
    parser.add_argument("--url", required=True, help="Grants.gov or NSF FOA URL to ingest")
    parser.add_argument("--out_dir", default="./out", help="Output directory (creates if missing)")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        record = extract_foa_fields(args.url)
        write_outputs(record, args.out_dir)
    except Exception as e:
        print(f"[ERROR] Failed to ingest FOA URL: {e}", file=sys.stderr)
        return 2

    print(f"Wrote: {os.path.join(args.out_dir, 'foa.json')}")
    print(f"Wrote: {os.path.join(args.out_dir, 'foa.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

