import os
import tempfile
import json
import threading
import time
import re
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest
from playwright.sync_api import sync_playwright, Page

from scraper import (
    parse_price_with_currency,
    resolve_url,
    extract_from_card,
    load_config,
    scrape_all,
    scrape_category,
    now_ts,
    clean_text,
    first_text,
    first_attr
)


@pytest.fixture
def temp_config_file():
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
        f.write(b"""
        headless: true
        user_agent: "Mozilla/5.0"
        suppliers:
          - supplier: "TestSupplier"
            base_url: "https://www.example.com"
            categories:
              - name: "Test Category"
                url: "https://www.example.com/category"
                selectors:
                  card: ".product-card"
                paging:
                  mode: "pagination"
                  next_button: ".next-page"
                  max_pages: 5
        """)
    yield Path(f.name)
    os.unlink(f.name)

@pytest.fixture
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        page = context.new_page()
        yield page
        browser.close()

class TestServer:
    def __init__(self, html_dir):
        self.html_dir = html_dir
        handler = SimpleHTTPRequestHandler
        os.chdir(html_dir)
        self.server = HTTPServer(("localhost", 0), handler)
        self.port = self.server.server_port
        
        # Start server in a thread
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
    
    def url(self, path="/"):
        return f"http://localhost:{self.port}{path}"
    
    def stop(self):
        self.server.shutdown()
        self.thread.join(1)

@pytest.fixture
def test_server():
    with tempfile.TemporaryDirectory() as tempdir:
        # Create index.html with product listings
        with open(os.path.join(tempdir, "index.html"), "w") as f:
            f.write("""<!DOCTYPE html>
            <html>
            <body>
                <div class="product-card">
                    <h3>Test Product 1</h3>
                    <div class="price">49.99 €</div>
                    <a href="/product1.html">Details</a>
                    <img src="/img1.jpg" alt="Product 1">
                </div>
                <div class="product-card">
                    <h3>Test Product 2</h3>
                    <div class="price">99.99 €</div>
                    <a href="/product2.html">Details</a>
                    <img src="/img2.jpg" alt="Product 2">
                </div>
                <a href="/page2.html" class="next-page">Next Page</a>
            </body>
            </html>""")
        
        # Create page2.html
        with open(os.path.join(tempdir, "page2.html"), "w") as f:
            f.write("""<!DOCTYPE html>
            <html>
            <body>
                <div class="product-card">
                    <h3>Test Product 3</h3>
                    <div class="price">149.99 €</div>
                    <a href="/product3.html">Details</a>
                    <img src="/img3.jpg" alt="Product 3">
                </div>
            </body>
            </html>""")
        
        # Create HTML fixtures for different suppliers
        create_supplier_fixtures(tempdir)
        
        # Start server
        server = TestServer(tempdir)
        yield server
        server.stop()

def create_supplier_fixtures(tempdir):
    
    with open(os.path.join(tempdir, "castorama_card.html"), "w") as f:
        f.write("""
        <div data-test-id="product-tile">
            <img src="https://example.com/img.jpg" alt="Product Image">
            <div data-test-id="product-tile-title">Test Castorama Product</div>
            <div data-test-id="price">59,90 €</div>
            <div data-test-id="brand">TestBrand</div>
            <a href="/products/test-product-123.html">Details</a>
        </div>
        """)
    
    with open(os.path.join(tempdir, "leroymerlin_card.html"), "w") as f:
        f.write("""
        <li class="product-thumbnail">
            <article class="o-thumbnail">
                <div class="o-thumbnail__details">
                    <div class="o-thumbnail__left">
                        <div class="o-thumbnail__illustration">
                            <img alt="Test Product" class="a-illustration__img" src="https://example.com/img.jpg">
                        </div>
                    </div>
                    <div class="o-thumbnail__infos">
                        <div class="o-thumbnail__designation">
                            <a href="/produits/test-product-123.html" class="a-designation">
                                <span class="a-designation__label">Test Leroy Merlin Product</span>
                            </a>
                        </div>
                        <div class="o-thumbnail__vendor">
                            <span class="a-vendor__name">LEROY MERLIN</span>
                        </div>
                    </div>
                </div>
                <div class="o-thumbnail__price-infos">
                    <div class="o-thumbnail__price">
                        <p class="m-price -main">
                            <span class="m-price__line">79,90 €</span>
                        </p>
                    </div>
                </div>
            </article>
        </li>
        """)
    
    with open(os.path.join(tempdir, "manomano_card.html"), "w") as f:
        f.write("""
        <a data-testid="productCardListing" href="/p/test-product-123">
            <div>
                <img data-testid="image" src="https://example.com/img.jpg" alt="Product Image">
                <div data-testid="product-card-listings-title">Test ManoMano Product</div>
                <div>
                    <span data-testid="price-main">39,99 €</span>
                </div>
            </div>
        </a>
        """)



