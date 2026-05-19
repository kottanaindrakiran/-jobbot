"""
main.py
-------
JobBot — End-to-end automated job application pipeline.
Run this every night via Task Scheduler.

Pipeline:
  1. Scrape    → raw jobs from LinkedIn, Naukri, Wellfound, Internshala
  2. Filter    → deduplicate, score, rank, keep top matches
  3. Per job   → resume (Claude) → cover letter (Claude)
                 → auto-apply (LinkedIn Easy Apply / Naukri)
                 → cold email if apply failed
                 → log to Google Sheets
  4. Digest    → send nightly summary email to self
"""

import json
import logging
import os
import random
import sys
import time
import traceback
from datetime import datetime, date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
#  PATHS
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
LOGS_DIR    = BASE_DIR / "logs"
PROFILE_PATH= BASE_DIR / "profile.json"
LOGS_DIR.mkdir(exist_ok=True)

TODAY       = date.today().isoformat()
LOG_FILE    = LOGS_DIR / f"jobbot_{TODAY}.log"

# ─────────────────────────────────────────────────────────────────────────────
#  LOGGING — both console and file
# ─────────────────────────────────────────────────────────────────────────────
def _setup_logging():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass  # sys.stdout might not support reconfigure in some exotic environments
    fmt = "%(asctime)s  [%(levelname)-8s]  %(name)s — %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
        ],
    )
    # Quiet noisy third-party loggers
    for noisy in ("urllib3", "httpcore", "httpx", "playwright",
                  "googleapiclient", "google.auth"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

_setup_logging()
log = logging.getLogger("main")

# ─────────────────────────────────────────────────────────────────────────────
#  IMPORTS (after logging is set up so module loggers inherit config)
# ─────────────────────────────────────────────────────────────────────────────
try:
    import scraper
    import filter as job_filter       # 'filter' shadows built-in — alias it
    import resume_gen
    import cover_gen
    import apply_bot
    import email_bot
    import sheets_logger
    import digest
except ImportError as e:
    log.critical(f"Failed to import module: {e}")
    log.critical("Run:  pip install -r requirements.txt")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
#  PROFILE
# ─────────────────────────────────────────────────────────────────────────────
def _load_profile() -> dict:
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)

# ─────────────────────────────────────────────────────────────────────────────
#  PIPELINE CONFIG  (tweak these to control the run)
# ─────────────────────────────────────────────────────────────────────────────
CONFIG = {
    "dry_run":           False,   # True → no actual applies / emails sent
    "max_applies":       30,      # stop auto-applying after this many
    "max_emails":        20,      # stop cold-emailing after this many
    "sleep_between":     (5, 10), # seconds between jobs (randomised)
    "sources":           ["linkedin", "naukri", "wellfound", "internshala"],
    "min_score":         15,
    "top_n":             100,
}

# ─────────────────────────────────────────────────────────────────────────────
#  BANNER
# ─────────────────────────────────────────────────────────────────────────────
def _banner(text: str, width: int = 60):
    log.info("=" * width)
    log.info(f"  {text}")
    log.info("=" * width)

