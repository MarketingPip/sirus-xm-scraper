#!/usr/bin/env python3
"""
SiriusXM Channel Guide Scraper - HIGH PERFORMANCE VERSION

Keeps the original working scroll logic, but replaces:
- Selenium DOM parsing with BeautifulSoup (10-50x faster)
- Second Selenium session for keys with ThreadPoolExecutor + requests
- Sequential API testing with ThreadPoolExecutor + requests
"""

import argparse
import json
import logging
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

try:
    import lxml
    PARSER = "lxml"
    HAS_LXML = True
except ImportError:
    PARSER = "html.parser"
    HAS_LXML = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GUIDE_URL = "https://www.siriusxm.ca/channel-guide/?plan=All+Access&category=Music"
CHANNEL_BASE_URL = "https://www.siriusxm.ca/channels/{slug}/"
MOUNTAIN_API_URL = "https://www.siriusxm.com/servlet/Satellite"
MOUNTAIN_API_PARAMS = {"pagename": "SXM/Services/MountainWrapper"}

API_BATCH_SIZE = 10
KEY_WORKERS = 10
API_WORKERS = 20
GUIDE_SCROLL_TIMEOUT = 90

KEY_PATTERNS = [
    re.compile(r"""channel_keys\s*:\s*['"]([^'"]+)['"]""", re.I),
    re.compile(r'contentId["\']?\s*:\s*["\']?(\d+)["\']?', re.I),
    re.compile(r'data-channel-id=["\']?(\d+)["\']?', re.I),
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("siriusxm")


# ---------------------------------------------------------------------------
# HTTP Session factory
# ---------------------------------------------------------------------------
def create_session(
    pool_size: int = 20,
    retries: int = 3,
    backoff: float = 0.5,
) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    })

    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=pool_size,
        pool_maxsize=pool_size * 2,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------
def extract_slug(url: str) -> Optional[str]:
    try:
        return urlparse(url).path.strip("/").split("/")[-1]
    except Exception:
        return None


def parse_guide_html(html: str) -> List[Dict]:
    """Parse the guide page with BeautifulSoup."""
    soup = BeautifulSoup(html, PARSER)
    cards = soup.find_all(class_="cg-card")
    channels: List[Dict] = []

    for card in cards:
        try:
            label_elem = card.find(class_="cg-channel-label")
            if not label_elem:
                continue
            m = re.search(r"CH\s*(\d+)", label_elem.get_text(strip=True), re.I)
            ch_num = int(m.group(1)) if m else None

            title_link = card.find(class_="cg-channel-title")
            if not title_link or title_link.name != "a":
                continue
            name = title_link.get_text(strip=True)
            url = title_link.get("href", "")
            slug = extract_slug(url)
            if not slug:
                continue

            sub = ""
            sub_elem = card.find(class_="cg-channel-subheadline")
            if sub_elem:
                sub = sub_elem.get_text(strip=True)

            desc = ""
            desc_elem = card.find(class_="cg-channel-description")
            if desc_elem:
                desc = desc_elem.get_text(strip=True)

            img = ""
            img_wrap = card.find(class_="cg-image-wrapper")
            if img_wrap:
                img_tag = img_wrap.find("img")
                if img_tag:
                    img = img_tag.get("src", "")

            deep = ""
            deep_link = card.find("a", attrs={"data-player-link": "true"})
            if deep_link:
                deep = deep_link.get("href", "")

            channels.append({
                "channel_number": ch_num,
                "name": name,
                "slug": slug,
                "url": url,
                "subheadline": sub,
                "description": desc,
                "image": img,
                "deep_link": deep,
            })
        except Exception:
            continue

    return channels


# ---------------------------------------------------------------------------
# Phase 1 - Guide scraping (USER'S WORKING SCROLL LOGIC)
# ---------------------------------------------------------------------------
def setup_driver() -> webdriver.Chrome:
    """Configure a lean headless Chrome instance."""
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-plugins")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.stylesheets": 2,
        "profile.default_content_setting_values.fonts": 2,
    }
    opts.add_experimental_option("prefs", prefs)
    opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    opts.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": (
                'Object.defineProperty(navigator, "webdriver", {get: () => undefined});'
                'window.chrome = {runtime: {}};'
            )
        },
    )
    driver.implicitly_wait(5)
    return driver


