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
    "[data-test-id='product-tile-title']",
    "[data-test-id='product-title']",
    "h3",
    ".product-title",
    ".title",
    "a[title]",
]
PRICE_HINTS = [
    "[data-test-id='price']",
    "[data-test-id='price-first-end-currency']",
    ".price",
    ".product-price",
    ".money",
    "span:has-text('€')",
]
BRAND_HINTS = [
    "[data-test-id='brand']",
    "[data-test-id='manufacturer']",
    ".product-brand",
    ".brand",
]
UNIT_HINTS = [
    "[data-test-id*='unit']",
    "[data-test-id*='pack']",
    ".unit",
    ".pack-size",
    ".volume",
    ".contenance",
    ".size",
]
IMAGE_HINTS = [
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
    
    if not image_url and supplier == "Castorama":
        try:
            all_imgs = card.locator("img")
            for i in range(all_imgs.count()):
                img = all_imgs.nth(i)
                for attr in ["srcset", "data-srcset", "src", "data-src"]:
                    val = img.get_attribute(attr) or ""
                    if "media.castorama.fr" in val:
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

    if os.environ.get("SCRAPER_SCREENSHOTS") == "1":
        try:
            snap = DATA_DIR / f"snap_{supplier}_{int(time.time()*1000)}.png"
            card.screenshot(path=str(snap))
            item["screenshot_path"] = str(snap)
        except Exception:
            item["screenshot_path"] = ""

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
    page.goto(cat.url, wait_until="domcontentloaded")
    page.wait_for_timeout(800)

    items: List[Dict[str, Any]] = []
    seen_keys = set()
    pages_seen = 0

    def collect_current_page():
        cards = page.locator(cat.card)
        cnt = cards.count()
        collected = 0
        for i in range(cnt):
            card = cards.nth(i)
            item = extract_from_card(card, cat.name, supplier.supplier, supplier.base_url)
            key = (item["supplier"], item["url"], item["name"], item.get("unit") or "")
            if item["name"] and item["url"] and key not in seen_keys:
                items.append(item)
                seen_keys.add(key)
                collected += 1
        return collected

    collect_current_page()

    if cat.paging_mode == "pagination":
        while len(items) < target_min and pages_seen < cat.max_pages:
            pages_seen += 1
            if not do_pagination(page, cat.next_button):
                break
            collect_current_page()

    elif cat.paging_mode == "load_more":
        while len(items) < target_min and pages_seen < cat.max_pages:
            pages_seen += 1
            if not do_load_more(page, cat.load_more_button):
                break
            collect_current_page()

    elif cat.paging_mode == "infinite_scroll":
        do_infinite_scroll(page, cat.scroll_steps, cat.scroll_wait_ms)
        collect_current_page()

    return items


def scrape_all(cfg: ScraperConfig, min_items: int) -> List[Dict[str, Any]]:
    all_items: List[Dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.headless)
        context = browser.new_context(user_agent=cfg.user_agent or None)
        page = context.new_page()

        total_cats = sum(len(s.categories) for s in cfg.suppliers) or 1
        per_cat_target = max(1, min_items // total_cats)

        for sup in cfg.suppliers:
            for cat in sup.categories:
                print(f"[scrape] {sup.supplier} — {cat.name}")
                cat_items = scrape_category(page, cat, sup, per_cat_target)
                all_items.extend(cat_items)

        context.close()
        browser.close()

    dedup: List[Dict[str, Any]] = []
    seen = set()
    for r in all_items:
        key = (r["supplier"], r["url"], r["name"], r.get("unit") or "")
        if key not in seen:
            dedup.append(r)
            seen.add(key)
    return dedup


def write_json(rows: List[Dict[str, Any]], out_path: Path):
    payload = {"scraped_at": now_ts(), "count": len(rows), "items": rows}
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_args():
    ap = argparse.ArgumentParser(description="Donizo material scraper (Playwright)")
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
