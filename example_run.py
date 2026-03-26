import json
from pathlib import Path

from main import extract_foa_fields, write_outputs

SAMPLE_URL = (
    "https://simpler.grants.gov/opportunity/77242ec4-56ad-4784-84ca-066b30d01fae"
)
OUT_DIR = Path("./out")


def main() -> None:
    record = extract_foa_fields(SAMPLE_URL)
    write_outputs(record, str(OUT_DIR))

    print("Showcase run complete.")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    print(f"Wrote {OUT_DIR / 'foa.json'}")
    print(f"Wrote {OUT_DIR / 'foa.csv'}")


if __name__ == "__main__":
    main()