def scroll_to_bottom_fast(driver, max_wait_sec=60, logger=None):
    """
    THE USER'S ORIGINAL SCROLL LOGIC - IT WORKS.
    Scrolls to document.body.scrollHeight and waits for lazy-load.
    """
    if logger:
        logger.info("Starting fast JS scroll...")
    start = time.time()

    result = driver.execute_script("""
        return new Promise((resolve) => {
            let lastH = 0;
            let stable = 0;
            const t0 = Date.now();

            const tick = () => {
                const h = document.body.scrollHeight;
                window.scrollTo(0, h);

                setTimeout(() => {
                    const newH = document.body.scrollHeight;
                    const elapsed = Date.now() - t0;
                    const cards = document.querySelectorAll('.cg-card').length;

                    if (newH === h) {
                        stable++;
                        if (stable >= 2) {
                            resolve({done: true, height: newH, ms: elapsed, cards: cards});
                            return;
                        }
                    } else {
                        stable = 0;
                    }

                    if (elapsed > """ + str(max_wait_sec * 1000) + """) {
                        resolve({done: false, height: newH, ms: elapsed, cards: cards});
                        return;
                    }

                    tick();
                }, 400);
            };

            tick();
        });
    """)

    elapsed = time.time() - start
    if logger:
        logger.info(
            f"Scrolled in {elapsed:.1f}s, height={result.get('height', 0)}, cards={result.get('cards', 0)}"
        )
    return result


def scrape_guide_selenium(logger: logging.Logger) -> List[Dict]:
    """Load the guide in Chrome, scroll to bottom, then parse with BS4."""
    logger.info("Using Selenium for guide scraping")
    driver = setup_driver()
    try:
        driver.get(GUIDE_URL)
        logger.info(f"Navigated to: {driver.current_url}")
        logger.info("Page load initiated, waiting 5s...")
        time.sleep(5)

        logger.info(f"Current URL: {driver.current_url}")
        logger.info(f"Page title: {driver.title}")

        src = driver.page_source[:500]
        if "Access Denied" in src or "403" in src:
            logger.error("ERROR: Access denied")
            return []
        if len(driver.page_source) < 1000:
            logger.error("ERROR: Page source too short")
            return []

        logger.info("Waiting for .cg-card elements...")
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".cg-card"))
            )
            logger.info("Found .cg-card elements!")
        except Exception as e:
            logger.warning(f"WARNING: Timeout: {e}")

        initial = driver.find_elements(By.CSS_SELECTOR, ".cg-card")
        logger.info(f"Initial card count: {len(initial)}")

        if len(initial) == 0:
            logger.error("ERROR: No cards found. Saving debug_page.html...")
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            return []

        # FAST JS SCROLL - USER'S ORIGINAL LOGIC
        scroll_to_bottom_fast(driver, max_wait_sec=60, logger=logger)

        # Extract: dump page_source ONCE, parse with BeautifulSoup
        logger.info("Extracting channel data with BeautifulSoup...")
        channels = parse_guide_html(driver.page_source)
        logger.info(f"Extracted {len(channels)} channels")
        return channels

    except Exception:
        logger.error("Selenium scrape failed")
        traceback.print_exc()
        return []
    finally:
        logger.info("Closing driver...")
        driver.quit()


def scrape_guide(logger: logging.Logger) -> List[Dict]:
    """Always use Selenium for the guide (requests returns 0 cards)."""
    if not HAS_SELENIUM:
        logger.error("Selenium is required for guide scraping.")
        sys.exit(1)
    return scrape_guide_selenium(logger)


# ---------------------------------------------------------------------------
# Phase 2 - Channel keys (parallel requests, NO Selenium)
# ---------------------------------------------------------------------------
def fetch_single_key(
    session: requests.Session,
    slug: str,
    logger: logging.Logger,
) -> Optional[str]:
    if not slug:
        return None
    url = CHANNEL_BASE_URL.format(slug=slug)
    try:
        resp = session.get(url, timeout=12)
        if resp.status_code != 200:
            return None
        text = resp.text
        for pat in KEY_PATTERNS:
            m = pat.search(text)
            if m:
                return m.group(1)
    except Exception as exc:
        logger.debug(f"Key fetch error for {slug}: {exc}")
    return None


