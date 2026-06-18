"""
Omni-Track Enterprise API v2.0
================================
Production-grade e-commerce & q-commerce inventory tracker.

Architecture:
  - Playwright Chromium (headless) with stealth for anti-bot bypass
  - Network interception for Q-commerce APIs (Blinkit, Zepto, Swiggy)
  - DOM parsing for E-commerce pages (Amazon, Flipkart)
  - asyncpg connection pool for PostgreSQL
  - Fuzzy matching via rapidfuzz for product name verification
"""

import asyncio
import os
import json
import random
import re
import io
import csv
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from rapidfuzz import fuzz
import uvicorn
import asyncpg
import pandas as pd
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright

# Stealth: try the pip package (v2.x API), fallback to manual injection
try:
    from playwright_stealth import Stealth
    _stealth = Stealth(
        navigator_languages_override=("en-IN", "en"),
        navigator_platform_override="Win32",
    )
    HAS_STEALTH_PKG = True
except ImportError:
    _stealth = None
    HAS_STEALTH_PKG = False

load_dotenv()

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("omni-track")

# ─── Configuration ────────────────────────────────────────────────────────────
DB_DSN = os.getenv("DB_DSN", "postgresql://vivaan:omni123@127.0.0.1:5432/omnitrack")
PROXY_URL = os.getenv("PROXY_URL", "")
MAX_CONCURRENT_PAGES = int(os.getenv("MAX_CONCURRENT_PAGES", "5"))
PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT_MS", "30000"))
MAX_RETRIES = 2
RATE_LIMIT_DELAY = (1.5, 3.5)  # seconds between same-platform requests
INTER_QUERY_DELAY = (2.0, 5.0)  # seconds between bulk queries

# ─── Hyper-Local Zone Config ──────────────────────────────────────────────────
PLATFORM_CONFIG = {
    "Delhi - Connaught Place": {"lat": 28.6304, "lon": 77.2177, "locality": "Connaught Place"},
    "Delhi - Vasant Kunj":     {"lat": 28.5293, "lon": 77.1533, "locality": "Vasant Kunj"},
    "Gurgaon - Sector 56":    {"lat": 28.4239, "lon": 77.1009, "locality": "Sector 56"},
    "Noida - Sector 62":      {"lat": 28.6258, "lon": 77.3788, "locality": "Sector 62"},
    "Mumbai - Powai":          {"lat": 19.1197, "lon": 72.9051, "locality": "Powai"},
    "Bangalore - Indiranagar": {"lat": 12.9784, "lon": 77.6408, "locality": "Indiranagar"},
    "Pune - Wakad":            {"lat": 18.6018, "lon": 73.7514, "locality": "Wakad"},
}

# ─── User-Agent Rotation Pool ────────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.6998.178 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]

# ─── Manual Stealth Script (fallback if playwright-stealth pip pkg not installed)
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {isInstalled: false} };
const _origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (p) => (
    p.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : _origQuery(p)
);
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        {name:'Chrome PDF Plugin', filename:'internal-pdf-viewer'},
        {name:'Chrome PDF Viewer', filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
        {name:'Native Client', filename:'internal-nacl-plugin'}
    ]
});
Object.defineProperty(navigator, 'languages', { get: () => ['en-IN','en-US','en'] });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
if(navigator.connection) Object.defineProperty(navigator.connection, 'rtt', { get: () => 50 });
"""


# ═══════════════════════════════════════════════════════════════════════════════
# BROWSER ENGINE — Manages Playwright Lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

class BrowserEngine:
    """Persistent Playwright browser with stealth capabilities."""

    def __init__(self):
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_PAGES)

    async def start(self):
        logger.info("🚀 Launching Playwright Chromium (headless)...")
        self._playwright = await async_playwright().start()
        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--disable-extensions",
            ],
        }
        if PROXY_URL:
            launch_args["proxy"] = {"server": PROXY_URL}
            logger.info(f"🔄 Proxy enabled: {PROXY_URL[:40]}...")
        self._browser = await self._playwright.chromium.launch(**launch_args)
        logger.info("✅ Browser engine ready.")

    async def stop(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("🛑 Browser engine stopped.")

    async def new_stealth_page(self, cookies: list = None, geolocation: dict = None) -> tuple:
        """Create a new browser context + page with stealth patches.
        Returns (page, context). Caller MUST close context when done.
        geolocation: optional dict with 'latitude' and 'longitude' for geo override.
        """
        ctx_kwargs = dict(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1920, "height": 1080},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            java_script_enabled=True,
            ignore_https_errors=True,
        )
        if geolocation:
            ctx_kwargs["geolocation"] = geolocation
            ctx_kwargs["permissions"] = ["geolocation"]

        ctx = await self._browser.new_context(**ctx_kwargs)
        if cookies:
            await ctx.add_cookies(cookies)

        page = await ctx.new_page()

        # Apply stealth
        if HAS_STEALTH_PKG and _stealth:
            await _stealth.apply_stealth_async(page)
        else:
            await ctx.add_init_script(STEALTH_JS)

        return page, ctx

    @property
    def semaphore(self):
        return self._semaphore


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def detect_query_type(query: str) -> str:
    """Detect if query is an Amazon ASIN, Flipkart FSN, or product name."""
    q = query.strip()
    # ASIN: starts with B followed by EXACTLY 9 alphanumeric chars (total 10)
    if re.match(r"^B[0-9A-Za-z]{9}$", q):
        return "asin"
    # FSN: 10-20 alphanumeric chars, case-insensitive
    # Note: FSNs CAN start with B (e.g. BWSGX8KRAKC5NFGR) — only exclude
    # exact 10-char B-strings which are ASINs (already caught above)
    if re.match(r"^[A-Za-z0-9]{10,20}$", q):
        # Must contain at least one digit to distinguish from plain English words
        if any(c.isdigit() for c in q):
            return "fsn"
        # Long alphanumeric strings (>=14 chars) are almost certainly IDs
        if len(q) >= 14:
            return "fsn"
    return "product_name"


async def safe_text(page: Page, selectors: list, default: str = "-") -> str:
    """Try multiple CSS selectors, return first match's text."""
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                text = await el.text_content()
                if text and text.strip():
                    return text.strip()
        except Exception:
            continue
    return default


