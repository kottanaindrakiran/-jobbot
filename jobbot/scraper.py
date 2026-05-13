"""
scraper.py
----------
Scrapes job listings from 4 sources and returns a unified list:
  1. LinkedIn  — RSS feed (requests + BeautifulSoup)
  2. Naukri    — HTML scrape (requests + rotating User-Agent)
  3. Wellfound — HTML scrape (requests + BeautifulSoup)
  4. Internshala — HTML scrape (requests + BeautifulSoup)

Each job is returned as a unified dict with keys:
  title, company, location, jd_text, apply_url, source, posted_at, scraped_at
"""

import time
import random
import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from dotenv import load_dotenv

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
    "SDE",
    "Software Development Engineer",
    "Java Developer",
    "Frontend Developer",
    "React Developer",
    "AI Engineer",
    "Backend Developer",
]

LOCATION = "India"

UA = UserAgent()

REQUEST_TIMEOUT = 20  # seconds

def _sleep():
    """Random polite delay between requests."""
    delay = random.uniform(2, 5)
    log.debug(f"Sleeping {delay:.1f}s …")
    time.sleep(delay)


def _get_headers(referer: str = "https://www.google.com/") -> dict:
    """Build a realistic browser header with rotating User-Agent."""
    return {
        "User-Agent": UA.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": referer,
        "Connection": "keep-alive",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_job(
    title: str,
    company: str,
    location: str,
    jd_text: str,
    apply_url: str,
    source: str,
    posted_at: str = "",
) -> dict:
    """Return a standardised job dict."""
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


# ─────────────────────────────────────────────────────────────────────────────
#  SOURCE 1 — LINKEDIN  (RSS)
# ─────────────────────────────────────────────────────────────────────────────
def _scrape_linkedin_role(role: str, session: requests.Session) -> list[dict]:
    """Scrape one role from LinkedIn via the public job-search RSS/HTML feed."""
    jobs: list[dict] = []

    # LinkedIn's public jobs page (no login required, parseable HTML)
    url = (
        "https://www.linkedin.com/jobs/search/"
        f"?keywords={quote_plus(role)}"
        f"&location={quote_plus(LOCATION)}"
        "&f_TPR=r86400"      # posted in last 24 h
        "&trk=public_jobs_jobs-search-bar_search-submit"
    )

    try:
        resp = session.get(url, headers=_get_headers("https://www.linkedin.com/"),
                           timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        cards = soup.select("li.jobs-search__results-list > div") or \
                soup.select("ul.jobs-search__results-list li")

        if not cards:
            # Try the public listing card selectors
            cards = soup.select("div.base-card")

        for card in cards:
            title_el   = card.select_one("h3.base-search-card__title, h3.job-search-card__title")
            company_el = card.select_one("h4.base-search-card__subtitle, a.job-search-card__company-name")
            loc_el     = card.select_one("span.job-search-card__location")
            link_el    = card.select_one("a.base-card__full-link, a.job-search-card__title-link")
            date_el    = card.select_one("time")

            if not title_el:
                continue

            jobs.append(_make_job(
                title      = title_el.get_text(),
                company    = company_el.get_text() if company_el else "",
                location   = loc_el.get_text()     if loc_el     else LOCATION,
                jd_text    = "",        # full JD requires individual page fetch
                apply_url  = link_el["href"]        if link_el    else url,
                source     = "linkedin",
                posted_at  = date_el.get("datetime", "") if date_el else "",
            ))

    except requests.exceptions.HTTPError as e:
        log.warning(f"[LinkedIn] HTTP {e.response.status_code} for role '{role}'")
    except Exception as e:
        log.warning(f"[LinkedIn] Failed for role '{role}': {e}")

    return jobs


def scrape_linkedin(roles: list[str] = TARGET_ROLES) -> list[dict]:
    log.info("── LinkedIn scraping started ──")
    session = requests.Session()
    all_jobs: list[dict] = []

    for role in roles:
        log.info(f"  [LinkedIn] Searching: {role}")
        found = _scrape_linkedin_role(role, session)
        log.info(f"  [LinkedIn] {len(found)} jobs for '{role}'")
        all_jobs.extend(found)
        _sleep()

    log.info(f"[LinkedIn] Total: {len(all_jobs)} jobs")
    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
#  SOURCE 2 — NAUKRI
# ─────────────────────────────────────────────────────────────────────────────
_NAUKRI_SLUG_MAP = {
    "Full Stack Developer":          "full-stack-developer",
    "SDE":                           "software-developer",
    "Software Development Engineer": "software-development-engineer",
    "Java Developer":                "java-developer",
    "Frontend Developer":            "front-end-developer",
    "React Developer":               "react-developer",
    "AI Engineer":                   "ai-engineer",
    "Backend Developer":             "backend-developer",
}


def _scrape_naukri_role(role: str, session: requests.Session) -> list[dict]:
    jobs: list[dict] = []
    slug = _NAUKRI_SLUG_MAP.get(role, role.lower().replace(" ", "-"))
    url  = f"https://www.naukri.com/{slug}-jobs-in-india"

    try:
        resp = session.get(url, headers=_get_headers("https://www.naukri.com/"),
                           timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Naukri uses several card classes; try all known patterns
        cards = (
            soup.select("article.jobTuple") or
            soup.select("div.srp-jobtuple-wrapper") or
            soup.select("div.job-tuple-header")
        )

        for card in cards:
            title_el   = card.select_one("a.title, a.jobTitle")
            company_el = card.select_one("a.companyName, span.companyName")
            loc_el     = card.select_one("li.location, span.locWdth")
            salary_el  = card.select_one("li.salary, span.salary")
            desc_el    = card.select_one("span.job-description, div.job-desc")
            link_el    = card.select_one("a.title, a.jobTitle")
            date_el    = card.select_one("span.jobAge, time")

            if not title_el:
                continue

            salary_str = salary_el.get_text(strip=True) if salary_el else ""
            desc_str   = desc_el.get_text(" ", strip=True) if desc_el else ""
            jd          = f"{desc_str} | Salary: {salary_str}".strip(" |")

            jobs.append(_make_job(
                title     = title_el.get_text(),
                company   = company_el.get_text() if company_el else "",
                location  = loc_el.get_text()     if loc_el     else "India",
                jd_text   = jd,
                apply_url = link_el["href"]        if link_el and link_el.get("href") else url,
                source    = "naukri",
                posted_at = date_el.get_text(strip=True) if date_el else "",
            ))

    except requests.exceptions.HTTPError as e:
        log.warning(f"[Naukri] HTTP {e.response.status_code} for role '{role}'")
    except Exception as e:
        log.warning(f"[Naukri] Failed for role '{role}': {e}")

    return jobs


def scrape_naukri(roles: list[str] = TARGET_ROLES) -> list[dict]:
    log.info("── Naukri scraping started ──")
    session = requests.Session()
    all_jobs: list[dict] = []

    for role in roles:
        log.info(f"  [Naukri] Searching: {role}")
        found = _scrape_naukri_role(role, session)
        log.info(f"  [Naukri] {len(found)} jobs for '{role}'")
        all_jobs.extend(found)
        _sleep()

    log.info(f"[Naukri] Total: {len(all_jobs)} jobs")
    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
#  SOURCE 3 — WELLFOUND (AngelList Talent)
# ─────────────────────────────────────────────────────────────────────────────
_WELLFOUND_ROLE_MAP = {
    "Full Stack Developer":          "full-stack-engineer",
    "SDE":                           "software-engineer",
    "Software Development Engineer": "software-engineer",
    "Java Developer":                "java-engineer",
    "Frontend Developer":            "frontend-engineer",
    "React Developer":               "frontend-engineer",
    "AI Engineer":                   "ai-engineer",
    "Backend Developer":             "backend-engineer",
}


def _scrape_wellfound_role(role: str, session: requests.Session) -> list[dict]:
    jobs: list[dict] = []
    role_slug = _WELLFOUND_ROLE_MAP.get(role, role.lower().replace(" ", "-"))
    url = f"https://wellfound.com/role/l/{role_slug}/india"

    try:
        resp = session.get(url, headers=_get_headers("https://wellfound.com/"),
                           timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Wellfound job cards
        cards = (
            soup.select("div[data-test='StartupResult']") or
            soup.select("div.styles_component__2E5Ba") or
            soup.select("div.w-full.rounded.border") or
            soup.select("li[data-test='jobListingCard']")
        )

        for card in cards:
            title_el   = card.select_one(
                "span[data-test='title'], a[data-test='job-link'], h2"
            )
            company_el = card.select_one(
                "a[data-test='startup-link'], span[data-test='company']"
            )
            loc_el     = card.select_one(
                "span[data-test='location'], div.text-xs.text-neutral-500"
            )
            funding_el = card.select_one(
                "span[data-test='fundraising'], span.text-neutral-1000"
            )
            link_el    = card.select_one(
                "a[data-test='job-link'], a[data-test='startup-link']"
            )

            if not title_el:
                continue

            funding_str = funding_el.get_text(strip=True) if funding_el else ""
            href        = link_el["href"] if link_el and link_el.get("href") else url
            if href.startswith("/"):
                href = "https://wellfound.com" + href

            jobs.append(_make_job(
                title     = title_el.get_text(),
                company   = company_el.get_text() if company_el else "",
                location  = loc_el.get_text()     if loc_el     else "India",
                jd_text   = f"Funding stage: {funding_str}" if funding_str else "",
                apply_url = href,
                source    = "wellfound",
                posted_at = "",
            ))

    except requests.exceptions.HTTPError as e:
        log.warning(f"[Wellfound] HTTP {e.response.status_code} for role '{role}'")
    except Exception as e:
        log.warning(f"[Wellfound] Failed for role '{role}': {e}")

    return jobs


def scrape_wellfound(roles: list[str] = TARGET_ROLES) -> list[dict]:
    log.info("── Wellfound scraping started ──")
    session = requests.Session()
    all_jobs: list[dict] = []

    for role in roles:
        log.info(f"  [Wellfound] Searching: {role}")
        found = _scrape_wellfound_role(role, session)
        log.info(f"  [Wellfound] {len(found)} jobs for '{role}'")
        all_jobs.extend(found)
        _sleep()

    log.info(f"[Wellfound] Total: {len(all_jobs)} jobs")
    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
#  SOURCE 4 — INTERNSHALA (full-time jobs)
# ─────────────────────────────────────────────────────────────────────────────
_INTERNSHALA_SLUG_MAP = {
    "Full Stack Developer":          "full-stack-development",
    "SDE":                           "software-development",
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

        # Internshala job card containers
        cards = (
            soup.select("div.individual_internship") or
            soup.select("div.internship-item") or
            soup.select("div.container-fluid.individual_internship")
        )

        for card in cards:
            title_el   = card.select_one(
                "h3.job-internship-name a, "
                "a.job-title-href, "
                "div.job-title h3"
            )
            company_el = card.select_one(
                "div.company_name a, "
                "span.company-name, "
                "p.company-name"
            )
            loc_el     = card.select_one(
                "div.location_link a, "
                "a.location_link, "
                "span.location_link"
            )
            salary_el  = card.select_one(
                "div.stipend_container span.stipend, "
                "span.stipend"
            )
            link_el    = card.select_one("a.job-title-href, h3.job-internship-name a")
            date_el    = card.select_one("div.posted_by_container span, span.posted-at")

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
                jd_text   = f"Salary/Stipend: {salary_str}" if salary_str else "",
                apply_url = href,
                source    = "internshala",
                posted_at = date_el.get_text(strip=True) if date_el else "",
            ))

    except requests.exceptions.HTTPError as e:
        log.warning(f"[Internshala] HTTP {e.response.status_code} for role '{role}'")
    except Exception as e:
        log.warning(f"[Internshala] Failed for role '{role}': {e}")

    return jobs


def scrape_internshala(roles: list[str] = TARGET_ROLES) -> list[dict]:
    log.info("── Internshala scraping started ──")
    session = requests.Session()
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
                 or None to run all sources

    Returns:
        list of unified job dicts
    """
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

    start = time.perf_counter()

    for source_name in sources:
        scraper_fn = scraper_map.get(source_name)
        if not scraper_fn:
            log.warning(f"Unknown source '{source_name}' — skipping")
            continue
        try:
            found = scraper_fn(roles)
            all_jobs.extend(found)
        except Exception as e:
            # Source-level safety net — one broken source never kills the run
            log.error(f"[{source_name}] Source-level failure (skipping): {e}")

    elapsed = time.perf_counter() - start
    log.info("=" * 60)
    log.info(f"✅ Scraping complete — {len(all_jobs)} total jobs in {elapsed:.1f}s")
    log.info("=" * 60)

    return all_jobs


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    # Quick test: scrape 2 roles from all sources
    test_roles = ["Full Stack Developer", "Backend Developer"]
    jobs = scrape_all(roles=test_roles)

    print(f"\n{'─'*60}")
    print(f"  Found {len(jobs)} jobs total")
    print(f"{'─'*60}")

    # Print first 5 results
    for i, job in enumerate(jobs[:5], 1):
        print(f"\n[{i}] {job['title']} @ {job['company']}")
        print(f"    Source   : {job['source']}")
        print(f"    Location : {job['location']}")
        print(f"    Posted   : {job['posted_at']}")
        print(f"    URL      : {job['apply_url'][:80]}…")
        if job['jd_text']:
            print(f"    JD       : {job['jd_text'][:100]}…")

    # Save full output to logs/
    import pathlib
    pathlib.Path("logs").mkdir(exist_ok=True)
    out_path = f"logs/scrape_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, default=str)
    print(f"\n💾 Full results saved to {out_path}")