def _step(n: int, title: str):
    log.info("")
    log.info(f"── Step {n}: {title} " + "─" * max(0, 50 - len(title)))

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
def main():
    start_time = datetime.now()

    _banner(f"🤖 JobBot Starting  —  {TODAY}")
    log.info(f"  Log file : {LOG_FILE}")
    log.info(f"  Dry run  : {CONFIG['dry_run']}")
    log.info(f"  Sources  : {CONFIG['sources']}")

    profile = _load_profile()
    log.info(f"  Candidate: {profile.get('name')}  |  {profile.get('email')}")

    stats = {
        "scraped":  0,
        "filtered": 0,
        "applied":  0,
        "emailed":  0,
        "failed":   0,
        "skipped":  0,
    }

    # ── Step 1: Scrape ────────────────────────────────────────────────────────
    _step(1, "Scrape")
    try:
        raw_jobs = scraper.scrape_all(
            roles=profile.get("target_roles", []),
            sources=CONFIG["sources"],
        )
        stats["scraped"] = len(raw_jobs)
        log.info(f"Scraped {stats['scraped']} jobs from all sources")
    except Exception as e:
        log.critical(f"Scraper crashed: {e}\n{traceback.format_exc()}")
        sys.exit(1)

    if not raw_jobs:
        log.warning("No jobs scraped — exiting early")
        return

    # ── Step 2: Filter ────────────────────────────────────────────────────────
    _step(2, "Filter & Rank")
    try:
        filtered_jobs = job_filter.run_filter(
            raw_jobs,
            min_score=CONFIG["min_score"],
            top_n=CONFIG["top_n"],
            profile=profile,
        )
        stats["filtered"] = len(filtered_jobs)
        log.info(f"Filtered to {stats['filtered']} quality matches")
    except Exception as e:
        log.critical(f"Filter crashed: {e}\n{traceback.format_exc()}")
        sys.exit(1)

    if not filtered_jobs:
        log.warning("No jobs passed the filter — exiting early")
        return

    # ── Step 3: Setup Google Sheet ────────────────────────────────────────────
    _step(3, "Setup Google Sheets")
    try:
        sheets_logger.setup_sheet()
    except Exception as e:
        log.warning(f"Sheets setup failed (will retry per-log): {e}")

    # ── Step 4: Per-job pipeline ──────────────────────────────────────────────
    _step(4, f"Process {stats['filtered']} Jobs")
    log.info(f"  Apply cap : {CONFIG['max_applies']}  |  Email cap: {CONFIG['max_emails']}")

    apply_count = 0
    email_count = 0

    for idx, job in enumerate(filtered_jobs, 1):
        title   = job.get("title",   "Unknown Role")
        company = job.get("company", "Unknown Company")
        source  = job.get("source",  "unknown")
        score   = job.get("match_score", 0.0)

        log.info("")
        log.info(f"[{idx}/{stats['filtered']}] {title} @ {company}  [{source}]  score={score}")

        try:
            # ── 4a: Generate tailored resume ──────────────────────────────────
            if CONFIG["dry_run"]:
                resume_path = ""
                log.info(f"  [DRY RUN] Skipped resume generation")
            else:
                resume_path = resume_gen.generate_resume(job, profile)
                job["resume_path"] = resume_path
                log.info(f"  Resume → {Path(resume_path).name}")

            # ── 4b: Generate cover letter ─────────────────────────────────────
            if CONFIG["dry_run"]:
                cover = "[DRY RUN cover letter]"
                log.info(f"  [DRY RUN] Skipped cover letter generation")
            else:
                cover = cover_gen.generate_cover_letter(job)
                job["cover_letter"] = cover
                log.info(f"  Cover letter generated ({len(cover.split())} words)")

            applied = False
            emailed = False

            if not CONFIG["dry_run"]:

                # ── 4c: Auto-apply (LinkedIn → Naukri fallback) ───────────────
                if apply_count < CONFIG["max_applies"]:
                        if source == "linkedin":
                            log.info("  Trying LinkedIn Easy Apply …")
                            applied = apply_bot.apply_linkedin(job, resume_path, profile)

                        elif source == "naukri":
                            log.info("  Trying Naukri Apply …")
                            applied = apply_bot.apply_naukri(job, resume_path, profile)

                        elif source == "internshala":
                            log.info("  Trying Internshala Apply …")
                            applied = apply_bot.apply_internshala(job, resume_path, profile)

                        elif source == "wellfound":
                            log.info("  Trying Wellfound Apply …")
                            applied = apply_bot.apply_wellfound(job, resume_path, profile)

                        else:
                            log.info(f"  Source '{source}' not supported for auto-apply")

                        if applied:
                            apply_count += 1
                            stats["applied"] += 1
                            job["applied"] = True
                            log.info(f"  ✅ Applied via bot ({apply_count}/{CONFIG['max_applies']})")
                            try:
                                sheets_logger.log_application(job, "EasyApply", score)
                            except Exception as se:
                                log.warning(f"  Sheets log failed: {se}")
                else:
                    log.info(f"  Apply cap reached ({CONFIG['max_applies']}) — skipping bot apply")


                # ── 4d: Cold email if apply failed / unsupported source ───────
                if not applied:
                    if email_count < CONFIG["max_emails"]:
                        log.info("  Trying cold email …")
                        emailed = email_bot.send_cold_email(job, cover, resume_path, profile)

                        if emailed:
                            email_count += 1
                            stats["emailed"] += 1
                            job["email_sent"] = True
                            log.info(f"  ✅ Cold email sent ({email_count}/{CONFIG['max_emails']})")
                            try:
                                sheets_logger.log_application(job, "Email", score)
                            except Exception as se:
                                log.warning(f"  Sheets log failed: {se}")
                        else:
                            log.info("  No recruiter email found — marking Manual")
                            stats["skipped"] += 1
                            try:
                                sheets_logger.log_application(job, "Manual", score)
                            except Exception as se:
                                log.warning(f"  Sheets log failed: {se}")
                    else:
                        log.info(f"  Email cap reached ({CONFIG['max_emails']}) — skipping email")
                        stats["skipped"] += 1

            else:
                # DRY RUN — just log
                log.info(f"  [DRY RUN] Would apply to: {title} @ {company}")
                job["dry_run"] = True
                try:
                    sheets_logger.log_application(job, "DryRun", score)
                except Exception:
                    pass

        except KeyboardInterrupt:
            log.warning("\nKeyboardInterrupt — stopping job loop cleanly")
            break

        except Exception as e:
            log.error(f"  ❌ Error on {company}: {e}")
            log.debug(traceback.format_exc())
            stats["failed"] += 1
            continue

        # ── Polite delay between jobs ─────────────────────────────────────────
        if idx < stats["filtered"]:
            delay = random.uniform(*CONFIG["sleep_between"])
            log.info(f"  Sleeping {delay:.1f}s …")
            time.sleep(delay)

    # ── Step 5: Nightly digest ────────────────────────────────────────────────
    _step(5, "Send Nightly Digest")
    try:
        digest.send_digest(stats, filtered_jobs[:10], profile)
    except Exception as e:
        log.error(f"Digest failed: {e}")

    # ── Final summary ─────────────────────────────────────────────────────────
    elapsed = (datetime.now() - start_time).seconds
    mins, secs = divmod(elapsed, 60)

    log.info("")
    _banner("🏁 JobBot Finished")
    log.info(f"  Runtime  : {mins}m {secs}s")
    log.info(f"  Scraped  : {stats['scraped']}")
    log.info(f"  Filtered : {stats['filtered']}")
    log.info(f"  Applied  : {stats['applied']}  (bot)")
    log.info(f"  Emailed  : {stats['emailed']}  (cold email)")
    log.info(f"  Skipped  : {stats['skipped']}  (manual / no email)")
    log.info(f"  Failed   : {stats['failed']}   (errors)")
    log.info(f"  Log file : {LOG_FILE}")
    log.info("=" * 60)

    return stats


# ─────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="JobBot — Automated Job Application Pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape + filter + generate, but do NOT apply or send emails",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=["linkedin", "naukri", "wellfound", "internshala"],
        choices=["linkedin", "naukri", "wellfound", "internshala"],
        help="Which sources to scrape (default: all)",
    )
    parser.add_argument(
        "--max-applies",
        type=int,
        default=30,
        help="Max number of bot applications per run (default: 30)",
    )
    parser.add_argument(
        "--max-emails",
        type=int,
        default=20,
        help="Max cold emails per run (default: 20)",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=15.0,
        help="Minimum match score to process a job (default: 15)",
    )

    args = parser.parse_args()

    # Apply CLI overrides to config
    CONFIG["dry_run"]     = args.dry_run
    CONFIG["sources"]     = args.sources
    CONFIG["max_applies"] = args.max_applies
    CONFIG["max_emails"]  = args.max_emails
    CONFIG["min_score"]   = args.min_score

    main()
