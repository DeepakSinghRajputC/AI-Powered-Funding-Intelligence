# AI-Powered Funding Intelligence (FOA Screening Task)

This folder contains a minimal Python script that ingests **one** FOA URL (Grants.gov or NSF), extracts common fields, applies deterministic rule-based tags, and writes:

- `out/foa.json`
- `out/foa.csv`

Run:

```bash
python main.py --url "https://example.com/foa-page" --out_dir ./out
```

See `readme.md` for the full explanation and the output schema.

# AI-Powered Funding Intelligence (FOA Screening Task)

This is a *minimal* Python script that takes **one** Funding Opportunity Announcement (FOA) URL (from **Grants.gov** or **NSF**), extracts a few common fields, applies **deterministic** rule-based tags, and writes:

1. `out/foa.json`
2. `out/foa.csv`

You can run it like this:

```bash
python main.py --url "https://example.com/foa-page" --out_dir ./out
```

## What’s included

- `main.py`: fetches the FOA webpage, parses HTML, extracts fields using rules/regex, tags the result deterministically, and writes outputs.
- `requirements.txt`: Python dependencies.
- `readme.md` (this file): how to run and what the script does.

## Output schema (what gets written)

The script writes a single FOA record with these keys in `out/foa.json`:

`url, source, title, opportunity_number, agency, cfda, synopsis, description, posted_date, closing_date, funding_instrument, eligibility, tags`

In `out/foa.csv`, the same columns are used, and `tags` is flattened into a single `;`-separated string.

## How extraction works (deterministically)

The script does not use ML. It follows fixed rules:

1. Downloads the FOA URL (must return HTML).
2. Parses the HTML with BeautifulSoup.
3. Tries to read:
   - `title` from `og:title` (or `h1`/`<title>` fallback)
   - `description` from the page `<meta name="description">`
   - several “labeled” fields by scanning page text for patterns like:
     - `Closing Date: ...`
     - `Agency: ...`
     - `Funding Opportunity Number: ...`
4. Attempts to convert `posted_date` / `closing_date` into ISO format (`YYYY-MM-DD`) if a known date format is found.
5. Generates `tags` using keyword rules over the extracted `title/description/synopsis` plus urgency based on `closing_date`.

## Important notes

- FOA pages vary a lot in formatting. This script uses generic heuristics, so some fields may come back as empty if the page doesn’t contain recognizable labels.
- If the provided URL does not return HTML (for example, a PDF), the script will fail with an error explaining the content-type mismatch.
