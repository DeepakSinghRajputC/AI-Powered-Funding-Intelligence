import argparse
import csv
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime
from typing import Any

import requests
from bs4 import BeautifulSoup

SCHEMA_FIELDS: list[str] = [
    "foa_id",
    "title",
    "agency",
    "open_date",
    "close_date",
    "eligibility_text",
    "program_description",
    "award_min",
    "award_max",
    "award_range",
    "opportunity_number",
    "assistance_listing",
    "funding_instrument",
    "source",
    "source_url",
    "synopsis",
    "tags",
]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return re.sub(r"\s+", " ", value).strip()


def _slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def _detect_source(url: str) -> str:
    lowered = url.lower()
    if "grants.gov" in lowered:
        return "grants.gov"
    if "nsf.gov" in lowered:
        return "nsf.gov"
    return "unknown"


def _fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "foa-ingest/0.3 (research-discovery-demo)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise RuntimeError(
            f"URL did not return HTML content. Received Content-Type: {content_type or 'unknown'}"
        )

    return response.text


def _extract_visible_lines(soup: BeautifulSoup) -> list[str]:
    raw_lines = soup.get_text("\n", strip=True).splitlines()
    cleaned = [_clean_text(line) for line in raw_lines]
    return [line for line in cleaned if line]


def _normalize_label(label: str) -> str:
    label = _clean_text(label).lower()
    label = re.sub(r"\s+", " ", label)
    label = label.rstrip(":")
    return label


def _candidate_labels(labels: list[str]) -> set[str]:
    variants: set[str] = set()
    for label in labels:
        norm = _normalize_label(label)
        variants.add(norm)
        if norm.endswith("s"):
            variants.add(norm[:-1])
        else:
            variants.add(norm + "s")
    return variants


def _looks_like_label(line: str) -> bool:
    normalized = _normalize_label(line)
    if not normalized:
        return False
    if line.endswith(":"):
        return True
    return bool(re.match(r"^[A-Z][A-Za-z0-9 /&()'.,\-]{1,50}$", line))


def _next_meaningful_value(
    lines: list[str],
    start_index: int,
    blocked_labels: set[str] | None = None,
    max_lookahead: int = 4,
) -> str | None:
    blocked_labels = blocked_labels or set()

    for offset in range(1, max_lookahead + 1):
        idx = start_index + offset
        if idx >= len(lines):
            break

        candidate = _clean_text(lines[idx])
        if not candidate or candidate == ":":
            continue

        candidate_norm = _normalize_label(candidate)
        if candidate_norm in blocked_labels:
            return None

        if candidate.endswith(":") and offset == 1:
            continue

        return candidate

    return None


def _find_label_value(lines: list[str], labels: list[str]) -> str | None:
    candidates = _candidate_labels(labels)

    for index, line in enumerate(lines):
        line_norm = _normalize_label(line)

        if line_norm in candidates:
            value = _next_meaningful_value(
                lines, index, blocked_labels=candidates, max_lookahead=4
            )
            if value:
                return value

        raw_match = re.match(r"^(.+?)\s*:\s*(.+)$", line)
        if raw_match:
            left = _normalize_label(raw_match.group(1))
            right = _clean_text(raw_match.group(2))
            if left in candidates and right and right != ":":
                return right

    return None


def _extract_section_text(
    lines: list[str],
    start_labels: list[str],
    stop_labels: list[str],
) -> str | None:
    start_candidates = _candidate_labels(start_labels)
    stop_candidates = _candidate_labels(stop_labels)

    start_index: int | None = None
    for index, line in enumerate(lines):
        if _normalize_label(line) in start_candidates:
            start_index = index + 1
            break

    if start_index is None:
        return None

    collected: list[str] = []
    for line in lines[start_index:]:
        normalized = _normalize_label(line)
        if normalized in stop_candidates:
            break

        if line.lower() in {"jump to all documents", "download all", "return to top"}:
            continue

        collected.append(line)

    text = _clean_text(" ".join(collected))
    return text or None


def _extract_title(soup: BeautifulSoup, lines: list[str]) -> str | None:
    h1 = soup.find("h1")
    if h1:
        title = _clean_text(h1.get_text(" ", strip=True))
        if title:
            return title

    meta_og = soup.find("meta", attrs={"property": "og:title"})
    if meta_og and meta_og.get("content"):
        title = _clean_text(meta_og.get("content"))
        title = re.sub(r"^Opportunity Listing\s*-\s*", "", title, flags=re.I)
        if title:
            return title

    if soup.title:
        title = _clean_text(soup.title.get_text(" ", strip=True))
        title = re.sub(r"^Opportunity Listing\s*-\s*", "", title, flags=re.I)
        if title:
            return title

    for line in lines[:50]:
        if "opportunity listing -" in line.lower():
            cleaned = re.sub(r"^Opportunity Listing\s*-\s*", "", line, flags=re.I)
            if cleaned:
                return cleaned

    for line in lines[:50]:
        if len(line) > 10 and not line.endswith(":"):
            return line

    return None


