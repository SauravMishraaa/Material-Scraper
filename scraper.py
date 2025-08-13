from __future__ import annotations

import argparse
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from playwright.sync_api import sync_playwright, Page

ROOT = Path(__file__).parent.resolve()
CONFIG_PATH = ROOT / "config" / "scraper_config.yaml"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_JSON = DATA_DIR / "materials.json"


def now_ts() -> int:
    return int(time.time())


# Handles "48€90", "1 320 €", "1 320,50 €", "$1,999.00"
_EURO_SPLIT = re.compile(r"(\d+)\D+(\d{2})")
_NUM_GROUP = re.compile(r"(\d[\d\s.,]*\d)")

def parse_price_with_currency(text: str) -> Tuple[Optional[str], Optional[float]]:
    if not text:
        return None, None
    t = text.replace("\xa0", " ").replace("\u202f", " ").strip()
    currency = None
    for sym in ("€", "$", "£", "₹"):
        if sym in t:
            currency = sym
            break

    m = _NUM_GROUP.search(t)
    if m:
        num = m.group(1).replace(" ", "")
        # "1.234,56" -> "1234.56"
        if "," in num and "." in num:
            num = num.replace(".", "").replace(",", ".")
        elif "," in num:
            if num.count(",") == 1:
                num = num.replace(",", ".")
            else:
                num = num.replace(",", "")
        try:
            return currency, float(num)
        except Exception:
            pass

    m2 = _EURO_SPLIT.search(t)  # "48€90"
    if m2:
        try:
            return currency or "€", float(f"{m2.group(1)}.{m2.group(2)}")
        except Exception:
            return currency or "€", None

    return currency, None


def clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class CategoryConfig:
    name: str
    url: str
    card: str
    paging_mode: str
    next_button: Optional[str] = None
    max_pages: int = 12
    load_more_button: Optional[str] = None
    scroll_steps: int = 25
    scroll_wait_ms: int = 400


@dataclass
class SupplierConfig:
    supplier: str
    base_url: str
    categories: List[CategoryConfig]


@dataclass
class ScraperConfig:
    headless: bool
    user_agent: Optional[str]
    suppliers: List[SupplierConfig]


def load_config(path: Path) -> ScraperConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    sups: List[SupplierConfig] = []
    for s in raw["suppliers"]:
        cats: List[CategoryConfig] = []
        for c in s["categories"]:
            paging = c.get("paging", {})
            cats.append(
                CategoryConfig(
                    name=c["name"],
                    url=c["url"],
                    card=c["selectors"]["card"],
                    paging_mode=paging.get("mode", "none"),
                    next_button=paging.get("next_button"),
                    max_pages=int(paging.get("max_pages", 12)),
                    load_more_button=paging.get("load_more_button"),
                    scroll_steps=int(paging.get("scroll_steps", 25)),
                    scroll_wait_ms=int(paging.get("scroll_wait_ms", 400)),
                )
            )
        sups.append(
            SupplierConfig(
                supplier=s["supplier"],
                base_url=s["base_url"],
                categories=cats,
            )
        )
    return ScraperConfig(
        headless=bool(raw.get("headless", True)),
        user_agent=raw.get("user_agent"),
        suppliers=sups,
    )


NAME_HINTS = [
    ".a-designation__label",
    "[data-test-id='product-tile-title']",
    "[data-test-id='product-title']",
    "[data-testid='product-card-listings-title']",
    "[data-testid='product-card-listings']",
    "[data-testid='productCardListing']",
    "h3",
    ".product-title",
    ".title",
    "a[title]",
]
PRICE_HINTS = [
    ".m-price.-main .m-price__line",
    ".m-price:not(.-crossed) .m-price__line",
    "[data-test-id='price']",
    "[data-testid='price-main']",
    "[data-testid='price-main'] [itemprop='price']",  
    "[data-test-id='price-first-end-currency']",
    ".price",
    ".product-price",
    ".money",
    "span:has-text('€')",
]

BRAND_HINTS = [
    ".a-vendor__name",
    "[data-test-id='brand']",
    "[data-test-id='manufacturer']",
    ".product-brand",
    ".brand",
    "[data-testid='brand-image']",  
]