def best_fuzzy_match(items: list, query: str, name_keys: list, threshold: int = 60) -> tuple:
    """Find the best fuzzy-matching item from a list.
    Uses weighted scoring: token_set (60%) + token_sort (20%) + partial (20%)
    to balance partial match capability with precision.
    Threshold at 60% — the weighted formula naturally caps genuine matches at
    70-88% due to extra words in product names (variants, sizes, brands).
    Returns (best_item, best_score) or (None, 0).
    """
    best_item, best_score = None, 0
    q = query.lower().strip()
    for item in items:
        name = ""
        for key in name_keys:
            name = item.get(key, "")
            if name:
                break
        if not name:
            continue
        n = name.lower().strip()
        # Weighted scoring for balanced precision + recall
        s_set = fuzz.token_set_ratio(q, n)
        s_sort = fuzz.token_sort_ratio(q, n)
        s_partial = fuzz.partial_ratio(q, n)
        score = (s_set * 0.6) + (s_sort * 0.2) + (s_partial * 0.2)
        if score > best_score and score > threshold:
            best_score = round(score)
            best_item = item
    return best_item, best_score


def _extract_products_recursive(data, results, depth=0, max_depth=10):
    """Recursively extract product-like dicts from nested JSON.
    Walks through any JSON structure (Zepto, Swiggy, etc.) and collects
    dicts that look like product entries (have a name + price/availability field).
    """
    if depth > max_depth:
        return
    if isinstance(data, dict):
        # Check if this dict looks like a product
        has_name = any(k in data for k in ["name", "product_name", "title", "display_name",
                                            "productName", "item_name", "itemName"])
        has_product_field = any(k in data for k in [
            "price", "mrp", "selling_price", "offer_price", "sellingPrice", "offerPrice",
            "available", "in_stock", "inventory", "inStock", "is_available", "isAvailable",
            "quantity", "product_id", "productId", "sku", "brand", "category",
        ])
        if has_name and has_product_field:
            results.append(data)
        # Recurse into values
        for v in data.values():
            _extract_products_recursive(v, results, depth + 1, max_depth)
    elif isinstance(data, list):
        for item in data:
            _extract_products_recursive(item, results, depth + 1, max_depth)



# ═══════════════════════════════════════════════════════════════════════════════
# PLATFORM SCRAPERS
# ═══════════════════════════════════════════════════════════════════════════════

# ────────────────────────────── BLINKIT ───────────────────────────────────────

async def scrape_blinkit(engine: BrowserEngine, query: str, zone: dict) -> dict:
    """Blinkit: navigate to search URL → intercept API JSON → fuzzy match."""
    result = {"uploaded_query": query, "platform": "Blinkit",
              "matched_product": "-", "status": "not_found", "price": "-"}
    ctx = None
    try:
        async with engine.semaphore:
            cookies = [
                {"name": "gr_1_lat", "value": str(zone["lat"]), "domain": ".blinkit.com", "path": "/"},
                {"name": "gr_1_lon", "value": str(zone["lon"]), "domain": ".blinkit.com", "path": "/"},
                {"name": "gr_1_locality", "value": zone.get("locality", ""), "domain": ".blinkit.com", "path": "/"},
            ]
            page, ctx = await engine.new_stealth_page(cookies=cookies)

            captured: list = []

            async def on_response(resp):
                try:
                    url = resp.url
                    if any(k in url for k in ["search", "layout", "products"]) and "blinkit" in url:
                        if resp.status == 200 and "json" in resp.headers.get("content-type", ""):
                            captured.append(await resp.json())
                except Exception:
                    pass

            page.on("response", on_response)

            logger.info(f"  🟡 [Blinkit] Searching: {query}")
            await page.goto(
                f"https://blinkit.com/s/?q={quote(query)}",
                wait_until="domcontentloaded", timeout=45000
            )
            await page.wait_for_timeout(random.randint(4000, 6000))

            # ── Parse intercepted JSON ──
            if captured:
                all_items = []
                for data in captured:
                    if not isinstance(data, dict):
                        continue

                    # Current Blinkit structure (2026):
                    # response.snippets[] → each snippet is a product card
                    # snippet.data.name = {text: "Product Name", ...}
                    # snippet.data.normal_price = {text: "₹30", ...}
                    # snippet.data.inventory = integer
                    # snippet.widget_type = "product_card_snippet_type_2"
                    snippets = data.get("response", {}).get("snippets", [])
                    for snip in snippets:
                        widget_type = snip.get("widget_type", "")
                        if "product_card" not in widget_type:
                            continue
                        sd = snip.get("data", {})
                        if not sd:
                            continue

                        # Extract name text from nested dict or string
                        raw_name = sd.get("name", "")
                        if isinstance(raw_name, dict):
                            name = raw_name.get("text", "")
                        else:
                            name = str(raw_name)

                        # Extract price text
                        raw_price = sd.get("normal_price", sd.get("price", ""))
                        if isinstance(raw_price, dict):
                            price_text = raw_price.get("text", "-")
                        else:
                            price_text = str(raw_price) if raw_price else "-"

                        # Extract variant
                        raw_variant = sd.get("variant", "")
                        if isinstance(raw_variant, dict):
                            variant = raw_variant.get("text", "")
                        else:
                            variant = str(raw_variant) if raw_variant else ""

                        inv = sd.get("inventory", 0)

                        all_items.append({
                            "name": f"{name} {variant}".strip() if variant else name,
                            "name_only": name,
                            "price": price_text,
                            "inventory": inv,
                        })

                    # Legacy fallback: old widget-based structure
                    widgets = (data.get("data", {}).get("layout", {}).get("widgets", [])
                               or data.get("data", {}).get("widgets", []))
                    for w in widgets:
                        items = (w.get("data", {}).get("items", [])
                                 or w.get("items", [])
                                 or w.get("products", []))
                        all_items.extend(items)

                    # Deep recursive fallback: extract any product-like dicts
                    # from the entire API response tree
                    if not all_items:
                        _extract_products_recursive(data, all_items)

                logger.info(f"  🟡 [Blinkit] Extracted {len(all_items)} product items from {len(captured)} responses")

                # Use lower threshold (45) for Blinkit — product names often
                # include variant/size info that inflates the fuzzy distance
                best, score = best_fuzzy_match(
                    all_items, query, ["name", "name_only", "product_name"], threshold=45
                )
                if best:
                    inv = best.get("inventory", best.get("in_stock", best.get("available", 0)))
                    price_val = best.get("price", best.get("selling_price", best.get("mrp", "-")))
                    try:
                        is_in_stock = int(str(inv)) > 0 if inv is not None else False
                    except (ValueError, TypeError):
                        is_in_stock = bool(inv)
                    result = {
                        "uploaded_query": query, "platform": "Blinkit",
                        "matched_product": best.get("name", best.get("product_name", "-")),
                        "status": "instock" if is_in_stock else "oos",
                        "price": str(price_val).strip() if price_val and str(price_val) != "-" else "-",
                        "match_score": round(score),
                    }
                else:
                    result["matched_product"] = "No matching product in API response"
            else:
                # ── Fallback: check if WAF blocked or parse DOM ──
                html = await page.content()
                if any(k in html.lower() for k in ["challenge", "captcha", "cf-browser"]):
                    result["status"] = "cloudflare_blocked"
                    result["matched_product"] = "WAF/Cloudflare challenge detected"
                else:
                    result = await _blinkit_dom_fallback(page, query, result)

    except Exception as e:
        result["status"] = "error"
        result["matched_product"] = f"Error: {str(e)[:200]}"
        logger.error(f"  ❌ [Blinkit] {query}: {e}")
    finally:
        if ctx:
            try: await ctx.close()
            except: pass
    return result


