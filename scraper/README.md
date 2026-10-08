# IMDb Scraper Pipeline

This directory contains the isolated IMDb collection and cleaning layer for Popcorn Picks. It does not modify the existing Flask API, frontend, SQLite database, IMDb archive, analytics, or deployment configuration.

## Collection roles

- `requests` discovers IMDb title URLs and retrieves IMDb HTML with a timeout, user agent, and per-page error handling.
- `selenium` opens the same small set of IMDb title pages in a normal browser and extracts browser-rendered HTML after an explicit wait.
- `beautifulsoup4` parses JSON-LD and metadata from the returned HTML.
- `pandas` cleans, validates, deduplicates, and writes the collected records.

The scraper uses IMDb pages only. It does not use ScrapingBee, another proxy, an external movie API, or the existing IMDb TSV archive.

## Test command

From the project root, after installing the project requirements:

```powershell
python scraper/run_pipeline.py --limit 3
```

The small test writes:

- `data/raw_test.csv`
- `data/clean_test.csv`

The test is intentionally capped at ten records by the command-line runner. Do not run a large collection until selectors, permission, rate, and data-quality behavior have been reviewed.

## Expected input and output

The default discovery source is IMDb's top chart page. Specific title URLs can be supplied with repeated `--url` arguments. The CSV uses pipe-delimited values for `genres` and `cast`; missing fields remain empty.

The final production output will eventually be `data/movies.csv`, but this test stage writes only test files.

## Limitations and errors

IMDb may return incomplete metadata, block requests, change markup, or require browser behavior for some fields. Requests and Selenium failures are logged per page and do not discard successful records. Selenium requires a locally available compatible browser; Selenium Manager may need normal driver/browser access. The code does not bypass CAPTCHAs, authentication, rate limits, or robots/access restrictions.