UNIT_HINTS = [
    ".m-price.-secondary .m-price__unit",
    "[data-testid='product-card-listings-title']",
    "[data-test-id*='unit']",
    "[data-test-id*='pack']",
    ".unit",
    ".pack-size",
    ".volume",
    ".contenance",
    ".size",
]
IMAGE_HINTS = [
    ".a-illustration__img",
    "picture source",
    "[data-testid='image']",  
    "img[src]",
    "img[data-srcset]",
    "img[srcset]",
    ".product-image img",
    ".thumbnail img",
    ".product-card img",
    "img[data-src]",
    "img[data-original]",
]
LINK_HINTS = [
    ".a-designation[href]",
    "a[href*='/p/']",
    "a[href*='-pr']",
    "a[href]",
]


def first_text(page_or_card, selectors: List[str]) -> str:
    for css in selectors:
        try:
            loc = page_or_card.locator(css)
            if loc.count() > 0:
                txt = loc.first.inner_text().strip()
                if txt:
                    return clean_text(txt)
        except Exception:
            continue
    return ""


def first_attr(page_or_card, selectors: List[str], attr: str) -> str:
    for css in selectors:
        try:
            loc = page_or_card.locator(css)
            if loc.count() > 0:
                val = loc.first.get_attribute(attr)
                if val:
                    return val.strip()
        except Exception:
            continue
    return ""


