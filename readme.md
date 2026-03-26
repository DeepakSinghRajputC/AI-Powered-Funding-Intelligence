# AI-Powered Funding Intelligence — FOA Ingestion + Semantic Tagging

A minimal, fully reproducible Python pipeline that ingests a single Funding
Opportunity Announcement (FOA) URL from **Grants.gov** or **NSF**, extracts
structured fields into a defined schema, applies deterministic rule-based
semantic tags, and writes clean outputs ready for downstream grant matching.

---

## Quick Start

```bash
# 1. Clone the repo and enter the project directory
git clone <repo-url>
cd AI-Powered-Funding-Intelligence

# 2. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
.venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run against any FOA URL
python main.py --url "https://simpler.grants.gov/opportunity/77242ec4-56ad-4784-84ca-066b30d01fae" --out_dir ./out
```

Outputs are written to `./out/foa.json` and `./out/foa.csv`.

---

## Showcase Example

A ready-to-run showcase script is provided that uses the sample Grants.gov
opportunity (Air Force Defense Research Sciences Conference and Workshop Support):

```bash
python example_run.py
```

This fetches the live page, extracts all fields, prints the full JSON record to
stdout, and writes both output files to `./out/`.

---

## CLI Reference

```
python main.py --url "<FOA page URL>" [--out_dir <directory>]
```

| Argument    | Required | Default  | Description                                 |
|-------------|----------|----------|---------------------------------------------|
| `--url`     | Yes      | —        | Full URL of the FOA page (Grants.gov or NSF)|
| `--out_dir` | No       | `./out`  | Directory to write `foa.json` and `foa.csv` |

Exit codes: `0` = success, `2` = fatal error (printed to stderr).

---

## Output Schema

Both `foa.json` and `foa.csv` use the same 17-field schema.  
In the CSV, the `tags` list is serialised as a `;`-separated string.

| Field                | Type            | Description                                                  |
|----------------------|-----------------|--------------------------------------------------------------|
| `foa_id`             | string          | Stable ID: slugified opportunity number, or URL hash fallback|
| `title`              | string          | FOA title (from `<h1>` or `og:title`, prefix-stripped)       |
| `agency`             | string \| null  | Sponsoring agency name                                       |
| `open_date`          | ISO date \| null| Posted / release date (`YYYY-MM-DD`)                         |
| `close_date`         | ISO date \| null| Application deadline (`YYYY-MM-DD`)                          |
| `eligibility_text`   | string \| null  | Full eligibility section text                                |
| `program_description`| string \| null  | Full description section text                                |
| `synopsis`           | string \| null  | Auto-generated 1–2 sentence summary of the description       |
| `award_min`          | integer \| null | Minimum award amount in USD                                  |
| `award_max`          | integer \| null | Maximum award amount in USD                                  |
| `award_range`        | string \| null  | Human-readable award range, e.g. `$1 - $1,000,000`           |
| `opportunity_number` | string \| null  | Funding opportunity / FOA number (e.g. `BAA-AFRL-AFOSR-...`) |
| `assistance_listing` | string \| null  | CFDA / Assistance Listing code and program name              |
| `funding_instrument` | string \| null  | Instrument types, e.g. `Cooperative agreement; Grant`        |
| `source`             | string          | Detected source: `grants.gov` or `nsf.gov` or `unknown`      |
| `source_url`         | string          | Original URL passed to the script                            |
| `tags`               | list[string]    | Sorted, deduplicated semantic tags (see Tagging section)      |

### Sample `foa.json`

```json
{
  "foa_id": "grants_gov-baa_afrl_afosr_2016_0008",
  "title": "Air Force Defense Research Sciences Conference and Workshop Support",
  "agency": "Air Force Office of Scientific Research",
  "open_date": "2016-07-22",
  "close_date": "2026-03-26",
  "eligibility_text": "Eligible applicants include nonprofit organizations and U.S. institutions of higher education ...",
  "program_description": "The Air Force Office of Scientific Research manages the basic research investment for the U.S. Air Force ...",
  "synopsis": "Broad Agency Announcement FA955025S0001 and BAA-AFRL-AFOSR-2016-0008 are closed as of 23 March 2026 ...",
  "award_min": 1,
  "award_max": 1000000,
  "award_range": "$1 - $1,000,000",
  "opportunity_number": "BAA-AFRL-AFOSR-2016-0008",
  "assistance_listing": "12.800 -- Air Force Defense Research Sciences Program",
  "funding_instrument": "Cooperative agreement; Grant",
  "source": "grants.gov",
  "source_url": "https://simpler.grants.gov/opportunity/77242ec4-56ad-4784-84ca-066b30d01fae",
  "tags": [
    "method:conference_workshop",
    "population:higher_education",
    "population:nonprofit",
    "research_domain:defense",
    "research_domain:science_engineering",
    "source:grants_gov",
    "sponsor_theme:basic_research",
    "sponsor_theme:research_exchange"
  ]
}
```