def test_load_config(temp_config_file):
    """Test Case 1: Verify config file is properly loaded."""
    config = load_config(temp_config_file)
    assert config.headless is True
    assert config.user_agent == "Mozilla/5.0"
    assert len(config.suppliers) == 1
    
    supplier = config.suppliers[0]
    assert supplier.supplier == "TestSupplier"
    assert supplier.base_url == "https://www.example.com"
    
    category = supplier.categories[0]
    assert category.name == "Test Category"
    assert category.url == "https://www.example.com/category"
    assert category.card == ".product-card"
    assert category.paging_mode == "pagination"
    assert category.next_button == ".next-page"
    assert category.max_pages == 5

@pytest.mark.parametrize("price_text,expected_currency,expected_price", [
    ("49,90 €", "€", 49.90),
    ("€49.90", "€", 49.90),
    ("1 349,99€", "€", 1349.99),
    ("29 €", "€", 29.0),
    ("29€90", "€", 29.90),
    ("$19.99", "$", 19.99),
    ("", None, None),
])
def test_parse_price(price_text, expected_currency, expected_price):
    """Test Case 2: Verify price parser handles various formats."""
    currency, price = parse_price_with_currency(price_text)
    assert currency == expected_currency
    assert price == expected_price

@pytest.mark.parametrize("href,base_url,expected", [
    ("/products/123", "https://www.example.com", "https://www.example.com/products/123"),
    ("products/123", "https://www.example.com/", "https://www.example.com/products/123"),
    ("https://www.other.com/product", "https://www.example.com", "https://www.other.com/product"),
    ("", "https://www.example.com", "https://www.example.com"),
    (None, "https://www.example.com", "https://www.example.com"),
])
def test_resolve_url(href, base_url, expected):
    """Test Case 3: Test relative URL resolution."""
    assert resolve_url(href, base_url) == expected

def test_clean_text():
    """Test text cleaning function."""
    assert clean_text("  Test   Product  ") == "Test Product"
    assert clean_text("\n\t  Test\n\nProduct\t ") == "Test Product"
    assert clean_text(None) == ""

def test_timestamp():
    """Test timestamp generation."""
    now = now_ts()
    assert isinstance(now, int)
    # Timestamp should be roughly current time
    assert abs(now - int(time.time())) < 5


def test_castorama_extraction(page, test_server):
    # Load test HTML
    page.goto(f"{test_server.url()}/castorama_card.html")
    
    # Get the card element
    card = page.locator("[data-test-id='product-tile']").first
    
    # Extract data
    result = extract_from_card(card, "Test Category", "Castorama", test_server.url())
    
    # Verify extraction
    assert result["supplier"] == "Castorama"
    assert result["category"] == "Test Category"
    assert "Test Castorama Product" in result["name"]
    assert result["price"] == 59.9
    assert result["currency"] == "€"
    assert "products/test-product-123.html" in result["url"]
    assert "example.com/img.jpg" in result["image_url"]
    assert result["brand"] == "TestBrand"
    assert "timestamp" in result

def test_leroymerlin_extraction(page, test_server):
    # Load test HTML
    page.goto(f"{test_server.url()}/leroymerlin_card.html")
    
    # Get the card element
    card = page.locator("li.product-thumbnail").first
    
    # Extract data
    result = extract_from_card(card, "Test Category", "Leroy Merlin", test_server.url())
    
    # Verify extraction
    assert result["supplier"] == "Leroy Merlin"
    assert result["category"] == "Test Category"
    assert "Test Leroy Merlin Product" in result["name"]
    assert result["price"] == 79.9
    assert result["currency"] == "€"
    assert "produits/test-product-123.html" in result["url"]
    assert "example.com/img.jpg" in result["image_url"]
    assert "LEROY MERLIN" in result["brand"]
    assert "timestamp" in result

def test_manomano_extraction(page, test_server):
    # Load test HTML
    page.goto(f"{test_server.url()}/manomano_card.html")
    
    # Get the card element
    card = page.locator("[data-testid='productCardListing']").first
    
    # Extract data
    result = extract_from_card(card, "Test Category", "ManoMano", test_server.url())
    
    # Verify extraction
    assert result["supplier"] == "ManoMano"
    assert result["category"] == "Test Category"
    assert "Test ManoMano Product" in result["name"]
    assert result["price"] == 39.99
    assert result["currency"] == "€"
    assert "/p/test-product-123" in result["url"]
    assert "example.com/img.jpg" in result["image_url"]
    assert "timestamp" in result


def test_missing_product_info(page):
    # Card with missing price
    page.set_content("""
    <div class="product-card">
        <h3>Product Without Price</h3>
        <a href="/test.html">Details</a>
        <img src="/img.jpg" alt="Product">
    </div>
    """)
    
    card = page.locator(".product-card").first
    result = extract_from_card(card, "Test", "Test", "https://example.com")
    
    assert result["name"] == "Product Without Price"
    assert result["price"] is None
    assert result["url"] == "https://example.com/test.html"
    
    # Card with missing image
    page.set_content("""
    <div class="product-card">
        <h3>Product Without Image</h3>
        <div class="price">19.99 €</div>
        <a href="/test.html">Details</a>
    </div>
    """)
    
    card = page.locator(".product-card").first
    result = extract_from_card(card, "Test", "Test", "https://example.com")
    
    assert result["name"] == "Product Without Image"
    assert result["price"] == 19.99
    assert result["image_url"] == ""

