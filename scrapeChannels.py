#!/usr/bin/env python3
"""
SiriusXM Channel Guide Scraper - FIXED
Handles missing elements, ads, and non-channel cards gracefully.
"""

import json
import re
import time
import requests
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ============================================================================
# CONFIGURATION
# ============================================================================

CHANNEL_GUIDE_URL = "https://www.siriusxm.ca/channel-guide/?plan=All+Access"
MOUNTAIN_WRAPPER_BASE = "https://www.siriusxm.com/servlet/Satellite"
OUTPUT_FILE = "siriusxm_all_channels.json"


# ============================================================================
# SELENIUM SETUP
# ============================================================================

def setup_driver(headless=True):
    """Configure and return a Chrome WebDriver instance."""
    chrome_options = Options()
    
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--ignore-certificate-errors")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    if headless:
        chrome_options.add_argument("--headless=new")
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
        '''
    })
    return driver


# ============================================================================
# SCROLLING
# ============================================================================

def scroll_to_bottom(driver, scroll_pause=2.0, max_scrolls=300):
    """Scroll to bottom, handling lazy-loaded content."""
    last_height = driver.execute_script("return document.body.scrollHeight")
    scrolls = 0
    no_change_count = 0
    
    print(f"Starting scroll... (height: {last_height})")
    
    while scrolls < max_scrolls and no_change_count < 3:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause)
        
        # Also scroll in smaller increments to trigger lazy loaders
        for offset in range(500, 2000, 500):
            driver.execute_script(f"window.scrollTo(0, document.body.scrollHeight - {offset});")
            time.sleep(0.3)
        
        new_height = driver.execute_script("return document.body.scrollHeight")
        
        if new_height == last_height:
            no_change_count += 1
            time.sleep(scroll_pause)
        else:
            no_change_count = 0
            last_height = new_height
        
        scrolls += 1
        
        if scrolls % 10 == 0:
            print(f"  Scroll {scrolls}, height: {last_height}, cards found so far: "
                  f"{len(driver.find_elements(By.CSS_SELECTOR, '#channel-guide-v3-container .cg-card'))}")
    
    print(f"Finished scrolling. Final height: {last_height}")
    return last_height


# ============================================================================
# CHANNEL EXTRACTION - ROBUST VERSION
# ============================================================================

def extract_slug_from_url(url):
    """Extract channel slug from URL path."""
    try:
        path = urlparse(url).path.strip('/')
        # URLs like /channels/radio-andy/ -> radio-andy
        parts = path.split('/')
        return parts[-1] if parts else None
    except:
        return None


def safe_find_text(element, selector, default=''):
    """Safely find text within an element, return default if not found."""
    try:
        return element.find_element(By.CSS_SELECTOR, selector).text.strip()
    except:
        return default


def safe_find_attr(element, selector, attr, default=''):
    """Safely find attribute within an element."""
    try:
        return element.find_element(By.CSS_SELECTOR, selector).get_attribute(attr) or default
    except:
        return default


def safe_find_element(parent, selector):
    """Safely find element, return None if not found."""
    try:
        return parent.find_element(By.CSS_SELECTOR, selector)
    except:
        return None


def parse_channel_card(card_element):
    """
    Extract channel data from a cg-card element.
    Returns None if this isn't a valid channel card.
    """
    # Skip non-channel cards (ads, headers, etc.)
    # A valid channel card must have a channel label or title
    label_elem = safe_find_element(card_element, ".cg-channel-label")
    title_elem = safe_find_element(card_element, ".cg-channel-title")
    
    if not label_elem and not title_elem:
        return None  # Skip ads, placeholders, etc.
    
    channel = {}
    
    # Channel number from label (e.g., "CH 102")
    if label_elem:
        label_text = label_elem.text.strip()
        match = re.search(r'CH\s*(\d+)', label_text, re.IGNORECASE)
        channel['channel_number'] = int(match.group(1)) if match else None
    else:
        channel['channel_number'] = None
    
    # Channel name and URL
    if title_elem:
        channel['name'] = title_elem.text.strip()
        channel['url'] = title_elem.get_attribute('href') or ''
        channel['slug'] = extract_slug_from_url(channel['url'])
    else:
        # Try to get name from image alt or other fallback
        channel['name'] = safe_find_attr(card_element, ".cg-image-wrapper img", "alt", "Unknown")
        channel['url'] = ''
        channel['slug'] = None
    
    # Skip if we couldn't get a slug (can't identify the channel)
    if not channel.get('slug'):
        return None
    
    # Subheadline
    channel['subheadline'] = safe_find_text(card_element, ".cg-channel-subheadline")
    
    # Description
    channel['description'] = safe_find_text(card_element, ".cg-channel-description")
    
    # Explicit badge
    try:
        card_element.find_element(By.CSS_SELECTOR, ".cg-explicit-badge")
        channel['explicit'] = True
    except:
        channel['explicit'] = False
    
    # Image URL
    channel['image'] = safe_find_attr(card_element, ".cg-image-wrapper img", "src")
    
    # Deep link (player URL from data-player-link)
    player_links = card_element.find_elements(By.CSS_SELECTOR, "a[data-player-link='true']")
    channel['deep_link'] = player_links[0].get_attribute('href') if player_links else ''
    
    # On-air info
    channel['on_air_now'] = safe_find_text(card_element, ".cg-on-air-show-name")
    channel['on_air_time'] = safe_find_text(card_element, ".cg-on-air-time")
    
    # On-air image
    channel['on_air_image'] = safe_find_attr(card_element, ".cg-on-air-image-wrapper img", "src")
    
    return channel


# ============================================================================
# CHANNEL KEY SCRAPING (from individual pages)
# ============================================================================

def extract_channel_key_from_page(driver, slug):
    """
    Visit channel page and extract channel_keys from embedded JS.
    Returns numeric key string or None.
    """
    if not slug:
        return None
    
    url = f"https://www.siriusxm.ca/channels/{slug}/"
    
    try:
        driver.get(url)
        time.sleep(1.5)
        
        source = driver.page_source
        
        # Pattern 1: channel_keys: '1234'
        match = re.search(r"channel_keys\s*:\s*['\"](\d+)['\"]", source)
        if match:
            return match.group(1)
        
        # Pattern 2: channel_keys: "1234"
        match = re.search(r"channel_keys\s*:\s*['\"]([^'\"]+)['\"]", source)
        if match:
            val = match.group(1)
            if val.isdigit():
                return val
        
        # Pattern 3: contentId in any script
        match = re.search(r"contentId['\"]?\s*:\s*['\"]([^'\"]+)['\"]", source)
        if match:
            val = match.group(1)
            if val.isdigit():
                return val
        
        # Pattern 4: Look for data-channel-id attributes
        match = re.search(r'data-channel-id=["\']?(\d+)["\']?', source)
        if match:
            return match.group(1)
            
    except Exception as e:
        print(f"    Error on page {slug}: {e}")
    
    return None


# ============================================================================
# MOUNTAINWRAPPER API
# ============================================================================

def test_channel_id(channel_id):
    """Test if a channel ID works in MountainWrapper API."""
    if not channel_id:
        return None
    
    url = f"{MOUNTAIN_WRAPPER_BASE}?pagename=SXM/Services/MountainWrapper&channels={channel_id}"
    
    try:
        response = requests.get(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('channels') and str(channel_id) in data['channels']:
                return data['channels'][str(channel_id)]
    except:
        pass
    
    return None


def batch_test_ids(channel_ids, batch_size=10):
    """Test multiple channel IDs against MountainWrapper in batches."""
    results = {}
    
    for i in range(0, len(channel_ids), batch_size):
        batch = [cid for cid in channel_ids[i:i + batch_size] if cid]
        if not batch:
            continue
            
        ids_param = ','.join(str(cid) for cid in batch)
        url = f"{MOUNTAIN_WRAPPER_BASE}?pagename=SXM/Services/MountainWrapper&channels={ids_param}"
        
        try:
            response = requests.get(url, headers={
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0'
            }, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                for cid in batch:
                    cid_str = str(cid)
                    if data.get('channels') and cid_str in data['channels']:
                        results[cid_str] = data['channels'][cid_str]
                        
        except Exception as e:
            print(f"  Batch error: {e}")
        
        time.sleep(0.3)
    
    return results


# ============================================================================
# MAIN SCRAPER
# ============================================================================

def scrape_all_channels():
    """Main scraper. Returns list of channel dicts."""
    driver = setup_driver(headless=True)
    channels = []
    skipped = 0
    
    try:
        print(f"Navigating to {CHANNEL_GUIDE_URL}")
        driver.get(CHANNEL_GUIDE_URL)
        
        # Wait for channel cards
        print("Waiting for initial load...")
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#channel-guide-v3-container .cg-card"))
        )
        time.sleep(3)
        
        # Scroll to load all
        scroll_to_bottom(driver, scroll_pause=2.0, max_scrolls=300)
        
        # Find all cards
        print("\nExtracting channel cards...")
        cards = driver.find_elements(By.CSS_SELECTOR, "#channel-guide-v3-container .cg-wrapper .cg-listing .cg-card")
        print(f"Total elements found: {len(cards)}")
        
        for i, card in enumerate(cards):
            if (i + 1) % 100 == 0:
                print(f"  Processed {i + 1}/{len(cards)}... (valid: {len(channels)}, skipped: {skipped})")
            
            channel = parse_channel_card(card)
            if channel:
                channels.append(channel)
            else:
                skipped += 1
        
    finally:
        driver.quit()
    
    print(f"\nValid channels: {len(channels)}, Skipped: {skipped}")
    return channels


# ============================================================================
# FETCH CHANNEL KEYS
# ============================================================================

def fetch_all_channel_keys(channels):
    """
    Visit each channel page to get numeric channel_keys.
    This is SLOW but gets the MountainWrapper-compatible IDs.
    """
    driver = setup_driver(headless=True)
    
    try:
        print(f"\nFetching numeric keys for {len(channels)} channels...")
        print("(This will take several minutes...)\n")
        
        for i, ch in enumerate(channels):
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(channels)}...")
            
            slug = ch.get('slug')
            if not slug:
                continue
            
            key = extract_channel_key_from_page(driver, slug)
            if key:
                ch['channel_key'] = key
            
            time.sleep(0.4)  # Be polite
            
    finally:
        driver.quit()
    
    return channels


# ============================================================================
# TEST AGAINST API
# ============================================================================

def test_channels_api(channels):
    """Test all channels against MountainWrapper API."""
    print("\nTesting channels against MountainWrapper API...")
    
    # First, try slugs
    slugs_to_test = [ch['slug'] for ch in channels if ch.get('slug')]
    print(f"Testing {len(slugs_to_test)} slugs...")
    slug_results = batch_test_ids(slugs_to_test, batch_size=15)
    
    for ch in channels:
        slug = ch.get('slug')
        if slug and slug in slug_results:
            ch['mountain_wrapper_id'] = slug
            ch['api_data'] = slug_results[slug]
    
    # Then try numeric keys for remaining
    keys_to_test = []
    for ch in channels:
        if not ch.get('api_data') and ch.get('channel_key'):
            keys_to_test.append(ch['channel_key'])
    
    if keys_to_test:
        print(f"Testing {len(keys_to_test)} numeric keys...")
        key_results = batch_test_ids(keys_to_test, batch_size=15)
        
        for ch in channels:
            key = ch.get('channel_key')
            if key and key in key_results and not ch.get('api_data'):
                ch['mountain_wrapper_id'] = key
                ch['api_data'] = key_results[key]
    
    working = sum(1 for ch in channels if ch.get('api_data'))
    print(f"Working API IDs: {working}/{len(channels)}")
    
    return channels


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 70)
    print("SiriusXM Channel Guide Scraper (Fixed)")
    print("=" * 70)
    
    # Phase 1: Scrape guide
    channels = scrape_all_channels()
    
    # Phase 2: Get numeric keys (optional - set to False to skip for speed)
    FETCH_KEYS = True
    if FETCH_KEYS:
        channels = fetch_all_channel_keys(channels)
    
    # Phase 3: Test against API
    channels = test_channels_api(channels)
    
    # Save
    output = {
        'metadata': {
            'total_channels': len(channels),
            'with_api_data': sum(1 for ch in channels if ch.get('api_data')),
            'with_numeric_key': sum(1 for ch in channels if ch.get('channel_key')),
            'scraped_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        },
        'channels': channels
    }
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'=' * 70}")
    print(f"SAVED: {OUTPUT_FILE}")
    print(f"Total: {len(channels)} channels")
    print(f"With API data: {output['metadata']['with_api_data']}")
    print(f"With numeric key: {output['metadata']['with_numeric_key']}")
    print(f"{'=' * 70}")
    
    # Show sample
    print("\nFirst 10 channels:")
    for ch in channels[:10]:
        api_id = ch.get('mountain_wrapper_id', 'N/A')
        num_key = ch.get('channel_key', 'N/A')
        print(f"  CH{ch.get('channel_number', '?'):>4} | {ch['name']:<35} | slug: {ch['slug']:<25} | key: {num_key:<6} | api: {api_id}")


if __name__ == "__main__":
    main()