---

## Semantic Tagging

Tags are **deterministic and rule-based** — no ML, no randomness, stable across
runs. The same input always produces the same tags.

### Tag namespaces

| Namespace           | Example tags                                           |
|---------------------|--------------------------------------------------------|
| `source:`           | `source:grants_gov`, `source:nsf_gov`                  |
| `research_domain:`  | `research_domain:defense`, `research_domain:science_engineering` |
| `method:`           | `method:conference_workshop`                           |
| `population:`       | `population:higher_education`, `population:nonprofit`  |
| `sponsor_theme:`    | `sponsor_theme:basic_research`, `sponsor_theme:research_exchange` |
| `status:`           | `status:closed`                                        |

### How tagging works

1. The `title`, `agency`, `program_description`, and `eligibility_text` fields
   are lowercased and concatenated into a single text blob.
2. A keyword map is scanned over that blob. Each keyword group maps to one tag.
3. Source is detected from the URL and added as a `source:` tag.
4. If the `close_date` is in the past, `status:closed` is appended.
5. Tags are sorted alphabetically and deduplicated before output.

To extend the taxonomy, edit the `keyword_map` dictionary inside
`rule_based_tags()` in `main.py`.

---

## Extraction Pipeline

```
URL
 │
 ▼
_fetch_html()          — requests GET with Accept: text/html
 │
 ▼
BeautifulSoup          — parse HTML tree
 │
 ▼
_extract_visible_lines() — flatten to clean text lines
 │
 ├─ _extract_title()          — prefers <h1>, strips "Opportunity Listing -" prefix
 ├─ _find_label_value()       — exact label → next-line or inline colon value
 ├─ _extract_section_text()   — captures multi-line section body between known headings
 ├─ _extract_assistance_listing() — joins number + program name across split lines
 ├─ _extract_funding_instrument() — collects multi-value instruments (e.g. grant + coop)
 ├─ _extract_award_values()   — handles value-before-label layout on simpler.grants.gov
 └─ _parse_date()             — normalises US & ISO date strings to YYYY-MM-DD
 │
 ▼
rule_based_tags()      — keyword + source + status tags
 │
 ▼
write_outputs()        — foa.json  +  foa.csv
```

---

## Supported Sources

| Source                  | URL pattern                                      | Notes                          |
|-------------------------|--------------------------------------------------|--------------------------------|
| Grants.gov (Simpler UI) | `simpler.grants.gov/opportunity/...`             | Fully tested                   |
| Grants.gov (Classic)    | `grants.gov/search-results-detail/...`           | Generic extractor applies      |
| NSF                     | `nsf.gov/funding/...`                            | Generic extractor applies      |

> **Note:** HTML structure varies across agencies and page versions. The
> extractor uses heuristics that work well for simpler.grants.gov. Other sources
> may return partial fields — this is expected behaviour for a minimal pipeline.

---

## File Structure

```
AI-Powered-Funding-Intelligence/
├── main.py            # Core pipeline: fetch → extract → tag → export
├── example_run.py     # One-click showcase using the sample Grants.gov URL
├── requirements.txt   # Python dependencies
├── README.md          # This file
└── out/
    ├── foa.json       # Most recent JSON output
    └── foa.csv        # Most recent CSV output
```

---

## Dependencies

```
requests>=2.32.0
beautifulsoup4>=4.12.0
```

No ML libraries, no API keys, no paid services required. Pure Python standard
library plus two lightweight packages.

---

## Design Notes

- **No ML required for the screening task.** Tags are produced by a keyword map
  and simple string matching — fast, transparent, and reproducible.
- **`foa_id` is stable.** When an `opportunity_number` is present it is used
  directly (slugified). Otherwise a SHA-1 hash of the URL is used. The same FOA
  URL will always generate the same `foa_id`.
- **Dates are always ISO 8601.** All date parsing normalises to `YYYY-MM-DD`
  regardless of the source format (US slash, long month name, ISO, etc.).
- **Awards handled gracefully.** On `simpler.grants.gov` the dollar values
  appear *before* their labels in the DOM text stream. The extractor accounts
  for this layout explicitly while falling back to after-label for other sources.
- **CSV tags are `;`-separated** so the field does not break standard CSV
  parsers and is easy to `SPLIT` in Excel or pandas.

---

## Roadmap / Stretch Goals

- [ ] Additional source modules (NIH, DARPA, DOE)
- [ ] PDF FOA ingestion via `pdfminer` / `pypdf`
- [ ] Embedding-based similarity tagging with `sentence-transformers`
- [ ] Vector index (FAISS / Chroma) for semantic FOA search
- [ ] Lightweight CLI or Streamlit search interface
- [ ] Evaluation dataset with precision / recall metrics for tag accuracy