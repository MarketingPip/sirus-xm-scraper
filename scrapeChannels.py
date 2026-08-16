#!/usr/bin/env python3
"""
SiriusXM Channel Guide Scraper - DEBUG VERSION
Maximum logging to diagnose silent failures.
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
    """Print with timestamp."""
    ts = time.strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def setup_driver():
    """Configure Chrome with maximum compatibility."""
    log("Setting up Chrome driver...")
    
    chrome_options = Options()
    
    # Essential headless flags
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-plugins")
    chrome_options.add_argument("--disable-images")  # Speed up loading
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.0")
    
    # Disable automation flags
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    # Logging prefs
    chrome_options.set_capability('goog:loggingPrefs', {'browser': 'ALL', 'driver': 'ALL'})
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        log("Chrome driver created successfully")
        
        # Mask webdriver
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': '''
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            '''
        })
        
        # Set implicit wait
        driver.implicitly_wait(5)
        
        return driver
        
    except Exception as e:
        log(f"FAILED to create driver: {e}")
        traceback.print_exc()
        sys.exit(1)


def scroll_to_bottom(driver, pause=2.0, max_scrolls=200):
    """Scroll with detailed logging."""
    log("Starting scroll sequence...")
    
    last_height = driver.execute_script("return document.body.scrollHeight")
    log(f"Initial page height: {last_height}")
    
    scrolls = 0
    no_change = 0
    
    while scrolls < max_scrolls and no_change < 3:
        # Scroll down
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        
        new_height = driver.execute_script("return document.body.scrollHeight")
        card_count = len(driver.find_elements(By.CSS_SELECTOR, ".cg-card"))
        
        if new_height == last_height:
            no_change += 1
            log(f"  Scroll {scrolls}: No height change ({no_change}/3), {card_count} cards")
        else:
            no_change = 0
            last_height = new_height
            if scrolls % 5 == 0:
                log(f"  Scroll {scrolls}: Height={new_height}, Cards={card_count}")
        
        scrolls += 1
    
    final_cards = len(driver.find_elements(By.CSS_SELECTOR, ".cg-card"))
    log(f"Scrolling complete. Final: {last_height}px, {final_cards} cards found")
    return final_cards


def extract_slug(url):
    """Get slug from channel URL."""
    try:
        path = urlparse(url).path.strip('/')
        return path.split('/')[-1]
    except:
        return None


def parse_card(card):
    """Extract data from a single card element."""
    try:
        # Channel label (CH 102)
        label_elem = None
        try:
            label_elem = card.find_element(By.CSS_SELECTOR, ".cg-channel-label")
        except:
            return None  # Skip non-channel cards
        
        label_text = label_elem.text if label_elem else ""
        match = re.search(r'CH\s*(\d+)', label_text, re.I)
        ch_num = int(match.group(1)) if match else None
        
        # Title link
        title_link = card.find_element(By.CSS_SELECTOR, ".cg-channel-title")
        name = title_link.text.strip()
        url = title_link.get_attribute("href") or ""
        slug = extract_slug(url)
        
        if not slug:
            return None
        
        # Optional fields
        sub = ""
        try:
            sub = card.find_element(By.CSS_SELECTOR, ".cg-channel-subheadline").text.strip()
        except:
            pass
        
        desc = ""
        try:
            desc = card.find_element(By.CSS_SELECTOR, ".cg-channel-description").text.strip()
        except:
            pass
        
        # Image
        img = ""
        try:
            img = card.find_element(By.CSS_SELECTOR, ".cg-image-wrapper img").get_attribute("src")
        except:
            pass
        
        # Deep link
        deep = ""
        try:
            deep = card.find_element(By.CSS_SELECTOR, "a[data-player-link='true']").get_attribute("href")
        except:
            pass
        
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
        
    except Exception as e:
        return None


def scrape_guide():
    """Main scraping function with full logging."""
    log("=" * 60)
    log("STARTING SCRAPER")
    log("=" * 60)
    
    driver = setup_driver()
    channels = []
    
    try:
        # Navigate
        url = "https://www.siriusxm.ca/channel-guide/?plan=All+Access"
        log(f"Navigating to: {url}")
        
        driver.get(url)
        log("Page load initiated, waiting...")
        
        # Wait for page to be ready
        time.sleep(5)
        
        # Check if we got the right page
        current_url = driver.current_url
        page_title = driver.title
        log(f"Current URL: {current_url}")
        log(f"Page title: {page_title}")
        
        # Check for common errors
        page_source = driver.page_source[:500]
        if "Access Denied" in page_source or "403" in page_source:
            log("ERROR: Access denied / blocked")
            return []
        
        if len(driver.page_source) < 1000:
            log("ERROR: Page source too short, likely blocked")
            return []
        
        # Wait for channel cards
        log("Waiting for .cg-card elements...")
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".cg-card"))
            )
            log("Found .cg-card elements!")
        except Exception as e:
            log(f"WARNING: Timeout waiting for .cg-card: {e}")
            # Try anyway - they might be there
            pass
        
        # Count initial cards
        initial_cards = driver.find_elements(By.CSS_SELECTOR, ".cg-card")
        log(f"Initial card count: {len(initial_cards)}")
        
        if len(initial_cards) == 0:
            log("ERROR: No cards found. Saving debug HTML...")
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            log("Saved debug_page.html for inspection")
            return []
        
        # Scroll to load all
        total_cards = scroll_to_bottom(driver, pause=2.0, max_scrolls=250)
        
        # Extract all cards
        log("Extracting channel data from cards...")
        cards = driver.find_elements(By.CSS_SELECTOR, ".cg-card")
        log(f"Processing {len(cards)} card elements...")
        
        for i, card in enumerate(cards):
            if (i + 1) % 100 == 0:
                log(f"  Processed {i + 1}/{len(cards)} cards, found {len(channels)} valid channels")
            
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
    """Get numeric channel key from channel page."""
    if not slug:
        return None
    
    url = f"https://www.siriusxm.ca/channels/{slug}/"
    try:
        driver.get(url)
        time.sleep(1.5)
        source = driver.page_source
        
        # Try multiple patterns
        patterns = [
            r"channel_keys\s*:\s*['\"](\d+)['\"]",
            r"contentId['\"]?\s*:\s*['\"](\d+)['\"]",
            r"data-channel-id=['\"]?(\d+)['\"]?",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, source)
            if match:
                return match.group(1)
                
    except Exception as e:
        log(f"    Error getting key for {slug}: {e}")
    
    return None


def fetch_keys(channels):
    """Fetch numeric keys for all channels."""
    if not channels:
        return channels
    
    log(f"\nFetching numeric keys for {len(channels)} channels...")
    driver = setup_driver()
    
    try:
        for i, ch in enumerate(channels):
            if (i + 1) % 50 == 0:
                log(f"  {i + 1}/{len(channels)}...")
            
            key = get_channel_key(driver, ch.get("slug"))
            if key:
                ch["channel_key"] = key
            
            time.sleep(0.3)
            
    finally:
        driver.quit()
    
    return channels


def test_api(channels):
    """Test channels against MountainWrapper API."""
    log("\nTesting against MountainWrapper API...")
    
    # Collect IDs to test
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
            resp = requests.get(url, headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0"
            }, timeout=15)
            
            if resp.status_code == 200:
                data = resp.json()
                for cid in batch:
                    cid_str = str(cid)
                    if data.get("channels") and cid_str in data["channels"]:
                        # Find matching channel and attach data
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
    # Scrape
    channels = scrape_guide()
    
    if not channels:
        log("No channels scraped. Exiting.")
        sys.exit(1)
    
    # Get keys
    channels = fetch_keys(channels)
    
    # Test API
    channels = test_api(channels)
    
    # Save
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
    
    # Show sample
    print("\n--- Sample ---")
    for c in channels[:5]:
        print(f"  CH{c.get('channel_number', '?'):>4} | {c['name']:<30} | {c.get('channel_key', 'N/A'):<6} | {c.get('slug', 'N/A')}")


if __name__ == "__main__":
    main()
