#!/usr/bin/env python3
"""
SiriusXM Channel Guide Scraper
Scrapes all channels from siriusxm.ca/channel-guide/ using Selenium,
extracts channel info, and fetches MountainWrapper API data.
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
from selenium.webdriver.common.action_chains import ActionChains


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
    
    # Essential flags for headless/containerized environments
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
# SCROLLING & LOADING
# ============================================================================

def scroll_to_bottom(driver, scroll_pause=2.0, max_scrolls=200):
    """
    Scroll to the very bottom of the page, handling lazy-loaded content.
    Returns the final scroll height.
    """
    last_height = driver.execute_script("return document.body.scrollHeight")
    scrolls = 0
    
    print(f"Starting scroll... (current height: {last_height})")
    
    while scrolls < max_scrolls:
        # Scroll down
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(scroll_pause)
        
        # Wait for any loading indicators to disappear
        try:
            WebDriverWait(driver, 3).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except:
            pass
        
        new_height = driver.execute_script("return document.body.scrollHeight")
        
        if new_height == last_height:
            # Try one more scroll after a longer wait to confirm we're at bottom
            time.sleep(scroll_pause * 2)
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                print(f"Reached bottom after {scrolls + 1} scrolls (height: {new_height})")
                break
        
        last_height = new_height
        scrolls += 1
        
        if scrolls % 10 == 0:
            print(f"  Scrolled {scrolls} times, height: {new_height}")
    
    return last_height


# ============================================================================
# CHANNEL EXTRACTION
# ============================================================================

def extract_channel_slug(href):
    """
    Extract channel slug from URL.
    e.g., 'https://www.siriusxm.ca/channels/diplos-revolution/?...' -> 'diplos-revolution'
    """
    path = urlparse(href).path
    # Remove trailing slash and extract last path segment
    slug = path.strip('/').split('/')[-1]
    return slug


def extract_channel_key_from_page(driver, channel_slug):
    """
    Visit individual channel page and extract channel_keys from <script> tag.
    Returns the numeric channel key or None.
    """
    channel_url = f"https://www.siriusxm.ca/channels/{channel_slug}/"
    
    try:
        driver.get(channel_url)
        time.sleep(1.5)  # Wait for JS to execute
        
        page_source = driver.page_source
        
        # Look for channel_keys in script tags
        # Pattern: channel_keys: '9172' or channel_keys: "9172"
        match = re.search(r"channel_keys\s*:\s*['\"](\d+)['\"]", page_source)
        if match:
            return match.group(1)
        
        # Alternative: look for contentId in any script
        match = re.search(r"contentId['\"]?\s*:\s*['\"]([^'\"]+)['\"]", page_source)
        if match:
            return match.group(1)
            
    except Exception as e:
        print(f"    Error fetching channel page for {channel_slug}: {e}")
    
    return None


def parse_channel_card(card_element, driver=None, fetch_keys=False):
    """
    Extract all data from a single cg-card element.
    
    Args:
        card_element: Selenium WebElement for the cg-card
        driver: Optional driver for fetching channel_keys from detail page
        fetch_keys: Whether to visit each channel page to get numeric ID
    
    Returns dict with channel info.
    """
    channel = {}
    
    try:
        # Channel number from cg-channel-label (e.g., "CH 102")
        label_elem = card_element.find_element(By.CSS_SELECTOR, ".cg-channel-label")
        label_text = label_elem.text.strip()
        match = re.search(r'CH\s*(\d+)', label_text, re.IGNORECASE)
        channel['channel_number'] = int(match.group(1)) if match else None
        
        # Channel name from cg-channel-title link
        title_link = card_element.find_element(By.CSS_SELECTOR, ".cg-channel-title")
        channel['name'] = title_link.text.strip()
        channel['url'] = title_link.get_attribute('href')
        channel['slug'] = extract_channel_slug(channel['url'])
        
        # Subheadline (short description)
        try:
            sub_elem = card_element.find_element(By.CSS_SELECTOR, ".cg-channel-subheadline")
            channel['subheadline'] = sub_elem.text.strip()
        except:
            channel['subheadline'] = ''
        
        # Description
        try:
            desc_elem = card_element.find_element(By.CSS_SELECTOR, ".cg-channel-description")
            channel['description'] = desc_elem.text.strip()
        except:
            channel['description'] = ''
        
        # Explicit badge
        try:
            card_element.find_element(By.CSS_SELECTOR, ".cg-explicit-badge")
            channel['explicit'] = True
        except:
            channel['explicit'] = False
        
        # Image URL
        try:
            img = card_element.find_element(By.CSS_SELECTOR, ".cg-image-wrapper img")
            channel['image'] = img.get_attribute('src')
        except:
            channel['image'] = ''
        
        # Deep link / player link
        try:
            player_link = card_element.find_element(By.CSS_SELECTOR, "a[data-player-link='true']")
            channel['deep_link'] = player_link.get_attribute('href')
        except:
            channel['deep_link'] = ''
        
        # On-air info
        try:
            on_air_name = card_element.find_element(By.CSS_SELECTOR, ".cg-on-air-show-name")
            channel['on_air_now'] = on_air_name.text.strip()
        except:
            channel['on_air_now'] = ''
        
        try:
            on_air_time = card_element.find_element(By.CSS_SELECTOR, ".cg-on-air-time")
            channel['on_air_time'] = on_air_time.text.strip()
        except:
            channel['on_air_time'] = ''
        
        # Fetch numeric channel_keys from individual page (slow but thorough)
        if fetch_keys and driver:
            print(f"  Fetching channel key for {channel['slug']}...")
            channel['channel_key'] = extract_channel_key_from_page(driver, channel['slug'])
            time.sleep(0.5)  # Be polite
        
    except Exception as e:
        print(f"Error parsing card: {e}")
        return None
    
    return channel


# ============================================================================
# MAIN SCRAPER
# ============================================================================

def scrape_all_channels(fetch_keys=False):
    """
    Main scraper. Navigates to channel guide, scrolls to bottom,
    extracts all channel cards.
    
    Args:
        fetch_keys: If True, visits each channel page to get numeric channel_keys.
                   Much slower but gets the MountainWrapper-compatible IDs.
    
    Returns list of channel dicts.
    """
    driver = setup_driver(headless=True)
    channels = []
    
    try:
        print(f"Navigating to {CHANNEL_GUIDE_URL}")
        driver.get(CHANNEL_GUIDE_URL)
        
        # Wait for initial cards to load
        print("Waiting for channel cards to load...")
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#channel-guide-v3-container .cg-card"))
        )
        
        # Give extra time for lazy images
        time.sleep(3)
        
        # Scroll to bottom to load all channels
        print("Scrolling to load all channels...")
        scroll_to_bottom(driver, scroll_pause=2.0, max_scrolls=300)
        
        # Now extract all cards
        print("Extracting channel data...")
        cards = driver.find_elements(By.CSS_SELECTOR, "#channel-guide-v3-container .cg-wrapper.cg-ready .cg-listing .cg-card")
        print(f"Found {len(cards)} channel cards")
        
        for i, card in enumerate(cards):
            if (i + 1) % 50 == 0:
                print(f"  Processed {i + 1}/{len(cards)}...")
            
            channel = parse_channel_card(card, driver=driver if fetch_keys else None, fetch_keys=fetch_keys)
            if channel:
                channels.append(channel)
        
        # If we didn't fetch keys during parsing, do a batch pass now
        # (Alternative: fetch all keys from the API using slugs)
        
    finally:
        driver.quit()
    
    return channels


# ============================================================================
# API FETCH (MountainWrapper)
# ============================================================================

def fetch_mountain_wrapper(channel_keys, batch_size=10):
    """
    Fetch channel data from MountainWrapper API.
    channel_keys can be numeric IDs or legacy string IDs.
    
    Returns dict mapping channel_key -> API response data.
    """
    results = {}
    
    # Split into batches to avoid URL length issues
    for i in range(0, len(channel_keys), batch_size):
        batch = channel_keys[i:i + batch_size]
        ids_param = ','.join(str(k) for k in batch)
        
        url = f"{MOUNTAIN_WRAPPER_BASE}?pagename=SXM/Services/MountainWrapper&channels={ids_param}"
        
        try:
            response = requests.get(url, headers={
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                for key in batch:
                    key_str = str(key)
                    if data.get('channels') and key_str in data['channels']:
                        results[key_str] = data['channels'][key_str]
            else:
                print(f"  API error for batch {i//batch_size + 1}: HTTP {response.status_code}")
                
        except Exception as e:
            print(f"  Error fetching batch {i//batch_size + 1}: {e}")
        
        time.sleep(0.3)  # Rate limiting
    
    return results


# ============================================================================
# ALTERNATIVE: Fetch keys via API using slugs
# ============================================================================

def try_slug_as_channel_id(slug):
    """
    Try using the channel slug directly in MountainWrapper.
    Some slugs work (like 'shade45'), most don't.
    """
    url = f"{MOUNTAIN_WRAPPER_BASE}?pagename=SXM/Services/MountainWrapper&channels={slug}"
    try:
        response = requests.get(url, headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0'
        }, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('channels') and slug in data['channels']:
                return data['channels'][slug]
    except:
        pass
    return None


# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    print("=" * 60)
    print("SiriusXM Channel Guide Scraper")
    print("=" * 60)
    
    # Phase 1: Scrape the channel guide
    # Set fetch_keys=True to visit each channel page for numeric IDs (SLOW)
    # Set fetch_keys=False for just guide data (FAST)
    print("\n[Phase 1] Scraping channel guide...")
    channels = scrape_all_channels(fetch_keys=False)
    
    print(f"\nScraped {len(channels)} channels from guide")
    
    # Phase 2: Try to get MountainWrapper data
    # Strategy: Try each slug as a channel ID, collect working ones
    print("\n[Phase 2] Testing channels against MountainWrapper API...")
    
    working_ids = {}
    failed_slugs = []
    
    for ch in channels:
        slug = ch['slug']
        result = try_slug_as_channel_id(slug)
        if result:
            working_ids[slug] = result
            ch['mountain_wrapper_id'] = slug
            ch['api_data'] = result
            print(f"  ✓ {slug} -> {result.get('displayname', 'N/A')}")
        else:
            failed_slugs.append(slug)
    
    print(f"\nWorking IDs: {len(working_ids)}/{len(channels)}")
    
    # Phase 3: For failed slugs, try to find numeric keys
    # This requires visiting each channel page - SLOW
    if failed_slugs:
        print(f"\n[Phase 3] Fetching numeric keys for {len(failed_slugs)} remaining channels...")
        print("(This requires Selenium and will take several minutes)")
        
        driver = setup_driver(headless=True)
        try:
            for slug in failed_slugs:
                key = extract_channel_key_from_page(driver, slug)
                if key:
                    # Test the numeric key
                    result = try_slug_as_channel_id(key)
                    if result:
                        # Find the channel in our list and update
                        for ch in channels:
                            if ch['slug'] == slug:
                                ch['channel_key'] = key
                                ch['mountain_wrapper_id'] = key
                                ch['api_data'] = result
                                working_ids[key] = result
                                print(f"  ✓ {slug} -> key:{key} -> {result.get('displayname')}")
                                break
                    else:
                        print(f"  ✗ {slug} -> key:{key} (API rejected)")
                else:
                    print(f"  ✗ {slug} -> no key found")
                
                time.sleep(0.5)
        finally:
            driver.quit()
    
    # Save results
    output = {
        'total_scraped': len(channels),
        'total_working_api': len(working_ids),
        'channels': channels,
        'working_ids': list(working_ids.keys())
    }
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'=' * 60}")
    print(f"Done! Saved to {OUTPUT_FILE}")
    print(f"Total channels: {len(channels)}")
    print(f"API-compatible: {len(working_ids)}")
    print(f"{'=' * 60}")
    
    # Print sample
    print("\nSample channels:")
    for ch in channels[:5]:
        print(f"  CH {ch.get('channel_number', '?'):>3} | {ch['name']:<30} | slug: {ch['slug']:<25} | key: {ch.get('channel_key', 'N/A')}")


if __name__ == "__main__":
    main()
