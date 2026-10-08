"""Small test runner for the future IMDb collection and cleaning pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

from clean_movies import clean_csv
from collect_imdb import collect_movies, write_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a small IMDb collect-clean test.")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--no-selenium", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "raw_test.csv"
    clean_path = output_dir / "clean_test.csv"
    records = collect_movies(
        limit=max(1, min(args.limit, 10)),
        timeout=args.timeout,
        use_selenium=not args.no_selenium,
    )
    write_csv(records, str(raw_path))
    clean_csv(raw_path, clean_path)
    print(f"Collected {len(records)} records")
    print(f"Raw output: {raw_path}")
    print(f"Clean output: {clean_path}")


if __name__ == "__main__":
    main()