def resolve_url(href: str, base_url: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return base_url.rstrip("/") + href
    return base_url.rstrip("/") + "/" + href


def extract_from_card(card, category_name: str, supplier: str, base_url: str) -> Dict[str, Any]:
    if supplier == "ManoMano":
        try:
            name = card.get_attribute("title") or first_text(card, ["[data-testid='product-card-listings-title']", "p"])
            
            href = card.get_attribute("href")
            product_url = resolve_url(href, base_url)
            
            price_elem = card.locator("[data-testid='price-main']").first
            price_text = ""
            if price_elem:
                price_text = price_elem.inner_text()
            
            if not price_text:
                price_text = first_text(card, [".nkATTd", "span:has-text('€')"])
            
            currency, price = parse_price_with_currency(price_text)
            
            brand = ""
            try:
                brand_img = card.locator("[data-testid='brand-image']").first
                if brand_img.is_visible(timeout=2000):  # Use a short timeout
                    brand = brand_img.get_attribute("alt", timeout=2000) or ""
            except Exception:
                pass
            
            unit = ""
            
            image_url = ""
            try:
                img = card.locator("[data-testid='image']").first
                if img and img.is_visible(timeout=2000):
                    srcset = img.get_attribute("srcset", timeout=2000)
                    if srcset:
                        urls = srcset.split(",")
                        for url_part in urls:
                            parts = url_part.strip().split(" ")
                            if len(parts) >= 1:
                                if "2x" in url_part:  
                                    image_url = parts[0]
                                    break
                                elif not image_url: 
                                    image_url = parts[0]
                    
                    # Fallback to src
                    if not image_url:
                        image_url = img.get_attribute("src", timeout=2000) or ""
            except Exception as e:
                print(f"Error getting image: {e}")
            
            print(f"ManoMano extraction: name={name}, price={price}, image_url={image_url}")
            
            return {
                "supplier": supplier,
                "category": category_name,
                "name": name,
                "price": price,
                "currency": currency,
                "url": product_url,
                "brand": brand,
                "unit": unit,
                "image_url": image_url,
                "timestamp": now_ts(),
            }
        
        except Exception as e:
            import traceback
            print(f"Error extracting ManoMano card: {e}")
            traceback.print_exc()
            
            # Return a minimal valid item instead of None
            return {
                "supplier": supplier,
                "category": category_name,
                "name": "Error extracting product",
                "price": 0,
                "currency": "€",
                "url": base_url,  
                "brand": "",
                "unit": "",
                "image_url": "",
                "timestamp": now_ts(),
                "error": str(e)
            }
    elif supplier == "Leroy Merlin":
        try:
            name = first_text(card, [".a-designation__label", ".a-designation"])
            if not name:
                name_link = card.locator(".a-designation[title]").first
                if name_link:
                    name = name_link.get_attribute("title") or ""
            href = first_attr(card, [".a-designation"], "href")
            product_url = resolve_url(href, base_url)
            
            price_text = first_text(card, [".m-price.-main .m-price__line", ".m-price:not(.-crossed) .m-price__line"])
            if not price_text:
                price_text = first_text(card, [".o-thumbnailPrice .m-price.-main"])
            currency, price = parse_price_with_currency(price_text)

            brand = first_text(card, [".a-vendor__name"])

            unit = ""
            unit_text = first_text(card, [".m-price.-secondary .m-price__unit", ".m-price__unit"])
            if unit_text:
                unit_match = re.search(r'/\s+(\w+)', unit_text)
                if unit_match:
                   unit = unit_match.group(1)
            
            image_url = ""
            img = card.locator(".a-illustration__img").first
            if img:
                image_url = img.get_attribute("src") or ""
            
            if not image_url:
                # Look for the highest resolution image in picture sources
                sources = card.locator("picture source")
                highest_width = 0
                for i in range(sources.count()):
                    source = sources.nth(i)
                    srcset = source.get_attribute("srcset") or ""
                    media = source.get_attribute("media") or ""
                    
                    # Try to extract width from media query
                    width_match = re.search(r'width=(\d+)', srcset)
                    if width_match:
                        width = int(width_match.group(1))
                        if width > highest_width:
                            highest_width = width
                            image_url = re.sub(r'\?.*$', '', srcset) 
            if not image_url:
                image_url = first_attr(card, ["img[src]"], "src")
            
            print(f"Leroy Merlin extraction: name={name}, price={price}, image_url={image_url}")

            return {
                "supplier": supplier,
                "category": category_name,
                "name": name,
                "price": price,
                "currency": currency,
                "url": product_url,
                "brand": brand,
                "unit": unit,
                "image_url": image_url,
                "timestamp": now_ts(),
            }
        except Exception as e:
            import traceback
            print(f"Error extracting Leroy Merlin card: {e}")
            traceback.print_exc()
            return {
                "supplier": supplier,
                "category": category_name,
                "name": "Error extracting product",
                "price": 0,
                "currency": "€",
                "url": base_url,  
                "brand": "",
                "unit": "",
                "image_url": "",
                "timestamp": now_ts(),
                "error": str(e)
            }

    name = first_text(card, NAME_HINTS)
    price_text = first_text(card, PRICE_HINTS)
    currency, price = parse_price_with_currency(price_text)
    brand = first_text(card, BRAND_HINTS)
    unit = first_text(card, UNIT_HINTS)

    image_url = ""
    
    direct_img = (
        first_attr(card, IMAGE_HINTS, "src")
        or first_attr(card, IMAGE_HINTS, "data-src")
        or first_attr(card, IMAGE_HINTS, "data-original")
    )
    
    if direct_img and not direct_img.startswith("data:image"):
        image_url = direct_img
    else:
        for attr in ["srcset", "data-srcset"]:
            for selector in IMAGE_HINTS:
                try:
                    img_elements = card.locator(selector)
                    for i in range(img_elements.count()):
                        srcset = img_elements.nth(i).get_attribute(attr)
                        if srcset and "https://" in srcset:
                            # Extract URL from srcset (which may contain multiple URLs)
                            url_match = re.search(r'(https://[^,\s]+)', srcset)
                            if url_match and not url_match.group(1).startswith("data:image"):
                                image_url = url_match.group(1)
                                break
                    if image_url:
                        break
                except Exception:
                    continue
            if image_url:
                break
    
    # For Castorama specifically
    if not image_url and supplier == "Castorama":
        try:
            all_imgs = card.locator("img")
            for i in range(all_imgs.count()):
                img = all_imgs.nth(i)
                for attr in ["srcset", "data-srcset", "src", "data-src"]:
                    val = img.get_attribute(attr) or ""
                    if "media.castorama.fr" in val:
                        # Extract just the URL part
                        url_part = re.search(r'(https://media\.castorama\.fr/[^,\s]+)', val)
                        if url_part:
                            image_url = url_part.group(1)
                            break
                if image_url:
                    break
        except Exception:
            pass

    href = first_attr(card, LINK_HINTS, "href")
    product_url = resolve_url(href, base_url)

    item = {
        "supplier": supplier,
        "category": category_name,
        "name": name,
        "price": price,
        "currency": currency,
        "url": product_url,
        "brand": brand,
        "unit": unit,
        "image_url": image_url,
        "timestamp": now_ts(),
    }

    return item


def do_pagination(page: Page, next_button_selector: Optional[str]) -> bool:
    if not next_button_selector:
        return False
    try:
        btn = page.locator(next_button_selector)
        if btn.count() > 0 and btn.first.is_visible() and btn.first.is_enabled():
            btn.first.click()
            page.wait_for_load_state("networkidle")
            return True
    except Exception:
        return False
    return False


def do_load_more(page: Page, load_button_selector: Optional[str]) -> bool:
    if not load_button_selector:
        return False
    try:
        btn = page.locator(load_button_selector)
        if btn.count() > 0 and btn.first.is_visible() and btn.first.is_enabled():
            btn.first.click()
            page.wait_for_timeout(600)
            return True
    except Exception:
        return False
    return False


def do_infinite_scroll(page: Page, steps: int, wait_ms: int):
    for _ in range(max(1, steps)):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(max(100, wait_ms))

def scrape_category(page: Page, cat: CategoryConfig, supplier: SupplierConfig, target_min: int) -> List[Dict[str, Any]]:
    """Scrape a single category page, handling pagination and collecting items."""
    url = cat.url
    print(f"Scraping {supplier.supplier}/{cat.name} from {url}")
    
    # Navigate to the category page
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(1000)  # give JS time to start
    
    # Handle site-specific cookie consents and initial setup
    if supplier.supplier == "Leroy Merlin":
        try:
            # Wait a bit longer for Leroy Merlin's page to fully load
            page.wait_for_load_state("networkidle", timeout=10000)
            
            # Handle cookie consent dialog - with proper escaping
            for selector in [
                "#didomi-notice-agree-button",
                'button:has-text("Accepter")',
                'button:has-text("Accept all")',
                'button:has-text("J\'accepte")'
            ]:
                try:
                    consent = page.locator(selector).first
                    if consent and consent.is_visible(timeout=3000):
                        consent.click()
                        print("Clicked cookie consent button on Leroy Merlin")
                        page.wait_for_timeout(1000)
                        break
                except Exception as e:
                    print(f"Failed with selector {selector}: {e}")
        except Exception as e:
            print(f"Error handling Leroy Merlin cookie consent: {e}")
    
    elif supplier.supplier == "ManoMano":
        try:
            # Handle ManoMano's cookie consent
            for selector in [
                "button[data-testid='cookie-banner-accept-button']",
                "#didomi-notice-agree-button",
                'button:has-text("Accepter")',
                'button:has-text("Accept all")'
            ]:
                try:
                    consent = page.locator(selector).first
                    if consent and consent.is_visible(timeout=3000):
                        consent.click()
                        print("Clicked cookie consent button on ManoMano")
                        page.wait_for_timeout(1000)
                        break
                except:
                    pass
        except Exception as e:
            print(f"Cookie handling error for ManoMano (non-critical): {e}")
    
    elif supplier.supplier == "Castorama":
        try:
            # Castorama cookie consent
            for selector in [
                "#onetrust-accept-btn-handler",
                'button:has-text("Accepter")',
                'button:has-text("Accept all")'
            ]:
                try:
                    consent = page.locator(selector).first
                    if consent and consent.is_visible(timeout=3000):
                        consent.click()
                        print("Clicked cookie consent button on Castorama")
                        page.wait_for_timeout(1000)
                        break
                except:
                    pass
        except Exception as e:
            print(f"Cookie handling error for Castorama (non-critical): {e}")
    
    items: List[Dict[str, Any]] = []
    seen_keys = set()  # To avoid duplicates
    pages_seen = 0
    
    # Helper function to extract products from current page
    def collect_current_page() -> int:
        cards = page.locator(cat.card)  # Use cat.card directly
        card_count = cards.count()
        print(f"[DEBUG] Found {card_count} cards for {supplier.supplier}/{cat.name}")
        
        collected = 0
        for i in range(card_count):
            try:
                card = cards.nth(i)
                
                # Take occasional screenshots for debugging
                if os.environ.get("SCRAPER_SCREENSHOTS") == "1" and i % 10 == 0:
                    try:
                        card_path = DATA_DIR / f"{supplier.supplier}_card_{i}_{int(time.time())}.png"
                        card.screenshot(path=str(card_path))
                    except Exception:
                        pass
                
                item = extract_from_card(card, cat.name, supplier.supplier, supplier.base_url)
                
                # Only add valid, non-duplicate items
                if item and item["name"] and item["url"] != supplier.base_url:
                    key = (item["supplier"], item["url"], item["name"], item.get("unit") or "")
                    if key not in seen_keys:
                        items.append(item)
                        seen_keys.add(key)
                        collected += 1
            except Exception as e:
                print(f"Error processing card {i}: {e}")
        
        return collected
    
    # Different handling based on pagination mode
    if cat.paging_mode == "infinite_scroll":  
        for scroll_step in range(cat.scroll_steps): 
            # Scroll to bottom to trigger lazy loading
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(cat.scroll_wait_ms)  
            
            # Collect items from current view
            new_items = collect_current_page()
            print(f"Scroll {scroll_step+1}: Collected {new_items} new items")
            
            # Stop if we have enough items or no new items were found
            if len(items) >= target_min:
                break
                
            # If no new items after several scrolls, stop
            if new_items == 0 and scroll_step >= 2:
                print("No new items found after multiple scrolls, stopping")
                break
    
    else:  # Pagination mode
        while True:
            pages_seen += 1
            print(f"Processing page {pages_seen} of {cat.max_pages}")  
            
            # Collect items from current page
            new_items = collect_current_page()
            print(f"Page {pages_seen}: Collected {new_items} items")
            
            # Stop conditions
            if len(items) >= target_min:
                print(f"Reached target of {target_min} items, stopping pagination")
                break
                
            if pages_seen >= cat.max_pages:  
                print(f"Reached max pages ({cat.max_pages}), stopping pagination")
                break
            
            # Try to find and click the next page button
            if cat.next_button:  # Use cat.next_button directly
                next_button = page.locator(cat.next_button).first
                if next_button and next_button.is_visible():
                    try:
                        # Scroll the button into view
                        next_button.scroll_into_view_if_needed()
                        page.wait_for_timeout(500)
                        
                        # Click and wait for navigation
                        next_button.click()
                        page.wait_for_load_state("networkidle", timeout=10000)
                        page.wait_for_timeout(1000)  # Extra wait for JS
                    except Exception as e:
                        print(f"Error navigating to next page: {e}")
                        break
                else:
                    print("No next page button found, ending pagination")
                    break
            else:
                print("No next_button configured, ending pagination")
                break
    
    print(f"Finished scraping {supplier.supplier}/{cat.name}: collected {len(items)} items")
    return items

def scrape_all(cfg: ScraperConfig, min_items: int) -> List[Dict[str, Any]]:
    all_items = []
    
    with sync_playwright() as p:
        browser_type = p.chromium
        browser = browser_type.launch(
            headless=cfg.headless,
        )
        
        context = browser.new_context(
            user_agent=cfg.user_agent,
            viewport={"width": 1280, "height": 720},
        )
        
        page = context.new_page()
        
        for supplier_cfg in cfg.suppliers:
            print(f"Processing supplier: {supplier_cfg.supplier}")
            supplier_items = []
            
            for cat in supplier_cfg.categories:
                try:
                    items = scrape_category(page, cat, supplier_cfg, min_items - len(all_items))
                    supplier_items.extend(items)
                    print(f"Got {len(items)} items from {supplier_cfg.supplier}/{cat.name}")
                except Exception as e:
                    print(f"Error scraping {supplier_cfg.supplier}/{cat.name}: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Add debug info
            print(f"Finished {supplier_cfg.supplier}: collected {len(supplier_items)} items")
            all_items.extend(supplier_items)
                    
        browser.close()
    
    return all_items


def write_json(rows: List[Dict[str, Any]], out_path: Path):
    payload = {"scraped_at": now_ts(), "count": len(rows), "items": rows}
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_args():
    ap = argparse.ArgumentParser(description="Material scraper")
    ap.add_argument("--config", type=str, default=str(CONFIG_PATH), help="Path to scraper_config.yaml")
    ap.add_argument("--min-items", type=int, default=100, help="Minimum total items to aim for")
    return ap.parse_args()


def main():
    args = parse_args()
    cfg = load_config(Path(args.config))
    rows = scrape_all(cfg, min_items=args.min_items)
    write_json(rows, OUTPUT_JSON)
    print(f"Added {len(rows)} items → {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