async def _blinkit_dom_fallback(page: Page, query: str, result: dict) -> dict:
    """Parse Blinkit rendered product cards when network interception fails."""
    try:
        cards = await page.query_selector_all(
            "[data-testid='plp-product'], .Product__Container, .plp-product, div[class*='Product__']"
        )
        best_name, best_score, best_price = None, 0, "-"
        for card in cards[:10]:
            name_el = await card.query_selector(
                "[class*='Product__UpdatedTitle'], [class*='plp-product__name'], div[class*='UpdatedTitle']"
            )
            price_el = await card.query_selector(
                "[class*='Product__UpdatedPrice'], [class*='plp-product__price'], div[class*='Price']"
            )
            if name_el:
                name = (await name_el.text_content() or "").strip()
                score = (fuzz.token_set_ratio(query.lower(), name.lower()) * 0.6 + fuzz.token_sort_ratio(query.lower(), name.lower()) * 0.2 + fuzz.partial_ratio(query.lower(), name.lower()) * 0.2)
                if score > best_score and score > 45:
                    best_score = score
                    best_name = name
                    best_price = ((await price_el.text_content()) or "-").strip() if price_el else "-"

        if best_name:
            result.update({
                "matched_product": best_name, "status": "instock",
                "price": best_price if "₹" in best_price else f"₹{best_price}",
                "match_score": round(best_score),
            })
        else:
            result["matched_product"] = "No results rendered on page"
    except Exception as e:
        logger.warning(f"  ⚠️ [Blinkit] DOM fallback error: {e}")
    return result


# ────────────────────────────── ZEPTO ─────────────────────────────────────────

