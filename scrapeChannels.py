#!/usr/bin/env python3
"""
SiriusXM Channel Guide Scraper - FAST VERSION
Combines debug logging with JS-promise scrolling for speed.
"""

import json
import re
import time
import sys
import traceback
import requests
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def log(msg):
    ts = time.strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def setup_driver():
    log("Setting up Chrome driver...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    prefs = {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.stylesheets": 2,
        "profile.default_content_setting_values.fonts": 2,
    }
    chrome_options.add_experimental_option("prefs", prefs)
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    try:
        driver = webdriver.Chrome(options=chrome_options)
        log("Chrome driver created successfully")
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': 'Object.defineProperty(navigator, "webdriver", {get: () => undefined}); window.chrome = {runtime: {}};'
        })
        driver.implicitly_wait(5)
        return driver
    except Exception as e:
        log(f"FAILED to create driver: {e}")
        traceback.print_exc()
        sys.exit(1)


def scroll_to_bottom_fast(driver, max_wait_sec=60):
    log("Starting fast JS scroll...")
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
    log(f"Scrolled in {elapsed:.1f}s, height={result.get('height', 0)}, cards={result.get('cards', 0)}")
    return result


def extract_slug(url):
    try:
        path = urlparse(url).path.strip('/')
        return path.split('/')[-1]
    except:
        return None


def parse_card(card):
    try:
        try:
            label_elem = card.find_element(By.CSS_SELECTOR, ".cg-channel-label")
        except:
            return None

        label_text = label_elem.text if label_elem else ""
        match = re.search(r'CH\s*(\d+)', label_text, re.I)
        ch_num = int(match.group(1)) if match else None

        title_link = card.find_element(By.CSS_SELECTOR, ".cg-channel-title")
        name = title_link.text.strip()
        url = title_link.get_attribute("href") or ""
        slug = extract_slug(url)

        if not slug:
            return None

        sub = ""
        try: sub = card.find_element(By.CSS_SELECTOR, ".cg-channel-subheadline").text.strip()
        except: pass

        desc = ""
        try: desc = card.find_element(By.CSS_SELECTOR, ".cg-channel-description").text.strip()
        except: pass

        img = ""
        try: img = card.find_element(By.CSS_SELECTOR, ".cg-image-wrapper img").get_attribute("src")
        except: pass

        deep = ""
        try: deep = card.find_element(By.CSS_SELECTOR, "a[data-player-link='true']").get_attribute("href")
        except: pass

        return {
            "channel_number": ch_num,
            "name": name,
            "slug": slug,
            "url": url,
            "subheadline": sub,
            "description": desc,
            "image": img,
            "deep_link": deep,
        }
    except:
        return None


def scrape_guide():
    log("=" * 60)
    log("STARTING FAST SCRAPER")
    log("=" * 60)

    driver = setup_driver()
    channels = []

    try:
        url = "https://www.siriusxm.ca/channel-guide/?plan=All+Access"
        log(f"Navigating to: {url}")

        driver.get(url)
        log("Page load initiated, waiting 5s...")
        time.sleep(5)

        log(f"Current URL: {driver.current_url}")
        log(f"Page title: {driver.title}")

        src = driver.page_source[:500]
        if "Access Denied" in src or "403" in src:
            log("ERROR: Access denied")
            return []
        if len(driver.page_source) < 1000:
            log("ERROR: Page source too short")
            return []

        log("Waiting for .cg-card elements...")
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".cg-card"))
            )
            log("Found .cg-card elements!")
        except Exception as e:
            log(f"WARNING: Timeout: {e}")

        initial = driver.find_elements(By.CSS_SELECTOR, ".cg-card")
        log(f"Initial card count: {len(initial)}")

        if len(initial) == 0:
            log("ERROR: No cards found. Saving debug_page.html...")
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            return []

        # FAST JS SCROLL
        scroll_to_bottom_fast(driver, max_wait_sec=60)

        # Extract
        log("Extracting channel data...")
        cards = driver.find_elements(By.CSS_SELECTOR, ".cg-card")
        log(f"Processing {len(cards)} cards...")

        for i, card in enumerate(cards):
            if (i + 1) % 100 == 0:
                log(f"  {i+1}/{len(cards)} cards, {len(channels)} valid channels")
            ch = parse_card(card)
            if ch:
                channels.append(ch)

        log(f"Extraction complete: {len(channels)} valid channels")

    except Exception as e:
        log(f"CRITICAL ERROR: {e}")
        traceback.print_exc()
    finally:
        log("Closing driver...")
        driver.quit()

    return channels


