"""
The Polite Scraper — FlyRank Internship W5 A9 (Python lane)
Pipeline: classify -> fetch (cache) -> discover (3 catalogue pages)
        -> extract (60 books) -> normalize -> validate -> store -> report.

Run:
    python src/main.py
    python src/main.py --include-bad-url   # prove Stage 5: 1 fake URL is skipped + reported
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, ValidationError, field_validator
from typing import Optional

# ---------- Config ----------
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
BOOK_CACHE_DIR = CACHE_DIR / "books"
OUTPUT_DIR = BASE_DIR / "output"

BASE_URL = "https://books.toscrape.com/"
START_URL = "https://books.toscrape.com/catalogue/page-1.html"
MAX_CATALOGUE_PAGES = 3

USER_AGENT = "FlyRankInternship-A9/1.0 (+https://github.com/your-username/flyrank-w5-a9)"
TIMEOUT = 10          # seconds — never wait forever
POLITE_DELAY = 0.6    # seconds between REAL requests (>= 0.5 required)
RETRY_WAIT = 1.0      # wait before the single retry on timeout/5xx

ALLOWED_RATINGS = {"One", "Two", "Three", "Four", "Five"}

stats = {"pages_fetched": 0, "cache_hits": 0}


def log_fetch(msg: str):
    print(msg, flush=True)


def cache_path_for_catalogue(page_num: int) -> Path:
    return CACHE_DIR / f"catalogue-page-{page_num}.html"


def cache_path_for_book(product_url: str) -> Path:
    # e.g. .../a-light-in-the-attic_1000/index.html -> a-light-in-the-attic_1000-index.html
    parts = [p for p in urlparse(product_url).path.strip("/").split("/") if p]
    safe = "-".join(parts[-2:]) if len(parts) >= 2 else "-".join(parts)
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", safe)
    return BOOK_CACHE_DIR / f"{safe}.html"


def fetch_url(url: str, cache_path: Path) -> str | None:
    """Fetch with cache-first, polite headers, timeout + status check.
    Returns HTML text, or None on failed fetch (logged, never raises).
    Retries ONCE on timeout / 5xx. Never retries 404 / 403."""
    if cache_path.exists():
        stats["cache_hits"] += 1
        html = cache_path.read_text(encoding="utf-8")
        log_fetch(f"CACHE HIT {url} ({len(html)} bytes)")
        return html

    def _do_request():
        return requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )

    time.sleep(POLITE_DELAY)
    try:
        resp = _do_request()
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        log_fetch(f"TIMEOUT/CONN {url}: {e} — retrying once...")
        time.sleep(RETRY_WAIT)
        try:
            resp = _do_request()
        except Exception as e2:
            log_fetch(f"FAILED {url}: {e2}")
            return None

    # Status check — only 200 is parseable HTML
    if resp.status_code == 200:
        pass  # good
    elif resp.status_code in (404, 403):
        log_fetch(f"FAILED {url}: HTTP {resp.status_code} (no retry)")
        return None
    elif 500 <= resp.status_code < 600:
        log_fetch(f"HTTP {resp.status_code} {url} — retrying once...")
        time.sleep(RETRY_WAIT)
        try:
            resp = _do_request()
        except Exception as e:
            log_fetch(f"FAILED {url}: {e}")
            return None
        if resp.status_code != 200:
            log_fetch(f"FAILED {url}: HTTP {resp.status_code} after retry")
            return None
    else:
        log_fetch(f"FAILED {url}: HTTP {resp.status_code}")
        return None

    html = resp.content.decode("utf-8", errors="replace")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(html, encoding="utf-8")
    stats["pages_fetched"] += 1
    log_fetch(f"FETCH {url} ({len(html)} bytes)")
    return html


def check_robots_once() -> str:
    """Stage 0: request robots.txt once, write down what happened."""
    url = "https://books.toscrape.com/robots.txt"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if r.status_code == 200:
            return f"robots.txt found (HTTP 200, {len(r.text)} bytes)"
        return f"no robots file found (HTTP {r.status_code} on {url})"
    except Exception as e:
        return f"no robots file found (request failed: {e})"


# ---------- Stage 2: discovery ----------
def discover_catalogue() -> tuple[list[str], list[str], dict[str, str]]:
    """Follow the site's own 'next' links for exactly 3 pages.
    Returns (catalogue_page_urls, unique_book_urls, book_url->source_page)."""
    catalogue_urls: list[str] = []
    discovered: list[str] = []
    source_of: dict[str, str] = {}

    page_url = START_URL
    for page_num in range(1, MAX_CATALOGUE_PAGES + 1):
        html = fetch_url(page_url, cache_path_for_catalogue(page_num))
        if html is None:
            log_fetch(f"Catalogue page {page_num} failed, stopping discovery.")
            break
        catalogue_urls.append(page_url)
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("article.product_pod h3 a"):
            href = a.get("href")
            if not href:
                continue
            abs_url = urljoin(page_url, href)
            discovered.append(abs_url)
            source_of.setdefault(abs_url, page_url)
        # follow "next" link — let the site tell us the pages
        if page_num < MAX_CATALOGUE_PAGES:
            next_a = soup.select_one("li.next a")
            if not next_a or not next_a.get("href"):
                break
            page_url = urljoin(page_url, next_a["href"])

    # dedupe, keep order
    seen, unique = set(), []
    for u in discovered:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return catalogue_urls, unique, source_of


# ---------- Stage 3: extraction ----------
def parse_book_page(html: str, product_url: str, source_page: str) -> dict:
    """Selectors aimed at the product area, not the whole document."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("div.product_main") or soup

    title_el = main.select_one("h1")
    title = title_el.get_text(strip=True) if title_el else None

    price_el = soup.select_one("div.product_main p.price_color") or soup.select_one("p.price_color")
    price_text = price_el.get_text(strip=True) if price_el else None

    avail_el = soup.select_one("div.product_main p.availability") or soup.select_one("p.availability")
    availability_text = " ".join(avail_el.get_text(" ", strip=True).split()) if avail_el else None

    rating_text = None
    rating_el = soup.select_one("p.star-rating")
    if rating_el:
        classes = rating_el.get("class", [])
        rating_text = next((c for c in classes if c != "star-rating"), None)

    description = None
    desc_head = soup.find("div", id="product_description")
    if desc_head is not None:
        sib = desc_head.find_next_sibling("p")
        if sib:
            description = sib.get_text(strip=True) or None
    # spec: missing description -> null, never invented

    return {
        "title": title,
        "product_url": product_url,
        "price_text": price_text,
        "availability_text": availability_text,
        "rating_text": rating_text,
        "description": description,
        "source_page": source_page,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


# ---------- Stage 4: schema ----------
class BookRecord(BaseModel):
    title: str
    product_url: str
    price_text: str
    price_gbp: float
    availability_text: str
    rating_text: str
    description: Optional[str] = None
    source_page: str
    fetched_at: str

    @field_validator("title", "price_text", "availability_text", "rating_text")
    @classmethod
    def non_empty(cls, v):
        if not isinstance(v, str) or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @field_validator("product_url", "source_page")
    @classmethod
    def must_be_https(cls, v):
        if not isinstance(v, str) or not v.startswith("https://"):
            raise ValueError("must be an absolute https:// URL")
        return v

    @field_validator("rating_text")
    @classmethod
    def valid_rating(cls, v):
        if v not in ALLOWED_RATINGS:
            raise ValueError(f"rating must be one of {sorted(ALLOWED_RATINGS)}")
        return v


def normalize_price(price_text: str) -> float:
    """'£51.77' -> 51.77. Tolerant of mojibake ('Â£51.77') and commas.
    Raises ValueError on garbage."""
    cleaned = re.sub(r"[^0-9.]", "", price_text or "")
    if not cleaned:
        raise ValueError(f"no numeric value in {price_text!r}")
    return float(cleaned)


# ---------- Main pipeline ----------
def main() -> int:
    ap = argparse.ArgumentParser(description="Polite scraper: 3 catalogue pages -> 60 books -> JSON + report")
    ap.add_argument("--include-bad-url", action="store_true",
                    help="inject one fake book URL to prove a bad page is skipped + reported")
    args = ap.parse_args()

    start = datetime.now(timezone.utc)
    t0 = time.time()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    BOOK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    robots_note = check_robots_once()
    print(f"robots check: {robots_note}")

    catalogue_urls, book_urls, source_of = discover_catalogue()
    print(f"catalogue_pages={len(catalogue_urls)}, discovered={len(book_urls)}, unique_urls={len(set(book_urls))}")

    if args.include_bad_url:
        fake = "https://books.toscrape.com/catalogue/this-book-does-not-exist_0000/index.html"
        if fake not in book_urls:
            book_urls.append(fake)
            source_of[fake] = catalogue_urls[0] if catalogue_urls else START_URL
        print(f"injected 1 fake URL for failure test")

    raw_records: list[dict] = []
    failed_urls: list[str] = []

    for url in book_urls:
        html = fetch_url(url, cache_path_for_book(url))
        if html is None:
            failed_urls.append(url)
            continue
        try:
            rec = parse_book_page(html, product_url=url, source_page=source_of.get(url, ""))
        except Exception as e:
            print(f"PARSE FAILED {url}: {e}")
            failed_urls.append(url)
            continue
        raw_records.append(rec)

    print(f"detail_pages={len(raw_records)}")
    if raw_records:
        print("sample raw record:")
        print(json.dumps(raw_records[0], indent=2, ensure_ascii=False))

    # normalize + validate (canonical URL = product_url, dedupe)
    valid: dict[str, dict] = {}
    errors: list[dict] = []
    for rec in raw_records:
        url = rec.get("product_url", "")
        if url in valid:
            continue  # same book twice counts once
        try:
            price_gbp = normalize_price(rec.get("price_text") or "")
        except Exception as e:
            errors.append({"product_url": url, "reason": f"bad price_text {rec.get('price_text')!r}: {e}"})
            continue
        candidate = dict(rec)
        candidate["price_gbp"] = price_gbp
        try:
            book = BookRecord(**candidate)
        except ValidationError as e:
            errors.append({"product_url": url, "reason": str(e.errors())})
            continue
        valid[url] = book.model_dump()

    books = [valid[u] for u in sorted(valid)]  # stable, idempotent order
    (OUTPUT_DIR / "books.json").write_text(json.dumps(books, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUTPUT_DIR / "errors.json").write_text(json.dumps(errors, indent=2, ensure_ascii=False), encoding="utf-8")

    end = datetime.now(timezone.utc)
    report = {
        "start_time": start.isoformat().replace("+00:00", "Z"),
        "end_time": end.isoformat().replace("+00:00", "Z"),
        "duration_seconds": round(time.time() - t0, 2),
        "catalogue_pages": len(catalogue_urls),
        "discovered": len(book_urls),
        "unique_urls": len(set(book_urls)),
        "detail_pages_attempted": len(book_urls),
        "pages_fetched": stats["pages_fetched"],
        "cache_hits": stats["cache_hits"],
        "valid_records": len(books),
        "invalid_records": len(errors),
        "failed_pages": len(failed_urls),
        "failed_urls": failed_urls,
        "robots_note": robots_note,
        "user_agent": USER_AGENT,
    }
    (OUTPUT_DIR / "run-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"valid={len(books)}, invalid={len(errors)}, failed_pages={len(failed_urls)}")
    print(f"wrote output/books.json, output/errors.json, output/run-report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