def fetch_keys(channels: List[Dict], logger: logging.Logger) -> List[Dict]:
    if not channels:
        return channels

    total = len(channels)
    logger.info(f"Fetching keys for {total} channels ({KEY_WORKERS} workers)...")

    # Deduplicate slugs
    slug_to_indices: Dict[str, List[int]] = {}
    for i, ch in enumerate(channels):
        slug = ch.get("slug")
        if slug:
            slug_to_indices.setdefault(slug, []).append(i)

    unique_slugs = list(slug_to_indices.keys())
    session = create_session(pool_size=KEY_WORKERS)
    found: Dict[str, str] = {}

    iterable = unique_slugs
    if HAS_TQDM:
        iterable = tqdm(unique_slugs, desc="Keys", unit="ch", ncols=80)

    with ThreadPoolExecutor(max_workers=KEY_WORKERS) as ex:
        future_to_slug = {
            ex.submit(fetch_single_key, session, slug, logger): slug
            for slug in unique_slugs
        }
        for future in as_completed(future_to_slug):
            slug = future_to_slug[future]
            try:
                key = future.result()
                if key:
                    found[slug] = key
            except Exception as exc:
                logger.debug(f"Exception fetching key for {slug}: {exc}")

    for slug, key in found.items():
        for idx in slug_to_indices.get(slug, []):
            channels[idx]["channel_key"] = key

    with_key = sum(1 for c in channels if c.get("channel_key"))
    logger.info(f"Keys resolved: {with_key}/{total}")
    return channels