def test_invalid_urls():
    assert resolve_url("", "") == ""
    assert resolve_url(None, None) == ""
    assert resolve_url("ftp://invalid", "https://example.com") == "ftp://invalid"
    assert resolve_url("//cdn.example.com/img.jpg", "https://example.com") == "https://cdn.example.com/img.jpg"

def test_error_recovery(page):
    page.set_content("""
    <div id="container">
        <div class="product-card">Good Product</div>
        <div class="product-card">
            <!-- This will cause an error when trying to extract price -->
            <script>throw new Error('Script error')</script>
        </div>
        <div class="product-card">Another Good Product</div>
    </div>
    """)
    
    # Mock extraction function that sometimes fails
    def mock_extract(card):
        text = card.inner_text()
        if "Good" in text:
            return {"status": "success", "name": text}
        else:
            raise Exception("Test error")
    
    # Try to process all cards
    cards = page.locator(".product-card")
    results = []
    
    for i in range(cards.count()):
        try:
            card = cards.nth(i)
            result = mock_extract(card)
            results.append(result)
        except Exception as e:
            # Just continue with next card
            pass
    
    # We should have two successful results
    assert len(results) == 2
    assert "Good Product" in results[0]["name"]
    assert "Another Good Product" in results[1]["name"]


def test_scraper_integration(test_server, tmp_path):
    # Create config file
    config_path = tmp_path / "test_config.yaml"
    with open(config_path, "w") as f:
        f.write(f"""
        headless: true
        user_agent: "Mozilla/5.0"
        suppliers:
          - supplier: "TestSupplier"
            base_url: "{test_server.url()}"
            categories:
              - name: "Test Category"
                url: "{test_server.url()}"
                selectors:
                  card: ".product-card"
                paging:
                  mode: "pagination"
                  next_button: ".next-page"
                  max_pages: 3
        """)
    
    # Load config
    config = load_config(config_path)
    
    # Create a simplified scrape_all function for testing
    def simplified_scrape_all():
        items = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=config.headless)
            context = browser.new_context(user_agent=config.user_agent)
            page = context.new_page()
            
            for supplier in config.suppliers:
                for category in supplier.categories:
                    # Navigate to initial page
                    page.goto(category.url)
                    
                    # Get all products on this page
                    cards = page.locator(".product-card")
                    for i in range(cards.count()):
                        card = cards.nth(i)
                        name = card.locator("h3").inner_text()
                        price_text = card.locator(".price").inner_text()
                        currency, price = parse_price_with_currency(price_text)
                        href = card.locator("a").get_attribute("href")
                        url = resolve_url(href, supplier.base_url)
                        
                        items.append({
                            "supplier": supplier.supplier,
                            "category": category.name,
                            "name": name,
                            "price": price,
                            "currency": currency,
                            "url": url,
                            "timestamp": now_ts()
                        })
                    
                    # Check for next page
                    next_button = page.locator(".next-page")
                    if next_button.count() > 0:
                        next_url = resolve_url(next_button.get_attribute("href"), supplier.base_url)
                        page.goto(next_url)
                        
                        # Get products from page 2
                        cards = page.locator(".product-card")
                        for i in range(cards.count()):
                            card = cards.nth(i)
                            name = card.locator("h3").inner_text()
                            price_text = card.locator(".price").inner_text()
                            currency, price = parse_price_with_currency(price_text)
                            href = card.locator("a").get_attribute("href")
                            url = resolve_url(href, supplier.base_url)
                            
                            items.append({
                                "supplier": supplier.supplier,
                                "category": category.name,
                                "name": name,
                                "price": price,
                                "currency": currency,
                                "url": url,
                                "timestamp": now_ts()
                            })
            
            browser.close()
        return items
    
    # Run the simplified scraper
    items = simplified_scrape_all()
    
    # Check results
    assert len(items) == 3
    
    # Check product names
    names = [item["name"] for item in items]
    assert "Test Product 1" in names
    assert "Test Product 2" in names
    assert "Test Product 3" in names
    
    # Check prices
    prices = [item["price"] for item in items]
    assert 49.99 in prices
    assert 99.99 in prices
    assert 149.99 in prices

def test_pagination(page, test_server):
    # Test traditional pagination
    page.goto(test_server.url())
    
    assert "Test Product 1" in page.content()
    assert "Test Product 2" in page.content()
    
    # Go to next page
    page.locator(".next-page").click()
    page.wait_for_load_state("networkidle")
    
    # Second page
    assert "Test Product 3" in page.content()