def _parse_date(raw: str | None) -> str | None:
    value = _clean_text(raw)
    if not value:
        return None

    value = re.sub(r"\b\d{1,2}:\d{2}\s*(AM|PM|am|pm)\b", "", value).strip()

    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
    ):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    match = re.search(
        r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4})",
        value,
        flags=re.I,
    )
    if match:
        candidate = _clean_text(match.group(1))
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(candidate, fmt).date().isoformat()
            except ValueError:
                continue

    return None


def _parse_money(raw: str | None) -> int | None:
    value = _clean_text(raw)
    if not value or value == "--":
        return None

    digits = re.sub(r"[^0-9.]", "", value)
    if not digits:
        return None

    try:
        number = float(digits)
    except ValueError:
        return None

    return int(number)


def _build_award_range(award_min: int | None, award_max: int | None) -> str | None:
    if award_min is None and award_max is None:
        return None
    if award_min is not None and award_max is not None:
        return f"${award_min:,} - ${award_max:,}"
    if award_min is not None:
        return f"From ${award_min:,}"
    return f"Up to ${award_max:,}"


def _generate_foa_id(
    source: str, source_url: str, opportunity_number: str | None
) -> str:
    if opportunity_number:
        return f"{_slugify(source)}-{_slugify(opportunity_number)}"

    digest = hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:12]
    return f"{_slugify(source)}-{digest}"


def _extract_description(lines: list[str]) -> str | None:
    description = _extract_section_text(
        lines,
        start_labels=["Description"],
        stop_labels=[
            "Eligibility",
            "Grantor contact information",
            "Documents",
            "Award",
            "History",
            "Application process",
            "Link to additional information",
            "Explore",
        ],
    )
    if not description:
        return None

    description = description.replace("Jump to all documents", "").strip()
    return _clean_text(description) or None


def _extract_eligibility(lines: list[str]) -> str | None:
    eligibility = _extract_section_text(
        lines,
        start_labels=["Eligibility"],
        stop_labels=[
            "Grantor contact information",
            "Documents",
            "Award",
            "History",
            "Application process",
            "Link to additional information",
            "Explore",
        ],
    )
    return _clean_text(eligibility) or None


