## 🧱 Material Scraper
Multi-supplier Web Scraper for Construction & Renovation Materials

---

## 💡 Overview
A modular, category-aware scraping engine that collects structured product data 
from real suppliers such as Leroy Merlin, Castorama, and ManoMano, 
handling pagination, infinite scroll, and product variations.

---

# 🗂 Folder Structure
```
/material-scraper/
├── scraper.py                 # Main scraper orchestration
├── config/                    # Config & selectors
│   └── scraper_config.yaml
├── data/                      # Output data storage
│   └── materials.json
├── tests/                     # Unit & integration tests
│   └── test_scraper.py
├── README.md
└── requirements.txt
```

---

## 🛠 System Flow

1. **Supplier & Category Config**
             ↓ (load_config)
2. **Scraping Orchestration:**
i.  Category URL Navigation
             ↓
ii. Pagination / Infinite Scroll Handling
             ↓
iii. Product Card Extraction
             ↓
iv.  Field Parsing:
    Name
    Category
    Price + Currency
    Brand
    Unit / Pack Size
3. **Image URL**
         ↓
4. **Data Structuring & Deduplication**
         ↓
5. **Save to JSON / CSV**

---

## 🚀 Quick Start
**1. Create virtual environment and install dependencies**

- **Windows:**
  ```
  python -m venv venv
  venv\Scripts\activate
  pip install -r requirements.txt
  ```

- **Mac/Linux:**
  ```
  python -m venv venv
  source venv/bin/activate
  pip install -r requirements.txt
  ```
**2. Run the scraper:**
```
python scraper.py --config config/scraper_config.yaml
```

**3. Output:**
- Scraped data is saved to data/materials.json

**4. Run tests:**
```
pytest -q
```

---

## 📋 Output Format:
- Each item in materials.json contains:
{
  "supplier": "Castorama",
  "category": "Tiles",
  "name": "Porcelain Floor Tile 30x30cm",
  "price": 12.5,
  "currency": "€",
  "url": "https://www.castorama.fr/product/...",
  "brand": "MarbleCo",
  "unit": "m² / box (10 pcs)",
  "image_url": "https://cdn.castorama.fr/images/....jpg",
  "timestamp": 1690000000
}

---

## 📌 Assumptions & Edge Cases Handled
- Supplier-specific selectors via config
- Automatic handling of pagination, load-more, and infinite scroll
- Fallback selectors when primary selector fails
- Deduplication by (supplier, product_url, name, unit)
- Graceful skip of incomplete products with debug logging
- Configurable delays to avoid anti-bot detection
- Timestamped data for versioning
- Added Cronjob which can auto-sync monthly

---

## 📈 Scalability Roadmap
- Static selector config → Automatic selector discovery
- Static JSON → Database + API export
- Headless browser scraping → Hybrid requests + JS rendering
- Category URLs → Auto-category detection from site sitemap

---

## How to Evolve
- Integrate supplier APIs (when available) for faster, cleaner data
- Add NLP product normalization for brand & unit consistency
- Push data to analytics dashboards or BI tools
- Enable multi-threaded scraping for faster runs (with care for rate limits)

---

## 📌 One Real-World Trust Feature
Selector fallback & debug logging ensures minimal data loss 
and quick recovery when suppliers change their site layouts.