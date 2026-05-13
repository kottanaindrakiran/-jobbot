"""
filter.py
---------
Cleans raw job list through a 5-step pipeline:

  Step 1 — Deduplication      : remove already-applied jobs (via Google Sheets)
  Step 2 — Keyword match score: score each job against candidate's skill profile
  Step 3 — Freshness filter   : keep only jobs posted within last 48 h
  Step 4 — Min-score filter   : drop jobs below 30% match score
  Step 5 — Sort & limit       : return top 100 by score

Usage:
    from filter import run_filter
    good_jobs = run_filter(raw_jobs)
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("filter")

# ─────────────────────────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
PROFILE_PATH   = Path(__file__).parent / "profile.json"
MIN_SCORE      = 15       # minimum match_score to keep a job
TOP_N          = 100      # max jobs to return
FRESHNESS_HRS  = 48       # hours — jobs older than this are dropped
ROLE_BOOST     = 20       # bonus points when job title matches a target role

# ─────────────────────────────────────────────────────────────────────────────
#  PROFILE LOADER
# ─────────────────────────────────────────────────────────────────────────────
def _load_profile() -> dict:
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _extract_skills(profile: dict) -> list[str]:
    """
    Flatten all skill categories from profile.json into a single lowercase list.
    Handles both old schema (languages/frameworks/tools/cloud) and
    new schema (languages/frameworks/ai_ml/databases/tools).
    """
    skills_block = profile.get("skills", {})
    all_skills: list[str] = []

    for category_values in skills_block.values():
        if isinstance(category_values, list):
            all_skills.extend(category_values)

    # Deduplicate, lowercase, strip whitespace
    seen: set[str] = set()
    result: list[str] = []
    for s in all_skills:
        key = s.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(s.strip())

    return result


def _extract_target_roles(profile: dict) -> list[str]:
    return [r.lower() for r in profile.get("target_roles", [])]


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 — DEDUPLICATION
# ─────────────────────────────────────────────────────────────────────────────
def _get_applied_urls() -> set[str]:
    """
    Fetch the set of already-applied job URLs from Google Sheets.
    Falls back gracefully if sheets_logger is unavailable.
    """
    try:
        from sheets_logger import get_applied_urls  # lazy import
        urls = get_applied_urls()
        log.info(f"[Filter] Loaded {len(urls)} already-applied URLs from Sheets")
        return set(urls)
    except ImportError:
        log.warning("[Filter] sheets_logger not available — skipping dedup against Sheets")
        return set()
    except Exception as e:
        log.warning(f"[Filter] Could not load applied URLs from Sheets: {e}")
        return set()


def _deduplicate_applied(jobs: list[dict], applied_urls: set[str]) -> list[dict]:
    """Remove jobs whose apply_url has already been applied to."""
    before = len(jobs)
    filtered = [j for j in jobs if j.get("apply_url", "").strip() not in applied_urls]
    removed  = before - len(filtered)
    if removed:
        log.info(f"[Filter] Step 1 — Removed {removed} already-applied jobs")
    else:
        log.info(f"[Filter] Step 1 — No previously-applied duplicates found")
    return filtered


def _deduplicate_within_batch(jobs: list[dict]) -> list[dict]:
    """
    Remove intra-batch duplicates by exact apply_url.
    Keeps first occurrence (highest potential score after ordering).
    """
    before   = len(jobs)
    seen_urls: set[str] = set()
    unique:    list[dict] = []

    for job in jobs:
        url = job.get("apply_url", "").strip()
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        unique.append(job)

    removed = before - len(unique)
    if removed:
        log.info(f"[Filter] Step 1 — Removed {removed} within-batch URL duplicates")
    return unique


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2 — KEYWORD MATCH SCORE
# ─────────────────────────────────────────────────────────────────────────────
def _build_skill_pattern(skills: list[str]):
    """
    Compile a single regex that matches any profile skill as a whole word.
    Using word boundaries (\b) ensures "Java" doesn't match "JavaScript" etc.
    Special regex chars in skill names are escaped automatically.
    """
    escaped = [re.escape(s) for s in skills]
    # Sort longest first so "Spring Boot" beats "Spring"
    escaped.sort(key=len, reverse=True)
    return re.compile(
        r"\b(" + "|".join(escaped) + r")\b",
        re.IGNORECASE,
    )


def _score_job(job: dict, skill_pattern, total_skills: int, target_roles: list[str]) -> float:
    """
    Compute match_score (0–120):
      - Base:  (matched_skills / total_skills) × 100
      - Boost: +20 if any target role appears in job title
    """
    search_text = f"{job.get('title', '')} {job.get('jd_text', '')}".strip()

    # Distinct matched skills (use a set so "Python" in title + JD counts once)
    matched = set(m.group(0).lower() for m in skill_pattern.finditer(search_text))
    base_score = (len(matched) / total_skills) * 100 if total_skills else 0

    # Role-title boost
    title_lower = job.get("title", "").lower()
    role_hit    = any(role in title_lower for role in target_roles)
    boost       = ROLE_BOOST if role_hit else 0

    return round(base_score + boost, 2)


def _apply_scores(
    jobs: list[dict],
    skills: list[str],
    target_roles: list[str],
) -> list[dict]:
    """Attach match_score and matched_skills to every job dict."""
    if not skills:
        log.warning("[Filter] No skills found in profile — all scores will be 0")
        for job in jobs:
            job["match_score"] = 0.0
            job["matched_skills"] = []
        return jobs

    pattern     = _build_skill_pattern(skills)
    total       = len(skills)

    for job in jobs:
        search_text = f"{job.get('title', '')} {job.get('jd_text', '')}".strip()
        matched_set = set(m.group(0).lower() for m in pattern.finditer(search_text))

        job["matched_skills"] = sorted(matched_set)
        job["match_score"]    = _score_job(job, pattern, total, target_roles)

    log.info(f"[Filter] Step 2 — Scored {len(jobs)} jobs against {total} skills")
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 — FRESHNESS FILTER
# ─────────────────────────────────────────────────────────────────────────────
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S%z",   # ISO 8601 with tz  e.g. 2025-05-13T14:30:00+05:30
    "%Y-%m-%dT%H:%M:%SZ",    # ISO 8601 UTC      e.g. 2025-05-13T14:30:00Z
    "%Y-%m-%dT%H:%M:%S",     # ISO 8601 no tz
    "%Y-%m-%d",               # date only         e.g. 2025-05-13
    "%d %b %Y",               # e.g. 13 May 2025
    "%b %d, %Y",              # e.g. May 13, 2025
    "%d-%m-%Y",               # e.g. 13-05-2025
]

_RELATIVE_PATTERNS = [
    # "2 days ago", "3 hours ago", "1 week ago", "Just now", "Today"
    (re.compile(r"(\d+)\s*hour",   re.I), "hours"),
    (re.compile(r"(\d+)\s*day",    re.I), "days"),
    (re.compile(r"(\d+)\s*week",   re.I), "weeks"),
    (re.compile(r"(\d+)\s*month",  re.I), "months"),
    (re.compile(r"just\s*now",     re.I), "now"),
    (re.compile(r"\btoday\b",      re.I), "today"),
    (re.compile(r"\byesterday\b",  re.I), "yesterday"),
]


def _parse_posted_at(raw: str) -> Optional[datetime]:
    """
    Try to parse posted_at string into an aware datetime.
    Returns None if the string cannot be parsed (→ benefit of doubt: keep the job).
    """
    if not raw:
        return None

    raw = raw.strip()
    now = datetime.now(timezone.utc)

    # ── Relative strings ──────────────────────────────────────────────────────
    for pattern, unit in _RELATIVE_PATTERNS:
        m = pattern.search(raw)
        if m:
            if unit == "now" or unit == "today":
                return now
            if unit == "yesterday":
                return now - timedelta(days=1)
            n = int(m.group(1))
            if unit == "hours":
                return now - timedelta(hours=n)
            if unit == "days":
                return now - timedelta(days=n)
            if unit == "weeks":
                return now - timedelta(weeks=n)
            if unit == "months":
                return now - timedelta(days=n * 30)

    # ── Absolute date strings ─────────────────────────────────────────────────
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            # Make timezone-aware if naive
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    return None  # unparseable → keep the job


def _apply_freshness_filter(jobs: list[dict]) -> list[dict]:
    """Keep jobs posted within FRESHNESS_HRS hours. Missing dates get a pass."""
    cutoff  = datetime.now(timezone.utc) - timedelta(hours=FRESHNESS_HRS)
    kept    = []
    dropped = 0

    for job in jobs:
        dt = _parse_posted_at(job.get("posted_at", ""))

        if dt is None:
            # Can't determine age → keep (benefit of doubt)
            kept.append(job)
            continue

        if dt >= cutoff:
            kept.append(job)
        else:
            dropped += 1

    log.info(
        f"[Filter] Step 3 — Freshness: kept {len(kept)}, "
        f"dropped {dropped} (older than {FRESHNESS_HRS}h)"
    )
    return kept


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 4 — MINIMUM SCORE FILTER
# ─────────────────────────────────────────────────────────────────────────────
def _apply_min_score(jobs: list[dict], min_score: float = MIN_SCORE) -> list[dict]:
    before   = len(jobs)
    filtered = [j for j in jobs if j.get("match_score", 0) >= min_score]
    dropped  = before - len(filtered)
    log.info(
        f"[Filter] Step 4 — Min-score ({min_score}): "
        f"kept {len(filtered)}, dropped {dropped}"
    )
    return filtered


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 5 — SORT & LIMIT
# ─────────────────────────────────────────────────────────────────────────────
def _sort_and_limit(jobs: list[dict], top_n: int = TOP_N) -> list[dict]:
    sorted_jobs = sorted(jobs, key=lambda j: j.get("match_score", 0), reverse=True)
    result      = sorted_jobs[:top_n]
    log.info(f"[Filter] Step 5 — Returning top {len(result)} jobs (sorted by score)")
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────
def run_filter(
    raw_jobs: list[dict],
    min_score: float = MIN_SCORE,
    top_n: int = TOP_N,
    profile: Optional[dict] = None,
) -> list[dict]:
    """
    Full 5-step filter pipeline.

    Args:
        raw_jobs:  Output from scraper.scrape_all()
        min_score: Minimum match_score to keep (default 30)
        top_n:     Maximum number of results to return (default 100)
        profile:   Preloaded profile dict; loaded from profile.json if None

    Returns:
        Filtered, scored, sorted list of job dicts with 'match_score' attached
    """
    total_raw = len(raw_jobs)
    log.info("=" * 60)
    log.info(f"🔍 Filter pipeline — {total_raw} raw jobs incoming")
    log.info("=" * 60)

    if not raw_jobs:
        log.warning("[Filter] Empty job list received — nothing to filter")
        return []

    # Load profile
    if profile is None:
        profile = _load_profile()

    skills       = _extract_skills(profile)
    target_roles = _extract_target_roles(profile)

    log.info(f"[Filter] Profile skills  : {len(skills)} total")
    log.info(f"[Filter] Target roles    : {target_roles}")

    # ── Step 1a: Remove already-applied (from Google Sheets) ──────────────────
    applied_urls = _get_applied_urls()
    jobs = _deduplicate_applied(raw_jobs, applied_urls)

    # ── Step 1b: Remove within-batch URL duplicates ───────────────────────────
    jobs = _deduplicate_within_batch(jobs)

    # ── Step 2: Score every job ───────────────────────────────────────────────
    jobs = _apply_scores(jobs, skills, target_roles)

    # ── Step 3: Freshness filter ──────────────────────────────────────────────
    jobs = _apply_freshness_filter(jobs)

    # ── Step 4: Minimum score ─────────────────────────────────────────────────
    jobs = _apply_min_score(jobs, min_score)

    # ── Step 5: Sort & limit ──────────────────────────────────────────────────
    jobs = _sort_and_limit(jobs, top_n)

    log.info("=" * 60)
    log.info(f"✅ Filtered {total_raw} raw jobs → {len(jobs)} quality matches")
    log.info("=" * 60)

    return jobs


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json as _json

    # ── Build a synthetic test dataset ────────────────────────────────────────
    mock_jobs = [
        # Should score HIGH — many matching skills + role boost
        {
            "title":     "Full Stack Developer",
            "company":   "TechCorp",
            "location":  "Bangalore",
            "jd_text":   "We need React, Java, Spring Boot, PostgreSQL, Docker, REST APIs, JWT Auth. FastAPI is a plus.",
            "apply_url": "https://linkedin.com/jobs/111",
            "source":    "linkedin",
            "posted_at": "1 hour ago",
        },
        # Should score MEDIUM — some skills match
        {
            "title":     "Backend Developer",
            "company":   "StartupXYZ",
            "location":  "Remote",
            "jd_text":   "Python, FastAPI, PostgreSQL, Docker experience required.",
            "apply_url": "https://naukri.com/jobs/222",
            "source":    "naukri",
            "posted_at": "3 hours ago",
        },
        # Should be DROPPED — score too low
        {
            "title":     "Marketing Analyst",
            "company":   "AdCo",
            "location":  "Mumbai",
            "jd_text":   "Excel, PowerPoint, Google Ads, SEO knowledge needed.",
            "apply_url": "https://internshala.com/jobs/333",
            "source":    "internshala",
            "posted_at": "2 days ago",
        },
        # Should be DROPPED — too old
        {
            "title":     "Java Developer",
            "company":   "OldCompany",
            "location":  "Hyderabad",
            "jd_text":   "Java, Spring Boot, MySQL, Microservices, Docker, JWT Auth",
            "apply_url": "https://wellfound.com/jobs/444",
            "source":    "wellfound",
            "posted_at": "5 days ago",
        },
        # Should PASS — missing posted_at gets benefit of doubt
        {
            "title":     "React Developer",
            "company":   "FreshStartup",
            "location":  "Pune",
            "jd_text":   "ReactJS, TypeScript, REST APIs, WebSockets, Supabase",
            "apply_url": "https://wellfound.com/jobs/555",
            "source":    "wellfound",
            "posted_at": "",   # unknown → keep
        },
    ]

    results = run_filter(mock_jobs)

    print(f"\n{'─'*60}")
    print(f"  {len(results)} jobs passed the filter")
    print(f"{'─'*60}")
    for i, job in enumerate(results, 1):
        print(
            f"\n[{i}] {job['title']} @ {job['company']}  "
            f"  score={job['match_score']}  "
            f"  matched={job['matched_skills']}"
        )
        print(f"     {job['apply_url']}")
