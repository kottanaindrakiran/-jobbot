"""
scraper.py
----------
Scrapes job listings from 4 sources and returns a unified list:
  1. LinkedIn    — Playwright browser (login → search, bypasses bot blocks)
  2. Naukri      — Playwright browser (login → search, bypasses JS rendering)
  3. Wellfound   — Playwright browser (login → browse, bypasses Cloudflare)
  4. Internshala — requests + BeautifulSoup (already works fine)

Each job is returned as a unified dict with keys:
  title, company, location, jd_text, apply_url, source, posted_at, scraped_at
"""

import json
import os
import time
import random
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scraper")

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────────────────────
TARGET_ROLES = [
    "Full Stack Developer",
    "Software Development Engineer",
    "Java Developer",
    "Frontend Developer",
    "React Developer",
    "AI Engineer",
    "Backend Developer",
]

LOCATION = "India"
UA = UserAgent()
REQUEST_TIMEOUT = 20  # seconds for requests lib

# Playwright settings
HEADLESS    = os.getenv("GITHUB_ACTIONS") == "true"
NAV_TIMEOUT = 30_000  # ms

# Credentials
LI_EMAIL = os.getenv("LINKEDIN_EMAIL")
LI_PASS  = os.getenv("LINKEDIN_PASSWORD")
NK_EMAIL = os.getenv("NAUKRI_EMAIL")
NK_PASS  = os.getenv("NAUKRI_PASSWORD")
WF_EMAIL = os.getenv("WELLFOUND_EMAIL")
WF_PASS  = os.getenv("WELLFOUND_PASSWORD")

COOKIES_DIR = Path(__file__).parent / "logs"
COOKIES_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _sleep():
    time.sleep(random.uniform(2, 5))

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _make_job(title, company, location, jd_text, apply_url, source, posted_at="") -> dict:
    return {
        "title":      title.strip(),
        "company":    company.strip(),
        "location":   location.strip(),
        "jd_text":    jd_text.strip(),
        "apply_url":  apply_url.strip(),
        "source":     source,
        "posted_at":  posted_at.strip(),
        "scraped_at": _now(),
    }

def _get_headers(referer: str = "https://www.google.com/") -> dict:
    return {
        "User-Agent":      UA.random,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         referer,
        "Connection":      "keep-alive",
    }

def _save_cookies(context, name: str):
    path = COOKIES_DIR / f"cookies_{name}.json"
    path.write_text(json.dumps(context.cookies()), encoding="utf-8")
    log.info(f"[{name}] Cookies saved ✓")

def _load_cookies(context, name: str) -> bool:
    path = COOKIES_DIR / f"cookies_{name}.json"
    alt_path = COOKIES_DIR / f"{name}_cookies.json"
    if not path.exists() and alt_path.exists():
        path = alt_path
    if not path.exists():
        log.info(f"[{name}] Cookie file not found at {path}")
        return False
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            log.warning(f"[{name}] Cookie file is empty!")
            return False
        cookies = json.loads(content)
        context.add_cookies(cookies)
        log.info(f"[{name}] Cookies loaded successfully ({len(cookies)} cookies) ✓")
        return True
    except json.JSONDecodeError as je:
        log.error(f"[{name}] Cookie file has invalid JSON: {je}")
        return False
    except Exception as e:
        log.error(f"[{name}] Failed to load cookies: {e}")
        return False

def _new_browser_context(pw):
    browser = pw.chromium.launch(
        headless=HEADLESS,
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
    )
    return browser, context