def _extract_assistance_listing(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        if _normalize_label(line) not in _candidate_labels(
            ["Assistance Listing", "Assistance Listings"]
        ):
            continue

        stop_candidates = _candidate_labels(
            [
                "Last Updated",
                "Description",
                "Eligibility",
                "Agency",
                "Posted date",
                "View version history on Grants.gov",
            ]
        )
        parts: list[str] = []
        for offset in range(1, 6):
            idx = index + offset
            if idx >= len(lines):
                break
            current = _clean_text(lines[idx])
            if not current or current == ":":
                continue
            if _normalize_label(current) in stop_candidates or current.endswith(":"):
                break
            parts.append(current)

        if not parts:
            continue

        cleaned_parts = [part for part in parts if part != "--"]
        joined = " -- ".join(cleaned_parts[:2]) if cleaned_parts else None
        if joined:
            return joined

    return _find_label_value(
        lines,
        [
            "Assistance Listing",
            "Assistance Listings",
            "CFDA",
            "Catalog of Federal Domestic Assistance",
        ],
    )


def _extract_funding_instrument(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        if _normalize_label(line) not in _candidate_labels(
            ["Funding instrument type", "Funding instrument"]
        ):
            continue

        values: list[str] = []
        for offset in range(1, 6):
            idx = index + offset
            if idx >= len(lines):
                break

            current = _clean_text(lines[idx])
            if not current or current == ":":
                continue

            current_norm = _normalize_label(current)
            if current_norm in _candidate_labels(
                [
                    "Opportunity Category",
                    "Opportunity Category Explanation",
                    "Category of Funding Activity",
                    "Category Explanation",
                    "Cost sharing or matching requirement",
                    "History",
                    "Version",
                    "Posted date",
                    "Archive date",
                ]
            ):
                break

            values.append(current)

        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            key = value.lower()
            if key not in seen:
                deduped.append(value)
                seen.add(key)

        if deduped:
            return "; ".join(deduped)

    return _find_label_value(lines, ["Funding instrument type", "Funding instrument"])


def _extract_award_values(lines: list[str]) -> tuple[int | None, int | None]:
    award_min: int | None = None
    award_max: int | None = None

    for index, line in enumerate(lines):
        normalized = _normalize_label(line)

        if normalized == _normalize_label("Award Minimum"):
            previous_value = _parse_money(lines[index - 1]) if index - 1 >= 0 else None
            next_value = _next_meaningful_value(lines, index, max_lookahead=2)
            next_money = _parse_money(next_value)

            # On simpler.grants.gov the dollar value sits one line BEFORE the label.
            # Prefer that; fall back to next-line for other page layouts.
            if previous_value is not None:
                award_min = previous_value
            elif next_money is not None:
                award_min = next_money

        if normalized == _normalize_label("Award Maximum"):
            previous_value = _parse_money(lines[index - 1]) if index - 1 >= 0 else None
            next_value = _next_meaningful_value(lines, index, max_lookahead=2)
            next_money = _parse_money(next_value)

            if previous_value is not None:
                award_max = previous_value
            elif next_money is not None:
                award_max = next_money

    return award_min, award_max


def _summary_from_description(description: str | None) -> str | None:
    if not description:
        return None

    parts = re.split(r"(?<=[.!?])\s+", description)
    if not parts:
        return description

    summary = " ".join(parts[:2]).strip()
    return _clean_text(summary)


def rule_based_tags(record: dict[str, Any]) -> list[str]:
    title = _clean_text(record.get("title")).lower()
    agency = _clean_text(record.get("agency")).lower()
    description = _clean_text(record.get("program_description")).lower()
    eligibility = _clean_text(record.get("eligibility_text")).lower()
    blob = " ".join([title, agency, description, eligibility]).strip()

    tags: list[str] = []

    source = _clean_text(record.get("source")).lower()
    if source == "grants.gov":
        tags.append("source:grants_gov")
    elif source == "nsf.gov":
        tags.append("source:nsf_gov")

    keyword_map: dict[str, list[str]] = {
        "research_domain:defense": [
            "air force",
            "defense",
            "dod",
            "department of defense",
        ],
        "research_domain:science_engineering": [
            "science",
            "research",
            "technology",
            "engineering",
        ],
        "method:conference_workshop": [
            "conference",
            "workshop",
            "symposium",
            "seminar",
        ],
        "population:higher_education": [
            "institution of higher education",
            "institutions of higher education",
            "higher education",
            "university",
            "college",
            "graduate students",
        ],
        "population:nonprofit": ["nonprofit", "non-profit", "charitable"],
        "sponsor_theme:basic_research": ["basic research"],
        "sponsor_theme:research_exchange": [
            "interchange",
            "research interest",
            "research findings",
        ],
    }

    for tag, keywords in keyword_map.items():
        if any(keyword in blob for keyword in keywords):
            tags.append(tag)

    close_date = _clean_text(record.get("close_date"))
    if close_date:
        try:
            close_dt = datetime.strptime(close_date, "%Y-%m-%d").date()
            if close_dt < date.today():
                tags.append("status:closed")
        except ValueError:
            pass

    return sorted(set(tags)) or ["research_domain:unknown"]


def extract_foa_fields(url: str) -> dict[str, Any]:
    source = _detect_source(url)
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")
    lines = _extract_visible_lines(soup)

    title = _extract_title(soup, lines)
    agency = _find_label_value(lines, ["Agency", "Federal Agency", "Funding Agency"])
    assistance_listing = _extract_assistance_listing(lines)
    opportunity_number = _find_label_value(
        lines,
        ["Funding opportunity number", "Opportunity Number", "FOA Number"],
    )
    open_date = _parse_date(_find_label_value(lines, ["Posted date", "Release Date"]))
    close_date = _parse_date(
        _find_label_value(lines, ["Closing", "Closing Date", "Close Date", "Deadline"])
    )
    funding_instrument = _extract_funding_instrument(lines)
    eligibility_text = _extract_eligibility(lines)
    program_description = _extract_description(lines)

    if not program_description:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            program_description = _clean_text(meta_desc.get("content"))

    award_min, award_max = _extract_award_values(lines)
    award_range = _build_award_range(award_min, award_max)
    foa_id = _generate_foa_id(
        source=source, source_url=url, opportunity_number=opportunity_number
    )
    synopsis = _summary_from_description(program_description)

    record: dict[str, Any] = {
        "foa_id": foa_id,
        "title": title,
        "agency": agency,
        "open_date": open_date,
        "close_date": close_date,
        "eligibility_text": eligibility_text,
        "program_description": program_description,
        "award_min": award_min,
        "award_max": award_max,
        "award_range": award_range,
        "opportunity_number": opportunity_number,
        "assistance_listing": assistance_listing,
        "funding_instrument": funding_instrument,
        "source": source,
        "source_url": url,
        "synopsis": synopsis,
        "tags": [],
    }

    record["tags"] = rule_based_tags(record)
    return record


def write_outputs(record: dict[str, Any], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)

    json_path = os.path.join(out_dir, "foa.json")
    csv_path = os.path.join(out_dir, "foa.csv")

    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, ensure_ascii=False)

    csv_record = dict(record)
    tags = csv_record.get("tags")
    if isinstance(tags, list):
        csv_record["tags"] = ";".join(tags)

    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCHEMA_FIELDS)
        writer.writeheader()
        writer.writerow({field: csv_record.get(field) for field in SCHEMA_FIELDS})


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest one FOA URL, extract structured fields, apply rule-based tags, and write foa.json + foa.csv."
    )
    parser.add_argument(
        "--url", required=True, help="FOA page URL from Grants.gov or NSF"
    )
    parser.add_argument(
        "--out_dir",
        default="./out",
        help="Output directory to write foa.json and foa.csv",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])

    try:
        record = extract_foa_fields(args.url)
        write_outputs(record, args.out_dir)
    except Exception as exc:
        print(f"[ERROR] Failed to ingest FOA URL: {exc}", file=sys.stderr)
        return 2

    print(f"Wrote: {os.path.join(args.out_dir, 'foa.json')}")
    print(f"Wrote: {os.path.join(args.out_dir, 'foa.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