# ---------------------------------------------------------------------------
# Phase 3 - MountainWrapper API testing (parallel batches)
# ---------------------------------------------------------------------------
def test_api_batch(session, batch, logger):
    ids_param = ",".join(str(x) for x in batch)
    params = {**MOUNTAIN_API_PARAMS, "channels": ids_param}
    try:
        resp = session.get(MOUNTAIN_API_URL, params=params, timeout=15)
        logger.info(f"Status: {resp.status_code}, Len: {len(resp.text)}")
        if resp.status_code == 200:
            # Strip BOM if present, then parse manually (avoids resp.json()'s BOM blind spot)
            text = resp.content.decode("utf-8-sig")
            data = json.loads(text)
            ch_data = data.get("channels", {})
            logger.info(f"Batch result: {len(ch_data)} channels returned")

            # Only keep the genre field per channel
            genre_only = {}
            for cid, info in ch_data.items():
                if isinstance(info, dict):
                    genre_only[str(cid)] = {
                    "genre": info.get("genreTitle"),
                    "uuid": info.get("uuid"),
                }

            return genre_only
        else:
            logger.warning(f"API returned {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        logger.error(f"API batch error: {exc}", exc_info=True)
    return {}
    



def test_api(channels: List[Dict], logger: logging.Logger) -> List[Dict]:
    if not channels:
        return channels

    id_to_indices: Dict[str, List[int]] = {}
    for i, ch in enumerate(channels):
        cid = ch.get("channel_key") or ch.get("slug")
        if cid:
            id_to_indices.setdefault(str(cid), []).append(i)

    ids_to_test = list(id_to_indices.keys())
    total_ids = len(ids_to_test)
    logger.info(f"Testing {total_ids} IDs against MountainWrapper ({API_WORKERS} workers)...")

    batches = [
        ids_to_test[i : i + API_BATCH_SIZE]
        for i in range(0, total_ids, API_BATCH_SIZE)
    ]

    session = create_session(pool_size=API_WORKERS)
    working = 0

    with ThreadPoolExecutor(max_workers=API_WORKERS) as ex:
        future_to_batch = {
            ex.submit(test_api_batch, session, batch, logger): batch
            for batch in batches
        }
        for future in as_completed(future_to_batch):
            batch = future_to_batch[future]
            try:
                batch_result = future.result()

                # Diff: which IDs we sent got no data back?
                missing = set(batch) - set(batch_result.keys())
                if missing:
                    logger.warning(f"No API data returned for: {sorted(missing)}")

                for cid, api_data in batch_result.items():
                    for idx in id_to_indices.get(cid, []):
                        if isinstance(api_data, dict):
                            channels[idx].update(api_data)
                        channels[idx]["mountain_wrapper_id"] = cid
                        working += 1
            except Exception as exc:
                logger.debug(f"API future exception: {exc}")

    logger.info(f"API hits: {working}/{len(channels)}")
    return channels


def write_api_json(channels: List[Dict], out_path: str, logger: logging.Logger) -> None:
    """Transform final channel records into the target schema and write to disk."""
    result = []
    for ch in channels:
        slug = ch.get("slug", "") or ""
        result.append({
            "number": ch.get("channel_number"),
            "name": ch.get("name"),
            "deeplink": slug.replace("-", ""),
            "url": ch.get("url"),
            "shortDescription": ch.get("subheadline", ""),
            "longDescription": ch.get("description", ""),
            "image": ch.get("image", ""),
            "id": ch.get("uuid"),
            "genres": [ch["genre"]] if ch.get("genre") else [],
        })

    path = Path(out_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info(f"Wrote {len(result)} channels -> {path.resolve()}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="SiriusXM Channel Scraper (high-performance)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scraper.py                         # full run
  python scraper.py --skip-api              # skip MountainWrapper test
  python scraper.py --workers 5 -o out.json   # 5 workers, custom output
  python scraper.py -v                      # verbose debug logging
        """,
    )
    parser.add_argument(
        "-o", "--output", default="siriusxm_channels.json",
        help="Output JSON file (default: siriusxm_channels.json)",
    )
    parser.add_argument(
        "--skip-keys", action="store_true",
        help="Skip fetching per-channel numeric keys",
    )
    parser.add_argument(
        "--skip-api", action="store_true",
        help="Skip MountainWrapper API validation",
    )
    parser.add_argument(
        "-w", "--workers", type=int, default=0,
        help="Max thread workers (0 = auto: 10 keys, 20 API). Lower if rate-limited.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logger = setup_logging(args.verbose)

    global KEY_WORKERS, API_WORKERS
    if args.workers > 0:
        KEY_WORKERS = args.workers
        API_WORKERS = args.workers

    logger.info("=" * 60)
    logger.info("HIGH PERFORMANCE SIRIUSXM SCRAPER")
    logger.info(f"  lxml parser : {HAS_LXML}")
    logger.info(f"  tqdm        : {HAS_TQDM}")
    logger.info(f"  selenium    : {HAS_SELENIUM}")
    logger.info(f"  key workers : {KEY_WORKERS}")
    logger.info(f"  api workers : {API_WORKERS}")
    logger.info("=" * 60)

    # Phase 1 - Guide (Selenium scroll + BS4 parse)
    channels = scrape_guide(logger)

    if not channels:
        logger.error("No channels found. Exiting.")
        sys.exit(1)
    logger.info(f"Guide scrape complete: {len(channels)} channels")

    # Phase 2 - Keys (ThreadPoolExecutor + requests)
    if not args.skip_keys:
        channels = fetch_keys(channels, logger)

    # Phase 3 - API (ThreadPoolExecutor + requests)
    if not args.skip_api:
        channels = test_api(channels, logger)

    # Serialize
    output = {
        "metadata": {
            "total": len(channels),
            "with_api": sum(1 for c in channels if "mountain_wrapper_id" in c),
            "with_key": sum(1 for c in channels if c.get("channel_key")),
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "channels": channels,
    }

    out_path = Path(args.output)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    logger.info(f"Saved {len(channels)} channels -> {out_path.resolve()}")
    write_api_json(channels, "siriusxm_api_channels.json", logger)
    print("--- Sample ---")
    for c in channels[:5]:
        print(
            f"  CH{c.get('channel_number', '?'):>4} | "
            f"{c['name']:<30} | "
            f"{c.get('channel_key', 'N/A'):<6} | "
            f"{c.get('slug', 'N/A')}"
        )


if __name__ == "__main__":
    main()