# ─────────────────────────────────────────────────────────────────────────────
#  SOURCE 1 — LINKEDIN  (Playwright + Login)
# ─────────────────────────────────────────────────────────────────────────────
def scrape_linkedin(roles: list[str] = TARGET_ROLES) -> list[dict]:
    log.info("── LinkedIn scraping started (Playwright) ──")
    if not LI_EMAIL or not LI_PASS:
        log.warning("[LinkedIn] Credentials not set — skipping")
        return []

    all_jobs: list[dict] = []

    try:
        with sync_playwright() as pw:
            browser, context = _new_browser_context(pw)
            page = context.new_page()

            # ── Login ──────────────────────────────────────────────────────
            logged_in = False
            if _load_cookies(context, "linkedin"):
                page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                page.wait_for_load_state("domcontentloaded")
                if "feed" in page.url:
                    log.info("[LinkedIn] Session restored via cookies ✓")
                    logged_in = True

            if not logged_in:
                log.info("[LinkedIn] Logging in …")
                page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                try:
                    page.wait_for_selector("#username", timeout=NAV_TIMEOUT)
                    page.fill("#username", LI_EMAIL)
                    page.fill("#password", LI_PASS)
                    page.click("button[type=submit]")
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(2)
                    if "feed" in page.url or "mynetwork" in page.url:
                        log.info("[LinkedIn] Login successful ✓")
                        _save_cookies(context, "linkedin")
                        logged_in = True
                    else:
                        log.warning(f"[LinkedIn] Login failed (URL: {page.url}) — skipping LinkedIn")
                        browser.close()
                        return []
                except PWTimeout:
                    log.warning("[LinkedIn] Login page timed out — site may be blocking headless browser")
                    browser.close()
                    return []

            # ── Scrape each role ───────────────────────────────────────────
            for role in roles:
                log.info(f"  [LinkedIn] Searching: {role}")
                url = (
                    "https://www.linkedin.com/jobs/search/"
                    f"?keywords={quote_plus(role)}"
                    f"&location={quote_plus(LOCATION)}"
                    "&f_TPR=r86400"
                )
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(random.uniform(2, 3))

                    # Scroll to trigger lazy-load
                    for _ in range(3):
                        page.keyboard.press("End")
                        time.sleep(1)

                    soup  = BeautifulSoup(page.content(), "lxml")
                    cards = (
                        soup.select("div.job-card-container") or
                        soup.select("div.job-card-list__entity-lockup") or
                        soup.select("div.base-card") or
                        soup.select(".job-search-card") or
                        soup.select("li.jobs-search__results-list > div")
                    )

                    role_jobs = []
                    for card in cards:
                        title_el = (
                            card.select_one("a.job-card-list__title--link") or
                            card.select_one("h3.base-search-card__title, h3.job-search-card__title")
                        )
                        company_el = (
                            card.select_one("span.job-card-container__primary-description") or
                            card.select_one(".job-card-container__company-link") or
                            card.select_one("div.artdeco-entity-lockup__subtitle") or
                            card.select_one("h4.base-search-card__subtitle, a.job-search-card__company-name")
                        )
                        loc_el = (
                            card.select_one("li.job-card-container__metadata-item") or
                            card.select_one("span.job-card-container__metadata-item") or
                            card.select_one(".job-card-container__metadata-wrapper") or
                            card.select_one("span.job-search-card__location")
                        )
                        link_el = (
                            title_el if title_el and (title_el.get("href") or "job-card" in title_el.get("class", [])) else None
                        ) or (
                            card.select_one("a.base-card__full-link, a.job-search-card__title-link")
                        )
                        date_el    = card.select_one("time")
                        if not title_el:
                            continue
                        
                        href = link_el["href"] if link_el and link_el.get("href") else url
                        if href.startswith("/"):
                            href = "https://www.linkedin.com" + href

                        role_jobs.append(_make_job(
                            title     = title_el.get_text(strip=True),
                            company   = company_el.get_text(strip=True) if company_el else "",
                            location  = loc_el.get_text(strip=True)     if loc_el     else LOCATION,
                            jd_text   = "",
                            apply_url = href,
                            source    = "linkedin",
                            posted_at = date_el.get("datetime", "") if date_el else "",
                        ))

                    log.info(f"  [LinkedIn] {len(role_jobs)} jobs for '{role}'")
                    all_jobs.extend(role_jobs)

                except PWTimeout:
                    log.warning(f"[LinkedIn] Timeout for role '{role}'")
                except Exception as e:
                    log.warning(f"[LinkedIn] Error for '{role}': {e}")

                time.sleep(random.uniform(2, 4))

            browser.close()

    except Exception as e:
        log.error(f"[LinkedIn] Scraper crashed: {e}")

    log.info(f"[LinkedIn] Total: {len(all_jobs)} jobs")
    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
#  SOURCE 2 — NAUKRI  (Playwright + Login)
# ─────────────────────────────────────────────────────────────────────────────
_NAUKRI_SLUG_MAP = {
    "Full Stack Developer":          "full-stack-developer",
    "Software Development Engineer": "software-development-engineer",
    "Java Developer":                "java-developer",
    "Frontend Developer":            "front-end-developer",
    "React Developer":               "react-developer",
    "AI Engineer":                   "ai-engineer",
    "Backend Developer":             "backend-developer",
}


