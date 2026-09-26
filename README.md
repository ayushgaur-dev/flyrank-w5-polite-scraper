# The Polite Scraper — FlyRank W5 A9 (Python lane)

Small, polite scraping pipeline: downloads the first **3 catalogue pages** of
[Books to Scrape](https://books.toscrape.com), visits all **60 book pages**,
turns messy HTML into clean, schema-checked JSON — without crashing on a broken
page, and with an honest report at the end of every run.

## Target classification (Stage 0)

- **Which site:** `books.toscrape.com` (catalogue) + `toscrape.com` (parent page describing the sandbox).
- **Why:** `toscrape.com` says Books is *"a fictional bookstore that desperately
  wants to be scraped. It's a safe place for beginners learning web scraping"*,
  and the catalogue header says *"We love being scraped!"*. That is explicit
  permission — a practice sandbox is the only kind of target this assignment touches.
- **How much:** exactly the first 3 catalogue pages (`page-1/2/3.html`) → 60 books. Nothing else.
- **What data:** public book facts only — title, price, availability, star rating,
  product description — plus provenance (`source_page`, `fetched_at`).
- **Why appropriate:** tiny scope, demo data with *"no real meaning"* (their words),
  no login/paywall/personal data, polite fetching (see below).
- **Robots check:** `GET https://books.toscrape.com/robots.txt` → **HTTP 404,
  no robots file found** (a missing file is not permission, just a missing file —
  permission here comes from the sandbox statements above).

I will not reuse this code on another site without checking its rules and terms first.

## Quick start (a stranger can run this in < 5 minutes)

```bash
cd scraper
pip install -r requirements.txt
python src/main.py
```

That's it. Output lands in `output/`:

| File | What |
|---|---|
| `output/books.json` | 60 validated records |
| `output/errors.json` | records that failed validation, with reasons (empty on a clean run) |
| `output/run-report.json` | honest numbers for the run |

Prove failure survival (one deliberately fake URL, broken on our side only):

```bash
python src/main.py --include-bad-url
# run still finishes, books.json still has the 60 good records, report shows failed_pages: 1
```

Second runs are served from `cache/` (gitignored) — no repeat hammering while developing.

## Lane & install

- **Lane:** Python 3.10+ · `requests` (HTTP) · `beautifulsoup4` (parse) · `pydantic` (schema).
- Install: `pip install -r requirements.txt` (no DB, proxy, cloud, or credit card needed).

## Record schema

Raw extraction keeps 8 fields; normalization adds `price_gbp`. Validated with Pydantic
before anything is stored — failures go to `errors.json`, never `books.json`:

```json
{
  "title": "A Light in the Attic",
  "product_url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
  "price_text": "£51.77",
  "price_gbp": 51.77,
  "availability_text": "In stock (22 available)",
  "rating_text": "Three",
  "description": "... (null when the page has none — never invented)",
  "source_page": "https://books.toscrape.com/catalogue/page-1.html",
  "fetched_at": "2026-09-26T18:29:57Z"
}
```

Rules: `title/price_text/availability_text/rating_text` non-empty; both URLs must be
absolute `https://`; `rating_text` ∈ One…Five; `description` optional; `price_gbp`
is a real number parsed from `price_text`; `product_url` is the canonical identity
(duplicates collapse to one; reruns overwrite, never append → idempotent).

## Politeness rules

- Honest `User-Agent`: `FlyRankInternship-A9/1.0 (+<your-repo-url>)`.
- `timeout=10`s on every request — never wait forever; status checked, only 200 is parsed.
- ≥ 0.6 s delay between **real** requests (`POLITE_DELAY`); cached reads never touch the network.
- Cache-first: `cache/catalogue-page-N.html` + `cache/books/*.html`; restart 50×, the site feels it once.
- Follow the site's own `li.next` links (pages 1→3, then stop); relative links resolved
  with `urljoin`, never string gluing; duplicates removed.
- Retry **once** on timeout/connection-error/5xx; **never** retry 404 (doesn't exist)
  or 403 (the site said no); each page handled separately so one bad page is logged
  and skipped.

## Proof — real `run-report.json`

```json
{
  "start_time": "2026-09-26T18:29:57.244848Z",
  "end_time": "2026-09-26T18:30:00.087016Z",
  "duration_seconds": 2.84,
  "catalogue_pages": 3,
  "discovered": 60,
  "unique_urls": 60,
  "detail_pages_attempted": 60,
  "pages_fetched": 0,
  "cache_hits": 63,
  "valid_records": 60,
  "invalid_records": 0,
  "failed_pages": 0,
  "failed_urls": [],
  "robots_note": "no robots file found (HTTP 404 on https://books.toscrape.com/robots.txt)",
  "user_agent": "FlyRankInternship-A9/1.0 (+https://github.com/your-username/flyrank-w5-a9)"
}
```

(First run: `pages_fetched: 63`; reruns serve from cache. With `--include-bad-url`:
`valid_records: 60`, `failed_pages: 1`.)

This assignment needed no browser: the data is already in the HTML the server sends,
so a browser would only add cost.

## Ethics note (in my own words)

Scrape only what explicitly welcomes it. Prefer an official API when one exists;
never bypass logins, paywalls, or blocks; collect only what you need; identify
yourself and go slowly. This code is scoped to a practice sandbox and must be
re-checked against any other site's rules and terms before reuse.

## Limitation

Single-threaded and sequential by design (politeness over speed) — a full cold run
takes ~2–3 minutes; there is no concurrency cap to tune because there is no concurrency.