async def scrape_zepto(engine: BrowserEngine, query: str, zone: dict) -> dict:
    """Zepto: set location cookies → navigate to search → intercept API → fuzzy match."""
    result = {"uploaded_query": query, "platform": "Zepto",
              "matched_product": "-", "status": "not_found", "price": "-"}
    ctx = None
    try:
        async with engine.semaphore:
            # Zepto location cookies
            cookies = [
                {"name": "user_lat", "value": str(zone["lat"]), "domain": ".zeptonow.com", "path": "/"},
                {"name": "user_lng", "value": str(zone["lon"]), "domain": ".zeptonow.com", "path": "/"},
                {"name": "latitude", "value": str(zone["lat"]), "domain": ".zeptonow.com", "path": "/"},
                {"name": "longitude", "value": str(zone["lon"]), "domain": ".zeptonow.com", "path": "/"},
                {"name": "user_locality", "value": zone.get("locality", ""), "domain": ".zeptonow.com", "path": "/"},
            ]
            geolocation = {"latitude": zone["lat"], "longitude": zone["lon"]}
            page, ctx = await engine.new_stealth_page(cookies=cookies, geolocation=geolocation)

            # Inject localStorage with location data before navigation
            await page.add_init_script(f"""
                try {{
                    localStorage.setItem('user_lat', '{zone["lat"]}');
                    localStorage.setItem('user_lng', '{zone["lon"]}');
                    localStorage.setItem('latitude', '{zone["lat"]}');
                    localStorage.setItem('longitude', '{zone["lon"]}');
                    localStorage.setItem('user_locality', '{zone.get("locality", "")}');
                    localStorage.setItem('userAddress', JSON.stringify({{
                        lat: {zone["lat"]}, lng: {zone["lon"]},
                        locality: '{zone.get("locality", "")}'
                    }}));
                }} catch(e) {{}}
            """)

            captured: list = []

            async def on_response(resp):
                try:
                    url = resp.url
                    if "zepto" in url:
                        ct = resp.headers.get("content-type", "")
                        if resp.status == 200 and ("json" in ct or "application" in ct):
                            try:
                                body = await resp.json()
                                captured.append(body)
                            except Exception:
                                pass
                except Exception:
                    pass

            page.on("response", on_response)
            logger.info(f"  🟣 [Zepto] Searching: {query}")

            # First navigate to homepage to set location context
            try:
                await page.goto("https://www.zeptonow.com/", wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(random.randint(2000, 3000))
            except Exception:
                pass  # Homepage may timeout but cookies are set

            # Now navigate to search
            await page.goto(
                f"https://www.zeptonow.com/search?query={quote(query)}",
                wait_until="domcontentloaded", timeout=45000
            )
            await page.wait_for_timeout(random.randint(4000, 6000))

            logger.info(f"  🟣 [Zepto] Captured {len(captured)} API responses")
            if captured:
                all_products = []
                for data in captured:
                    if not isinstance(data, dict):
                        continue
                    # Recursively extract product-like dicts from any JSON structure
                    _extract_products_recursive(data, all_products)

                logger.info(f"  🟣 [Zepto] Extracted {len(all_products)} product items")

                # Use lower threshold (45) for q-commerce — product names include
                # brand + variant info that increases fuzzy distance
                best, score = best_fuzzy_match(
                    all_products, query, ["name", "product_name", "title",
                                          "display_name", "productName", "item_name"],
                    threshold=45
                )
                if best:
                    avail = best.get("available", best.get("in_stock",
                                best.get("inventory", best.get("inStock",
                                best.get("is_available", True)))))
                    price_val = best.get("price", best.get("selling_price",
                                    best.get("sellingPrice", best.get("mrp",
                                    best.get("offer_price", best.get("offerPrice", "-"))))))
                    result = {
                        "uploaded_query": query, "platform": "Zepto",
                        "matched_product": best.get("name", best.get("product_name",
                                              best.get("display_name", best.get("title", "-")))),
                        "status": "instock" if avail else "oos",
                        "price": f"₹{price_val}" if price_val and str(price_val) != "-" else "-",
                        "match_score": round(score),
                    }
            else:
                # DOM fallback
                result = await _qcommerce_dom_fallback(page, query, "Zepto", result)

    except Exception as e:
        result["status"] = "error"
        result["matched_product"] = f"Error: {str(e)[:200]}"
        logger.error(f"  ❌ [Zepto] {query}: {e}")
    finally:
        if ctx:
            try: await ctx.close()
            except: pass
    return result


# ────────────────────────── SWIGGY INSTAMART ──────────────────────────────────

async def scrape_swiggy(engine: BrowserEngine, query: str, zone: dict) -> dict:
    """Swiggy Instamart: set location cookies → navigate to search → intercept API → fuzzy match."""
    result = {"uploaded_query": query, "platform": "Swiggy Instamart",
              "matched_product": "-", "status": "not_found", "price": "-"}
    ctx = None
    try:
        async with engine.semaphore:
            # Swiggy location cookies (required for Instamart to show products)
            cookies = [
                {"name": "lat", "value": str(zone["lat"]), "domain": ".swiggy.com", "path": "/"},
                {"name": "lng", "value": str(zone["lon"]), "domain": ".swiggy.com", "path": "/"},
                {"name": "userLat", "value": str(zone["lat"]), "domain": ".swiggy.com", "path": "/"},
                {"name": "userLng", "value": str(zone["lon"]), "domain": ".swiggy.com", "path": "/"},
                {"name": "address", "value": zone.get("locality", ""), "domain": ".swiggy.com", "path": "/"},
                {"name": "addressId", "value": "auto_generated", "domain": ".swiggy.com", "path": "/"},
            ]
            geolocation = {"latitude": zone["lat"], "longitude": zone["lon"]}
            page, ctx = await engine.new_stealth_page(cookies=cookies, geolocation=geolocation)

            # Inject localStorage for Swiggy's SPA location detection
            await page.add_init_script(f"""
                try {{
                    localStorage.setItem('lat', '{zone["lat"]}');
                    localStorage.setItem('lng', '{zone["lon"]}');
                    localStorage.setItem('address', JSON.stringify({{
                        lat: {zone["lat"]}, lng: {zone["lon"]},
                        address: '{zone.get("locality", "")}',
                        annotation: '{zone.get("locality", "")}'
                    }}));
                }} catch(e) {{}}
            """)

            captured: list = []

            async def on_response(resp):
                try:
                    url = resp.url
                    if "swiggy" in url:
                        ct = resp.headers.get("content-type", "")
                        if resp.status == 200 and ("json" in ct or "application" in ct):
                            try:
                                body = await resp.json()
                                captured.append(body)
                            except Exception:
                                pass
                except Exception:
                    pass

            page.on("response", on_response)
            logger.info(f"  🟠 [Swiggy] Searching: {query}")

            # First navigate to Instamart homepage to establish location session
            try:
                await page.goto("https://www.swiggy.com/instamart", wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(random.randint(2000, 3000))
            except Exception:
                pass  # May timeout but location cookies are set

            # Now navigate to search
            await page.goto(
                f"https://www.swiggy.com/instamart/search?custom_back=true&query={quote(query)}",
                wait_until="domcontentloaded", timeout=45000
            )
            await page.wait_for_timeout(random.randint(4000, 6000))

            logger.info(f"  🟠 [Swiggy] Captured {len(captured)} API responses")
            if captured:
                all_products = []
                for data in captured:
                    if not isinstance(data, dict):
                        continue
                    # Recursively extract product-like dicts from any JSON structure
                    _extract_products_recursive(data, all_products)

                logger.info(f"  🟠 [Swiggy] Extracted {len(all_products)} product items")

                # Use lower threshold (45) for q-commerce
                best, score = best_fuzzy_match(
                    all_products, query, ["name", "display_name", "product_name",
                                          "productName", "title", "item_name"],
                    threshold=45
                )
                if best:
                    avail = best.get("available", best.get("in_stock",
                                best.get("inventory", best.get("inStock",
                                best.get("is_available", True)))))
                    price_val = best.get("price", best.get("offer_price",
                                    best.get("offerPrice", best.get("mrp",
                                    best.get("selling_price", best.get("sellingPrice", "-"))))))
                    result = {
                        "uploaded_query": query, "platform": "Swiggy Instamart",
                        "matched_product": best.get("name", best.get("display_name",
                                              best.get("product_name", best.get("title", "-")))),
                        "status": "instock" if avail else "oos",
                        "price": f"₹{price_val}" if price_val and str(price_val) != "-" else "-",
                        "match_score": round(score),
                    }
            else:
                result = await _qcommerce_dom_fallback(page, query, "Swiggy Instamart", result)

    except Exception as e:
        result["status"] = "error"
        result["matched_product"] = f"Error: {str(e)[:200]}"
        logger.error(f"  ❌ [Swiggy] {query}: {e}")
    finally:
        if ctx:
            try: await ctx.close()
            except: pass
    return result


async def _qcommerce_dom_fallback(page: Page, query: str, platform: str, result: dict) -> dict:
    """Generic DOM fallback for q-commerce search results."""
    try:
        cards = await page.query_selector_all(
            "[class*='product' i], [class*='Product'], [data-testid*='product']"
        )
        for card in cards[:8]:
            text = (await card.text_content() or "").strip()
            if len(text) > 5:
                score = (fuzz.token_set_ratio(query.lower(), text[:100].lower()) * 0.6 + fuzz.token_sort_ratio(query.lower(), text[:100].lower()) * 0.2 + fuzz.partial_ratio(query.lower(), text[:100].lower()) * 0.2)
                if score > 45:
                    result.update({
                        "matched_product": text[:200], "status": "instock", "match_score": round(score)
                    })
                    break

        page_text = (await page.text_content("body") or "").lower()
        if any(p in page_text for p in ["no results", "no products", "sorry", "not found"]):
            result["status"] = "not_found"
            result["matched_product"] = "No results found on platform"
    except Exception:
        pass
    return result


# ────────────────────────────── AMAZON ────────────────────────────────────────

async def scrape_amazon(engine: BrowserEngine, query: str, pincode: str) -> dict:
    """Amazon India: ASIN → product page, or search → best match. DOM-based."""
    result = {"uploaded_query": query, "platform": "Amazon",
              "matched_product": "-", "status": "not_found", "price": "-"}
    qtype = detect_query_type(query)
    ctx = None
    try:
        async with engine.semaphore:
            cookies = [
                {"name": "lc-acbin", "value": pincode, "domain": ".amazon.in", "path": "/"},
            ]
            page, ctx = await engine.new_stealth_page(cookies=cookies)

            if qtype == "asin":
                url = f"https://www.amazon.in/dp/{query.strip()}"
            else:
                url = f"https://www.amazon.in/s?k={quote(query)}"

            logger.info(f"  🔵 [Amazon] {'ASIN' if qtype == 'asin' else 'Search'}: {query}")
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await page.wait_for_timeout(random.randint(2500, 4500))

            # CAPTCHA check
            captcha = await page.query_selector("#captchacharacters, form[action*='validateCaptcha']")
            if captcha:
                result["status"] = "cloudflare_blocked"
                result["matched_product"] = "Amazon CAPTCHA challenge"
                return result

            if qtype == "asin" or "/dp/" in page.url:
                result = await _parse_amazon_product(page, query, pincode)
                # ASIN direct lookup → always 100% match if product found
                if result.get("matched_product", "-") != "-":
                    result["match_score"] = 100
            else:
                result = await _parse_amazon_search(page, query)

    except Exception as e:
        result["status"] = "error"
        result["matched_product"] = f"Error: {str(e)[:200]}"
        logger.error(f"  ❌ [Amazon] {query}: {e}")
    finally:
        if ctx:
            try: await ctx.close()
            except: pass
    return result


async def _parse_amazon_product(page: Page, query: str, pincode: str) -> dict:
    """Parse Amazon product page for title, price, availability."""
    result = {"uploaded_query": query, "platform": "Amazon",
              "matched_product": "-", "status": "not_found", "price": "-"}

    # Title
    title = await safe_text(page, ["#productTitle", "#title span", "h1#title span"])
    result["matched_product"] = title[:250] if title != "-" else "-"

    # Price
    price = await safe_text(page, [
        "#corePrice_feature_div span.a-price-whole",
        "span.a-price-whole",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#apex_offerDisplay_desktop span.a-price-whole",
    ])
    if price != "-":
        result["price"] = f"₹{price.replace(',', '').replace('.', '').strip()}"

    # Availability — multiple signals
    avail_text = (await safe_text(page, [
        "#availability span", "#availability", "#outOfStock span",
    ])).lower()

    add_to_cart = await page.query_selector("#add-to-cart-button, #submit.add-to-cart-ubb-ibb")
    buy_now = await page.query_selector("#buy-now-button")

    if add_to_cart or buy_now:
        result["status"] = "instock"
    elif "in stock" in avail_text or "available" in avail_text:
        result["status"] = "instock"
    elif "out of stock" in avail_text or "unavailable" in avail_text or "currently unavailable" in avail_text:
        result["status"] = "oos"
    else:
        # Secondary: check delivery message
        delivery = await safe_text(page, [
            "#deliveryMessage_feature_div", "#mir-layout-DELIVERY_BLOCK",
            "#ddmDeliveryMessage", "#deliveryBlockMessage",
        ])
        if delivery != "-" and any(w in delivery.lower() for w in ["deliver", "free", "arrive"]):
            result["status"] = "instock"
            result["delivery_info"] = delivery[:200]
        else:
            result["status"] = "oos"

    # Try setting pincode for delivery check
    try:
        pin_input = await page.query_selector("#GLUXZipUpdateInput")
        if pin_input:
            await pin_input.fill("")
            await pin_input.type(pincode, delay=50)
            apply_btn = await page.query_selector(
                "#GLUXZipUpdate input[type='submit'], #GLUXZipUpdate .a-button-input"
            )
            if apply_btn:
                await apply_btn.click()
                await page.wait_for_timeout(2500)
                delivery = await safe_text(page, [
                    "#ddmDeliveryMessage", "#deliveryMessage_feature_div",
                    "#mir-layout-DELIVERY_BLOCK",
                ])
                if delivery != "-":
                    result["delivery_info"] = delivery[:200]
                    if "deliver" in delivery.lower():
                        result["status"] = "instock"
    except Exception:
        pass

    return result


async def _parse_amazon_search(page: Page, query: str) -> dict:
    """Parse Amazon search results using JS evaluation for robust name extraction.
    Amazon's current DOM puts only brand in h2, full name needs deeper extraction."""
    result = {"uploaded_query": query, "platform": "Amazon",
              "matched_product": "-", "status": "not_found", "price": "-"}
    try:
        # Use JS to extract product data from cards — more robust than CSS selectors
        products = await page.evaluate("""
            () => {
                const cards = document.querySelectorAll('[data-asin]:not([data-asin=""])');
                const results = [];
                for (let i = 0; i < Math.min(15, cards.length); i++) {
                    const card = cards[i];
                    const asin = card.getAttribute('data-asin');
                    if (!asin || asin.length < 5) continue;
                    
                    // Get product name: try multiple strategies
                    let name = '';
                    // Strategy 1: h2 > a full inner text (may contain full title or just brand)
                    const h2a = card.querySelector('h2 a');
                    if (h2a) name = h2a.innerText.trim();
                    // Strategy 2: If name too short, get the longest text from card links
                    if (name.length < 15) {
                        const links = card.querySelectorAll('a[href*="/dp/"], a[href*="/gp/"]');
                        links.forEach(a => {
                            const t = a.innerText.trim();
                            if (t.length > name.length) name = t;
                        });
                    }
                    // Strategy 3: Aria label
                    if (name.length < 15) {
                        const ariaEl = card.querySelector('[aria-label]');
                        if (ariaEl) {
                            const aria = ariaEl.getAttribute('aria-label');
                            if (aria && aria.length > name.length) name = aria;
                        }
                    }
                    // Strategy 4: img alt text
                    if (name.length < 15) {
                        const img = card.querySelector('img[alt]');
                        if (img) {
                            const alt = img.getAttribute('alt');
                            if (alt && alt.length > name.length) name = alt;
                        }
                    }
                    
                    if (!name || name.length < 3) continue;
                    
                    // Get price
                    const priceEl = card.querySelector('span.a-price-whole');
                    const price = priceEl ? priceEl.innerText.replace(/[,.]/g, '').trim() : '-';
                    
                    results.push({name: name.substring(0, 300), price, asin});
                }
                return results;
            }
        """)

        logger.info(f"  🔵 [Amazon] Extracted {len(products)} product cards from search")

        # Lower threshold to 45 for product name searches — Amazon product
        # titles are long and include brand, variant, size which inflates distance
        best_data, best_score = None, 0
        for p in products:
            q, n = query.lower(), p["name"].lower()
            score = (fuzz.token_set_ratio(q, n) * 0.6 + fuzz.token_sort_ratio(q, n) * 0.2 + fuzz.partial_ratio(q, n) * 0.2)
            if score > best_score and score > 45:
                best_score = score
                best_data = p

        if best_data:
            result.update({
                "matched_product": best_data["name"][:250],
                "price": f"₹{best_data['price']}" if best_data["price"] != "-" else "-",
                "status": "instock",
                "match_score": round(best_score),
            })
        else:
            # CSS selector fallback if JS evaluate returned empty
            if not products:
                logger.info("  🔵 [Amazon] JS evaluate empty, trying CSS fallback...")
                try:
                    fallback_items = []
                    cards = await page.query_selector_all(
                        "div.s-result-item[data-asin]:not([data-asin='']), "
                        "div[data-component-type='s-search-result']"
                    )
                    for card in cards[:15]:
                        name_el = await card.query_selector(
                            "h2 a span, h2 span, h2 a, "
                            "span[class*='a-text-normal'], img[alt]"
                        )
                        price_el = await card.query_selector("span.a-price-whole")
                        if name_el:
                            tag = await name_el.evaluate("el => el.tagName")
                            if tag == "IMG":
                                name = await name_el.get_attribute("alt") or ""
                            else:
                                name = (await name_el.text_content() or "").strip()
                            price = (await price_el.text_content() or "-").replace(",", "").replace(".", "").strip() if price_el else "-"
                            if name and len(name) > 5:
                                fallback_items.append({"name": name[:300], "price": price})

                    for p in fallback_items:
                        q, n = query.lower(), p["name"].lower()
                        score = (fuzz.token_set_ratio(q, n) * 0.6 + fuzz.token_sort_ratio(q, n) * 0.2 + fuzz.partial_ratio(q, n) * 0.2)
                        if score > best_score and score > 45:
                            best_score = score
                            best_data = p

                    if best_data:
                        result.update({
                            "matched_product": best_data["name"][:250],
                            "price": f"₹{best_data['price']}" if best_data["price"] != "-" else "-",
                            "status": "instock",
                            "match_score": round(best_score),
                        })
                    else:
                        result["matched_product"] = "No matching product in search results"
                except Exception as fe:
                    logger.warning(f"  ⚠️ [Amazon] CSS fallback error: {fe}")
                    result["matched_product"] = "No matching product in search results"
            else:
                result["matched_product"] = "No matching product in search results"
    except Exception as e:
        logger.warning(f"  ⚠️ [Amazon] Search parse error: {e}")
    return result


# ────────────────────────────── FLIPKART ──────────────────────────────────────

async def scrape_flipkart(engine: BrowserEngine, query: str, pincode: str, fk_type: str) -> dict:
    """Flipkart: FSN → product page, or search → best match. DOM-based.
    Sets pincode cookie for delivery context."""
    result = {"uploaded_query": query, "platform": fk_type,
              "matched_product": "-", "status": "not_found", "price": "-"}
    qtype = detect_query_type(query)
    ctx = None
    try:
        async with engine.semaphore:
            # Set Flipkart pincode cookie for delivery location
            cookies = [
                {"name": "vw", "value": pincode, "domain": ".flipkart.com", "path": "/"},
                {"name": "dl-pincode", "value": pincode, "domain": ".flipkart.com", "path": "/"},
            ]
            page, ctx = await engine.new_stealth_page(cookies=cookies)

            if qtype == "fsn":
                # FSN search needs marketplace-specific URLs for Grocery/Minutes
                if fk_type == "Flipkart Grocery":
                    url = f"https://www.flipkart.com/grocery-supermart-store?q={quote(query.strip())}"
                elif fk_type == "Flipkart Minutes":
                    url = f"https://www.flipkart.com/search?q={quote(query.strip())}&marketplace=FLIPKART_MINUTES"
                else:
                    url = f"https://www.flipkart.com/search?q={quote(query.strip())}"
            elif fk_type == "Flipkart Grocery":
                url = f"https://www.flipkart.com/grocery-supermart-store?q={quote(query)}"
            elif fk_type == "Flipkart Minutes":
                url = f"https://www.flipkart.com/search?q={quote(query)}&marketplace=FLIPKART_MINUTES"
            else:
                url = f"https://www.flipkart.com/search?q={quote(query)}"

            logger.info(f"  🔷 [{fk_type}] {'FSN' if qtype == 'fsn' else 'Search'}: {query}")
            await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            await page.wait_for_timeout(random.randint(2500, 4500))

            # Dismiss login popup
            try:
                close = await page.query_selector(
                    "button._2KpZ6l._2doB4z, button[class*='close'], span.QqFHMw, "
                    "button:has-text('✕'), div[class*='_30XB9F'] button"
                )
                if close:
                    await close.click()
                    await page.wait_for_timeout(500)
            except Exception:
                pass

            if qtype == "fsn":
                # For FSN: try to click first search result → product page
                result = await _parse_flipkart_fsn(page, query, pincode, fk_type)
            else:
                result = await _parse_flipkart_search(page, query, pincode, fk_type)

    except Exception as e:
        result["status"] = "error"
        result["matched_product"] = f"Error: {str(e)[:200]}"
        logger.error(f"  ❌ [{fk_type}] {query}: {e}")
    finally:
        if ctx:
            try: await ctx.close()
            except: pass
    return result


async def _parse_flipkart_product(page: Page, query: str, pincode: str, fk_type: str) -> dict:
    """Parse Flipkart product page for title, price, availability + pincode check."""
    result = {"uploaded_query": query, "platform": fk_type,
              "matched_product": "-", "status": "not_found", "price": "-"}

    # Title (Flipkart changes classnames — try many selectors)
    title = await safe_text(page, [
        "span.VU-ZEz", "span.B_NuCI", "h1._9E25nV", "h1 span",
        "span[class*='VU-ZEz']", "span[class*='B_NuCI']",
    ])
    result["matched_product"] = title[:250] if title != "-" else "-"

    # Price
    price = await safe_text(page, [
        "div.Nx9bqj.CxhGGd", "div._30jeq3._16Jk6d", "div._30jeq3",
        "div.Nx9bqj", "div[class*='CxhGGd']",
    ])
    if price != "-":
        result["price"] = price.strip()

    # Availability: check Add to Cart / Sold Out
    sold_out = await page.query_selector(
        "div._16FRp0, div:has-text('Sold Out'), div:has-text('Currently out of stock'), "
        "div[class*='_16FRp0']"
    )
    add_to_cart = await page.query_selector(
        "button:has-text('Add to Cart'), button:has-text('ADD TO CART'), "
        "button:has-text('Buy Now'), button:has-text('BUY NOW'), "
        "button._2KpZ6l._2U9uOA"
    )

    if sold_out:
        result["status"] = "oos"
    elif add_to_cart:
        result["status"] = "instock"
    else:
        result["status"] = "oos"

    # Pincode delivery check
    try:
        pin_input = await page.query_selector(
            "input#pincodeInputId, input[class*='_36yFo0'], input[placeholder*='pincode' i], "
            "input[placeholder*='Enter Delivery Pincode' i]"
        )
        if pin_input:
            await pin_input.click()
            await pin_input.fill("")
            await pin_input.type(pincode, delay=60)
            check_btn = await page.query_selector(
                "span._2P_LDn, span:has-text('Check'), button:has-text('Check'), "
                "span[class*='_2P_LDn']"
            )
            if check_btn:
                await check_btn.click()
                await page.wait_for_timeout(3000)
                delivery = await safe_text(page, [
                    "div._3XINqE", "div.XQDdHH", "div[class*='_1dVbu9']",
                    "span[class*='_2KBcv8']", "div[class*='XQDdHH']",
                ])
                if delivery != "-":
                    if any(w in delivery.lower() for w in ["deliver", "arrive", "free delivery", "get it by"]):
                        result["status"] = "instock"
                        result["delivery_info"] = delivery[:200]
                    elif any(w in delivery.lower() for w in ["not deliver", "unavailable", "sold out", "cannot"]):
                        result["status"] = "oos"
    except Exception as e:
        logger.warning(f"  ⚠️ [{fk_type}] Pincode check error: {e}")

    return result


async def _parse_flipkart_fsn(page: Page, query: str, pincode: str, fk_type: str) -> dict:
    """FSN lookup: search by FSN code → click first result → parse product page.
    Since this is a direct ID lookup, match_score is 100% if product is found."""
    result = {"uploaded_query": query, "platform": fk_type,
              "matched_product": "-", "status": "not_found", "price": "-"}
    try:
        # Check if search returned results — try clicking first product link
        first_product = await page.query_selector(
            "div[data-id] a[href*='/p/'], "
            "a.CGtC98, a.s1Q9rs, a.IRpwTa, a.WKTcLC, "
            "div[data-id] a[href*='flipkart.com']"
        )
        if first_product:
            href = await first_product.get_attribute("href")
            if href:
                product_url = href if href.startswith("http") else f"https://www.flipkart.com{href}"
                await page.goto(product_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                await page.wait_for_timeout(random.randint(2000, 3500))

                # Dismiss login popup again
                try:
                    close = await page.query_selector(
                        "button._2KpZ6l._2doB4z, button[class*='close'], span.QqFHMw, "
                        "button:has-text('✕'), div[class*='_30XB9F'] button"
                    )
                    if close:
                        await close.click()
                        await page.wait_for_timeout(500)
                except Exception:
                    pass

                # Parse the product page
                result = await _parse_flipkart_product(page, query, pincode, fk_type)
                # FSN direct lookup → always 100% match if product found
                if result.get("matched_product", "-") != "-":
                    result["match_score"] = 100
                return result

        # Fallback: try to parse search results directly if no clickable product
        result = await _parse_flipkart_search(page, query, pincode, fk_type)
        # For FSN search fallback, if found, set score to 100%
        if result.get("status") in ("instock", "oos"):
            result["match_score"] = 100
    except Exception as e:
        logger.warning(f"  ⚠️ [{fk_type}] FSN parse error: {e}")

    return result


async def _parse_flipkart_search(page: Page, query: str, pincode: str, fk_type: str) -> dict:
    """Parse Flipkart search results using JS evaluation + CSS selectors.
    Current Flipkart selectors (2026): div[data-id] cards, div.RG5Slk name, div.hZ3P6w price."""
    result = {"uploaded_query": query, "platform": fk_type,
              "matched_product": "-", "status": "not_found", "price": "-"}
    try:
        # Use JS evaluation for robust extraction
        products = await page.evaluate("""
            () => {
                const cards = document.querySelectorAll('div[data-id]');
                const results = [];
                for (let i = 0; i < Math.min(15, cards.length); i++) {
                    const card = cards[i];
                    const dataId = card.getAttribute('data-id');
                    
                    // Product name — try current selectors then fallbacks
                    let name = '';
                    const nameSelectors = [
                        'div.RG5Slk', 'div.KzDlHZ', 'div._4rR01T',
                        'a.IRpwTa', 'a.s1Q9rs', 'a.WKTcLC'
                    ];
                    for (const sel of nameSelectors) {
                        const el = card.querySelector(sel);
                        if (el && el.innerText.trim().length > 3) {
                            name = el.innerText.trim();
                            break;
                        }
                    }
                    // Fallback: longest link text
                    if (!name) {
                        const links = card.querySelectorAll('a[href*="/p/"]');
                        links.forEach(a => {
                            const t = a.innerText.trim();
                            if (t.length > name.length && t.length > 10) name = t;
                        });
                    }
                    if (!name || name.length < 3) continue;
                    
                    // Price — try current selectors
                    let price = '-';
                    const priceSelectors = [
                        'div.hZ3P6w', 'div.Nx9bqj', 'div._30jeq3'
                    ];
                    for (const sel of priceSelectors) {
                        const el = card.querySelector(sel);
                        if (el && el.innerText.trim()) {
                            price = el.innerText.trim();
                            break;
                        }
                    }
                    
                    // Stock info
                    const stockEl = card.querySelector('div.HZ0E6r, div[class*="HZ0E6r"]');
                    const stockNote = stockEl ? stockEl.innerText.trim() : '';
                    
                    results.push({
                        name: name.substring(0, 300),
                        price,
                        dataId,
                        stockNote
                    });
                }
                return results;
            }
        """)

        best_data, best_score = None, 0
        for p in products:
            q, n = query.lower(), p["name"].lower()
            score = (fuzz.token_set_ratio(q, n) * 0.6 + fuzz.token_sort_ratio(q, n) * 0.2 + fuzz.partial_ratio(q, n) * 0.2)
            if score > best_score and score > 45:
                best_score = score
                best_data = p

        if best_data:
            result.update({
                "matched_product": best_data["name"][:250],
                "price": best_data["price"],
                "status": "instock",
                "match_score": round(best_score),
            })
        else:
            page_text = (await page.inner_text("body") or "").lower()
            if any(p in page_text for p in ["sorry, no results found", "no results", "did you mean"]):
                result["matched_product"] = "No results found on Flipkart"
    except Exception as e:
        logger.warning(f"  ⚠️ [{fk_type}] Search parse error: {e}")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# INGESTION ENGINE — Orchestrates all scrapers
# ═══════════════════════════════════════════════════════════════════════════════

class IngestionEngine:
    """Processes queries across selected platforms with rate limiting."""

    def __init__(self, engine: BrowserEngine, zone: str, pincode: str):
        self.engine = engine
        self.zone_config = PLATFORM_CONFIG[zone]
        self.zone = zone
        self.pincode = pincode

    async def _dispatch(self, platform: str, query: str):
        """Route to the correct scraper function."""
        if platform == "Blinkit":
            return await scrape_blinkit(self.engine, query, self.zone_config)
        elif platform == "Zepto":
            return await scrape_zepto(self.engine, query, self.zone_config)
        elif platform == "Swiggy":
            return await scrape_swiggy(self.engine, query, self.zone_config)
        elif platform == "Amazon":
            return await scrape_amazon(self.engine, query, self.pincode)
        elif platform in ("Flipkart Main", "Flipkart Grocery", "Flipkart Minutes"):
            return await scrape_flipkart(self.engine, query, self.pincode, platform)
        else:
            return {"uploaded_query": query, "platform": platform,
                    "matched_product": "Unknown platform", "status": "error", "price": "-"}

    async def process_query(self, query: str, platforms: list) -> list:
        """Process ONE query across all selected platforms in parallel."""
        tasks = [self._dispatch(p, query) for p in platforms]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                processed.append({
                    "uploaded_query": query, "platform": platforms[i],
                    "matched_product": f"Error: {str(r)[:200]}", "status": "error", "price": "-",
                })
            else:
                processed.append(r)
        return processed

    async def process_bulk(self, queries: list, platforms: list) -> list:
        """Process multiple queries sequentially (platforms run in parallel per query)."""
        all_results = []
        total = len(queries)
        for i, query in enumerate(queries):
            logger.info(f"━━━ Query {i+1}/{total}: {query}")
            results = await self.process_query(query, platforms)
            all_results.extend(results)
            if i < total - 1:
                delay = random.uniform(*INTER_QUERY_DELAY)
                logger.info(f"    ⏳ Waiting {delay:.1f}s before next query...")
                await asyncio.sleep(delay)
        return all_results


# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════

browser_engine = BrowserEngine()
db_pool: Optional[asyncpg.Pool] = None
last_results_store: list = []


async def lifespan(app):
    """Application lifecycle: start browser + DB, then cleanup."""
    global db_pool
    logger.info("═══ Omni-Track Enterprise v2.0 ═══")

    # Start browser engine
    await browser_engine.start()

    # Init PostgreSQL pool
    try:
        db_pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=10)
        async with db_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS inventory_logs (
                    id SERIAL PRIMARY KEY,
                    platform VARCHAR(50),
                    city VARCHAR(50),
                    zone VARCHAR(100),
                    product_name VARCHAR(255),
                    search_query VARCHAR(255),
                    status VARCHAR(50),
                    price VARCHAR(50),
                    match_score INTEGER DEFAULT 0,
                    delivery_info VARCHAR(255),
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Add new columns if they don't exist (for existing DBs)
            await conn.execute("""
                DO $$ BEGIN
                    ALTER TABLE inventory_logs ADD COLUMN IF NOT EXISTS match_score INTEGER DEFAULT 0;
                    ALTER TABLE inventory_logs ADD COLUMN IF NOT EXISTS delivery_info VARCHAR(255);
                EXCEPTION WHEN OTHERS THEN NULL;
                END $$;
            """)
        logger.info("✅ PostgreSQL pool ready.")
    except Exception as e:
        logger.error(f"❌ Database Init Error: {e}")
        logger.warning("⚠️ Continuing without database — results won't be persisted.")

    yield

    # Shutdown
    await browser_engine.stop()
    if db_pool:
        await db_pool.close()
    logger.info("═══ Omni-Track shutdown complete ═══")


app = FastAPI(title="Omni-Track Enterprise API v2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/api/bulk-inventory")
async def bulk_inventory(
    zone: str = Form(...),
    platforms: str = Form(...),
    mode: str = Form(...),
    pincode: str = Form(...),
    query: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    global last_results_store

    if zone not in PLATFORM_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid Zone")

    selected_platforms = json.loads(platforms)
    queries: list = []

    if mode == "single":
        if not query:
            raise HTTPException(status_code=400, detail="Query is required for single mode")
        queries = [query.strip()]
    elif mode == "bulk":
        if not file:
            raise HTTPException(status_code=400, detail="File is required for bulk mode")
        contents = await file.read()
        try:
            if file.filename.endswith(".csv"):
                df = pd.read_csv(io.BytesIO(contents))
            else:
                df = pd.read_excel(io.BytesIO(contents))
            col = next(
                (c for c in df.columns if c.lower() in ["product", "product_name", "fsn", "asin", "query", "name", "sku"]),
                None,
            )
            if not col:
                raise HTTPException(
                    status_code=400,
                    detail="Could not find a valid column. Use 'product', 'fsn', 'asin', or 'query' as header.",
                )
            queries = df[col].dropna().astype(str).str.strip().tolist()[:100]
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error parsing file: {e}")

    total_lookups = len(queries) * len(selected_platforms)
    logger.info(f"🎯 Starting scrape: {len(queries)} queries × {len(selected_platforms)} platforms = {total_lookups} lookups")

    engine = IngestionEngine(browser_engine, zone, pincode)
    results = await engine.process_bulk(queries, selected_platforms)

    # Persist to PostgreSQL
    if db_pool:
        try:
            async with db_pool.acquire() as conn:
                for r in results:
                    await conn.execute(
                        """INSERT INTO inventory_logs
                           (platform, city, zone, product_name, search_query, status, price, match_score, delivery_info)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
                        r.get("platform", ""),
                        zone.split(" - ")[0],
                        zone,
                        str(r.get("matched_product", ""))[:250],
                        str(r.get("uploaded_query", ""))[:250],
                        r.get("status", ""),
                        str(r.get("price", "")),
                        int(r.get("match_score", 0) or 0),
                        str(r.get("delivery_info", ""))[:250],
                    )
        except Exception as e:
            logger.error(f"❌ PSQL Save Error: {e}")

    last_results_store = results
    logger.info(f"✅ Scrape complete: {len(results)} results")
    return {"data": results, "processed_count": len(queries)}


@app.get("/api/zones")
async def get_zones():
    return {"zones": list(PLATFORM_CONFIG.keys())}


@app.get("/api/export-csv")
async def export_csv():
    """Download last scrape results as CSV."""
    if not last_results_store:
        raise HTTPException(status_code=404, detail="No results to export. Run a scrape first.")
    output = io.StringIO()
    fields = ["uploaded_query", "platform", "matched_product", "status", "price", "match_score", "delivery_info"]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in last_results_store:
        writer.writerow({f: row.get(f, "") for f in fields})
    output.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=omni_track_{ts}.csv"},
    )


@app.get("/api/platform-status")
async def platform_status():
    """Platform readiness status."""
    return {
        "platforms": {
            "Blinkit": {"status": "ready", "method": "Network Interception", "note": "Browser cookies for location"},
            "Zepto": {"status": "ready", "method": "Network Interception", "note": "Browser cookies for location"},
            "Swiggy Instamart": {"status": "ready", "method": "Network Interception", "note": "Browser session"},
            "Amazon": {"status": "ready", "method": "DOM Parsing", "note": "ASIN or product name + pincode"},
            "Flipkart Main": {"status": "ready", "method": "DOM Parsing", "note": "FSN or product name + pincode"},
            "Flipkart Grocery": {"status": "ready", "method": "DOM Parsing", "note": "Marketplace: GROCERY"},
            "Flipkart Minutes": {"status": "ready", "method": "DOM Parsing", "note": "Marketplace: FLIPKART_MINUTES"},
        }
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "engine": "playwright", "version": "2.0.0"}


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)