def scrape_naukri(roles: list[str] = TARGET_ROLES) -> list[dict]:
    log.info("── Naukri scraping started (Playwright) ──")
    if not NK_EMAIL or not NK_PASS:
        log.warning("[Naukri] Credentials not set — skipping")
        return []

    all_jobs: list[dict] = []

    try:
        with sync_playwright() as pw:
            browser, context = _new_browser_context(pw)
            page = context.new_page()

            # ── Login ──────────────────────────────────────────────────────
            logged_in = False
            if _load_cookies(context, "naukri"):
                try:
                    page.goto("https://www.naukri.com/mnjuser/homepage", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                    page.wait_for_load_state("domcontentloaded")
                    title = page.title()
                    if "nlogin" not in page.url and "login" not in page.url and "access denied" not in title.lower() and "cloudflare" not in title.lower():
                        log.info("[Naukri] Session restored via cookies ✓")
                        logged_in = True
                    else:
                        log.warning(f"[Naukri] Session cookie invalid or blocked by Cloudflare (URL: {page.url}, Title: {title})")
                except Exception as e:
                    log.warning(f"[Naukri] Session restore check failed: {e}")

            if not logged_in:
                log.info("[Naukri] Logging in …")
                page.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                try:
                    page.wait_for_selector("input[placeholder*='Email']", timeout=NAV_TIMEOUT)
                    time.sleep(1)
                    page.fill("input[placeholder*='Email']", NK_EMAIL)
                    page.fill("input[placeholder*='Password']", NK_PASS)
                    page.click("button[type=submit]")
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(2)
                    _save_cookies(context, "naukri")
                    log.info("[Naukri] Login successful ✓")
                    logged_in = True
                except PWTimeout:
                    log.warning("[Naukri] Login page timed out — site may be blocking headless browser")
                    browser.close()
                    return []
                except Exception as e:
                    log.warning(f"[Naukri] Login error: {e}")
                    browser.close()
                    return []

            # ── Scrape each role ───────────────────────────────────────────
            for role in roles:
                log.info(f"  [Naukri] Searching: {role}")
                slug = _NAUKRI_SLUG_MAP.get(role, role.lower().replace(" ", "-"))
                url  = f"https://www.naukri.com/{slug}-jobs-in-india"

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(random.uniform(2, 3))

                    soup  = BeautifulSoup(page.content(), "lxml")
                    cards = (
                        soup.select("article.jobTuple") or
                        soup.select("div.srp-jobtuple-wrapper") or
                        soup.select("div.cust-job-tuple") or
                        soup.select("div.job-tuple-header")
                    )

                    role_jobs = []
                    for card in cards:
                        title_el   = card.select_one("a.title, a.jobTitle")
                        company_el = card.select_one("a.companyName, span.companyName")
                        loc_el     = card.select_one("li.location, span.locWdth, span.loc")
                        desc_el    = card.select_one("span.job-description, div.job-desc")
                        link_el    = card.select_one("a.title, a.jobTitle")
                        if not title_el:
                            continue
                        href = link_el["href"] if link_el and link_el.get("href") else url
                        role_jobs.append(_make_job(
                            title     = title_el.get_text(),
                            company   = company_el.get_text() if company_el else "",
                            location  = loc_el.get_text()     if loc_el     else "India",
                            jd_text   = desc_el.get_text(" ", strip=True) if desc_el else "",
                            apply_url = href,
                            source    = "naukri",
                        ))

                    log.info(f"  [Naukri] {len(role_jobs)} jobs for '{role}'")
                    all_jobs.extend(role_jobs)

                except PWTimeout:
                    log.warning(f"[Naukri] Timeout for role '{role}'")
                except Exception as e:
                    log.warning(f"[Naukri] Error for '{role}': {e}")

                time.sleep(random.uniform(2, 4))

            browser.close()

    except Exception as e:
        log.error(f"[Naukri] Scraper crashed: {e}")

    log.info(f"[Naukri] Total: {len(all_jobs)} jobs")
    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
#  SOURCE 3 — WELLFOUND  (Playwright + Login, bypasses Cloudflare 403)
# ─────────────────────────────────────────────────────────────────────────────
_WELLFOUND_ROLE_MAP = {
    "Full Stack Developer":          "full-stack-engineer",
    "Software Development Engineer": "software-engineer",
    "Java Developer":                "java-engineer",
    "Frontend Developer":            "frontend-engineer",
    "React Developer":               "frontend-engineer",
    "AI Engineer":                   "ai-engineer",
    "Backend Developer":             "backend-engineer",
}


def scrape_wellfound(roles: list[str] = TARGET_ROLES) -> list[dict]:
    log.info("── Wellfound scraping started (Playwright) ──")
    if not WF_EMAIL or not WF_PASS:
        log.warning("[Wellfound] Credentials not set — skipping")
        return []

    all_jobs: list[dict] = []

    try:
        with sync_playwright() as pw:
            browser, context = _new_browser_context(pw)
            page = context.new_page()

            # ── Login ──────────────────────────────────────────────────────
            logged_in = False
            if _load_cookies(context, "wellfound"):
                try:
                    page.goto("https://wellfound.com/jobs", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                    page.wait_for_load_state("domcontentloaded")
                    title = page.title()
                    if "login" not in page.url and "access denied" not in title.lower() and "cloudflare" not in title.lower():
                        log.info("[Wellfound] Session restored via cookies ✓")
                        logged_in = True
                    else:
                        log.warning(f"[Wellfound] Session cookie invalid or blocked by Cloudflare (URL: {page.url}, Title: {title})")
                except Exception as e:
                    log.warning(f"[Wellfound] Session restore check failed: {e}")

            if not logged_in:
                log.info("[Wellfound] Logging in …")
                page.goto("https://wellfound.com/login", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                try:
                    page.wait_for_selector("input[type='email']", timeout=NAV_TIMEOUT)
                    time.sleep(1)
                    page.fill("input[type='email']", WF_EMAIL)
                    page.fill("input[type='password']", WF_PASS)
                    page.click("button[type='submit']")
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(2)
                    if "login" not in page.url:
                        log.info("[Wellfound] Login successful ✓")
                        _save_cookies(context, "wellfound")
                        logged_in = True
                    else:
                        log.warning(f"[Wellfound] Login failed (URL: {page.url})")
                        browser.close()
                        return []
                except PWTimeout:
                    log.warning("[Wellfound] Login page timed out — Cloudflare may be blocking")
                    browser.close()
                    return []
                except Exception as e:
                    log.warning(f"[Wellfound] Login error: {e}")
                    browser.close()
                    return []

            # ── Scrape each role ───────────────────────────────────────────
            for role in roles:
                log.info(f"  [Wellfound] Searching: {role}")
                role_slug = _WELLFOUND_ROLE_MAP.get(role, role.lower().replace(" ", "-"))
                url = f"https://wellfound.com/role/l/{role_slug}/india"

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                    page.wait_for_load_state("domcontentloaded")
                    time.sleep(random.uniform(2, 3))

                    soup  = BeautifulSoup(page.content(), "lxml")
                    cards = (
                        soup.select("div[data-test='StartupResult']") or
                        soup.select("li[data-test='jobListingCard']") or
                        soup.select("div.w-full.rounded.border")
                    )

                    role_jobs = []
                    for card in cards:
                        title_el   = card.select_one("span[data-test='title'], a[data-test='job-link'], h2")
                        company_el = card.select_one("a[data-test='startup-link'], span[data-test='company']")
                        loc_el     = card.select_one("span[data-test='location'], div.text-xs")
                        link_el    = card.select_one("a[data-test='job-link'], a[data-test='startup-link']")
                        if not title_el:
                            continue
                        href = link_el["href"] if link_el and link_el.get("href") else url
                        if href.startswith("/"):
                            href = "https://wellfound.com" + href
                        role_jobs.append(_make_job(
                            title     = title_el.get_text(),
                            company   = company_el.get_text() if company_el else "",
                            location  = loc_el.get_text()     if loc_el     else "India",
                            jd_text   = "",
                            apply_url = href,
                            source    = "wellfound",
                        ))

                    log.info(f"  [Wellfound] {len(role_jobs)} jobs for '{role}'")
                    all_jobs.extend(role_jobs)

                except PWTimeout:
                    log.warning(f"[Wellfound] Timeout for role '{role}'")
                except Exception as e:
                    log.warning(f"[Wellfound] Error for '{role}': {e}")

                time.sleep(random.uniform(2, 4))

            browser.close()

    except Exception as e:
        log.error(f"[Wellfound] Scraper crashed: {e}")

    log.info(f"[Wellfound] Total: {len(all_jobs)} jobs")
    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
#  SOURCE 4 — INTERNSHALA  (requests — already works fine)
# ─────────────────────────────────────────────────────────────────────────────
_INTERNSHALA_SLUG_MAP = {
    "Full Stack Developer":          "full-stack-development",
    "Software Development Engineer": "software-development",
    "Java Developer":                "java",
    "Frontend Developer":            "frontend-development",
    "React Developer":               "react-js",
    "AI Engineer":                   "artificial-intelligence",
    "Backend Developer":             "backend-development",
}


def _scrape_internshala_role(role: str, session: requests.Session) -> list[dict]:
    jobs: list[dict] = []
    slug = _INTERNSHALA_SLUG_MAP.get(role, role.lower().replace(" ", "-"))
    url  = f"https://internshala.com/jobs/{slug}-jobs/"

    try:
        resp = session.get(url, headers=_get_headers("https://internshala.com/"),
                           timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        cards = (
            soup.select("div.individual_internship") or
            soup.select("div.internship-item") or
            soup.select("div.container-fluid.individual_internship")
        )

        for card in cards:
            title_el   = card.select_one(
                "h3.job-internship-name a, a.job-title-href, div.job-title h3"
            )
            company_el = card.select_one(
                "div.company_name a, span.company-name, p.company-name"
            )
            loc_el     = card.select_one(
                "div.location_link a, a.location_link, span.location_link"
            )
            salary_el  = card.select_one("div.stipend_container span.stipend, span.stipend")
            link_el    = card.select_one("a.job-title-href, h3.job-internship-name a")

            if not title_el:
                continue

            salary_str = salary_el.get_text(strip=True) if salary_el else ""
            href       = link_el["href"] if link_el and link_el.get("href") else url
            if href.startswith("/"):
                href = "https://internshala.com" + href

            jobs.append(_make_job(
                title     = title_el.get_text(),
                company   = company_el.get_text() if company_el else "",
                location  = loc_el.get_text()     if loc_el     else "India",
                jd_text   = f"Salary: {salary_str}" if salary_str else "",
                apply_url = href,
                source    = "internshala",
            ))

    except requests.exceptions.HTTPError as e:
        log.warning(f"[Internshala] HTTP {e.response.status_code} for role '{role}'")
    except Exception as e:
        log.warning(f"[Internshala] Failed for role '{role}': {e}")

    return jobs


def scrape_internshala(roles: list[str] = TARGET_ROLES) -> list[dict]:
    log.info("── Internshala scraping started ──")
    session   = requests.Session()
    all_jobs: list[dict] = []

    for role in roles:
        log.info(f"  [Internshala] Searching: {role}")
        found = _scrape_internshala_role(role, session)
        log.info(f"  [Internshala] {len(found)} jobs for '{role}'")
        all_jobs.extend(found)
        _sleep()

    log.info(f"[Internshala] Total: {len(all_jobs)} jobs")
    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
#  MASTER SCRAPER
# ─────────────────────────────────────────────────────────────────────────────
def scrape_all(
    roles: list[str] = TARGET_ROLES,
    sources: list[str] | None = None,
) -> list[dict]:
    """
    Run all scrapers and return a combined, flattened job list.

    Args:
        roles:   list of job role search terms
        sources: subset of ["linkedin","naukri","wellfound","internshala"]
                 or None to run all

    Returns:
        list of unified job dicts
    """
    import time as _time
    sources = sources or ["linkedin", "naukri", "wellfound", "internshala"]
    all_jobs: list[dict] = []

    scraper_map = {
        "linkedin":    scrape_linkedin,
        "naukri":      scrape_naukri,
        "wellfound":   scrape_wellfound,
        "internshala": scrape_internshala,
    }

    log.info("=" * 60)
    log.info(f"🕷  JobBot Scraper — {len(roles)} roles × {len(sources)} sources")
    log.info(f"   Roles  : {roles}")
    log.info(f"   Sources: {sources}")
    log.info("=" * 60)

    start = _time.perf_counter()

    for source_name in sources:
        scraper_fn = scraper_map.get(source_name)
        if not scraper_fn:
            log.warning(f"Unknown source '{source_name}' — skipping")
            continue
        try:
            found = scraper_fn(roles)
            all_jobs.extend(found)
        except Exception as e:
            log.error(f"[{source_name}] Source-level failure (skipping): {e}")

    elapsed = _time.perf_counter() - start
    log.info("=" * 60)
    log.info(f"✅ Scraping complete — {len(all_jobs)} total jobs in {elapsed:.1f}s")
    log.info("=" * 60)

    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import pathlib
    test_roles = ["Full Stack Developer", "Backend Developer"]
    jobs = scrape_all(roles=test_roles)

    print(f"\n{'-'*60}")
    print(f"  Found {len(jobs)} jobs total")
    print(f"{'-'*60}")
    for i, job in enumerate(jobs[:5], 1):
        print(f"\n[{i}] {job['title']} @ {job['company']}")
        print(f"    Source  : {job['source']}")
        print(f"    URL     : {job['apply_url'][:80]}")

    pathlib.Path("logs").mkdir(exist_ok=True)
    out = f"logs/scrape_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    import json as _json
    with open(out, "w", encoding="utf-8") as f:
        _json.dump(jobs, f, indent=2, default=str)
    print(f"\nSaved to {out}")