def get_channel_key(driver, slug):
    if not slug:
        return None
    url = f"https://www.siriusxm.ca/channels/{slug}/"
    try:
        driver.get(url)
        time.sleep(1.5)
        source = driver.page_source

        for pattern in [
            r"channel_keys\s*:\s*['\"](\d+)['\"]",
            r"contentId['\"]?\s*:\s*['\"](\d+)['\"]",
            r"data-channel-id=['\"]?(\d+)['\"]?",
        ]:
            match = re.search(pattern, source)
            if match:
                return match.group(1)
    except Exception as e:
        log(f"    Error getting key for {slug}: {e}")
    return None


def fetch_keys(channels):
    if not channels:
        return channels
    log(f"\nFetching numeric keys for {len(channels)} channels...")
    driver = setup_driver()
    try:
        for i, ch in enumerate(channels):
            if (i + 1) % 50 == 0:
                log(f"  {i+1}/{len(channels)}...")
            key = get_channel_key(driver, ch.get("slug"))
            if key:
                ch["channel_key"] = key
            time.sleep(0.3)
    finally:
        driver.quit()
    return channels


def test_api(channels):
    log("\nTesting against MountainWrapper API...")
    ids_to_test = []
    for ch in channels:
        if ch.get("channel_key"):
            ids_to_test.append(ch["channel_key"])
        elif ch.get("slug"):
            ids_to_test.append(ch["slug"])

    log(f"Testing {len(ids_to_test)} IDs...")
    working = 0
    batch_size = 10

    for i in range(0, len(ids_to_test), batch_size):
        batch = ids_to_test[i:i + batch_size]
        ids_param = ",".join(str(x) for x in batch)
        url = f"https://www.siriusxm.com/servlet/Satellite?pagename=SXM/Services/MountainWrapper&channels={ids_param}"
        try:
            resp = requests.get(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                for cid in batch:
                    cid_str = str(cid)
                    if data.get("channels") and cid_str in data["channels"]:
                        for ch in channels:
                            if ch.get("channel_key") == cid_str or ch.get("slug") == cid_str:
                                ch["api_data"] = data["channels"][cid_str]
                                ch["mountain_wrapper_id"] = cid_str
                                working += 1
                                break
        except Exception as e:
            log(f"  API batch error: {e}")
        time.sleep(0.2)

    log(f"API working: {working}/{len(channels)}")
    return channels


def main():
    channels = scrape_guide()
    if not channels:
        log("No channels scraped. Exiting.")
        sys.exit(1)

    channels = fetch_keys(channels)
    channels = test_api(channels)

    output = {
        "metadata": {
            "total": len(channels),
            "with_api": sum(1 for c in channels if c.get("api_data")),
            "with_key": sum(1 for c in channels if c.get("channel_key")),
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        },
        "channels": channels
    }

    with open("siriusxm_channels.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    log(f"\nSaved {len(channels)} channels to siriusxm_channels.json")
    print("\n--- Sample ---")
    for c in channels[:5]:
        print(f"  CH{c.get('channel_number', '?'):>4} | {c['name']:<30} | {c.get('channel_key', 'N/A'):<6} | {c.get('slug', 'N/A')}")


if __name__ == "__main__":
    main()
