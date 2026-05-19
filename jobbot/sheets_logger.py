"""
sheets_logger.py
----------------
Logs every job application to a Google Sheet using gspread + service account auth.

Sheet: "Applications"
Columns: Date | Company | Role | Location | Source | Apply Method |
         Match Score | Status | Apply URL | Cover Letter Sent | Notes

Public API:
    setup_sheet()                                   -> None
    log_application(job, method, match_score)       -> None
    get_applied_urls()                              -> list[str]
    get_daily_stats()                               -> dict
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime, date
from pathlib import Path

import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sheets_logger")

# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────────────────────
SHEET_ID   = os.getenv("GOOGLE_SHEET_ID", "")
SHEET_NAME = "Applications"
LOGS_DIR   = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Date",
    "Company",
    "Role",
    "Location",
    "Source",
    "Apply Method",
    "Match Score",
    "Status",
    "Apply URL",
    "Cover Letter Sent",
    "Notes",
]

# Column indices (1-based) — update if you reorder HEADERS
COL_DATE        = 1
COL_COMPANY     = 2
COL_ROLE        = 3
COL_LOCATION    = 4
COL_SOURCE      = 5
COL_METHOD      = 6
COL_SCORE       = 7
COL_STATUS      = 8
COL_URL         = 9
COL_COVER       = 10
COL_NOTES       = 11

URL_COL_LETTER  = "I"   # Column I = Apply URL (used in range fetches)


# ─────────────────────────────────────────────────────────────────────────────
#  AUTH — SERVICE ACCOUNT
# ─────────────────────────────────────────────────────────────────────────────
def _get_credentials() -> Credentials:
    """
    Build service-account credentials from:
      1. GOOGLE_SHEETS_CREDENTIALS env var (raw JSON string)
      2. google_service_account.json file in project root
    """
    creds_json_str = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "")

    if creds_json_str:
        try:
            info = json.loads(creds_json_str)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            log.info("[Sheets] Auth via GOOGLE_SHEETS_CREDENTIALS env var ✓")
            return creds
        except (json.JSONDecodeError, ValueError) as e:
            log.error(f"[Sheets] Invalid GOOGLE_SHEETS_CREDENTIALS JSON: {e}")
            raise

    # Fallback: look for a JSON file on disk
    fallback_paths = [
        Path(__file__).parent / "google_service_account.json",
        Path(__file__).parent / "credentials_google.json",
    ]
    for p in fallback_paths:
        if p.exists():
            creds = Credentials.from_service_account_file(str(p), scopes=SCOPES)
            log.info(f"[Sheets] Auth via {p.name} ✓")
            return creds

    raise FileNotFoundError(
        "No Google service account credentials found. "
        "Set GOOGLE_SHEETS_CREDENTIALS in .env or place "
        "google_service_account.json in the project folder."
    )


def _get_client() -> gspread.Client:
    creds = _get_credentials()
    return gspread.authorize(creds)


# ─────────────────────────────────────────────────────────────────────────────
#  WORKSHEET ACCESSOR  (with auto-retry on transient errors)
# ─────────────────────────────────────────────────────────────────────────────
def _get_worksheet(retries: int = 3) -> gspread.Worksheet:
    """
    Open the 'Applications' worksheet, creating it if it doesn't exist.
    Retries up to `retries` times on API errors.
    """
    for attempt in range(1, retries + 1):
        try:
            client      = _get_client()
            spreadsheet = client.open_by_key(SHEET_ID)

            try:
                ws = spreadsheet.worksheet(SHEET_NAME)
            except WorksheetNotFound:
                log.info(f"[Sheets] Worksheet '{SHEET_NAME}' not found — creating …")
                ws = spreadsheet.add_worksheet(
                    title=SHEET_NAME, rows=2000, cols=len(HEADERS)
                )

            return ws

        except SpreadsheetNotFound:
            raise RuntimeError(
                f"Spreadsheet '{SHEET_ID}' not found. "
                "Make sure GOOGLE_SHEET_ID is correct and the service account "
                "has Editor access to the sheet."
            )
        except APIError as e:
            if attempt < retries:
                wait = 2 ** attempt
                log.warning(f"[Sheets] API error (attempt {attempt}): {e} — retrying in {wait}s")
                time.sleep(wait)
            else:
                raise


# ─────────────────────────────────────────────────────────────────────────────
#  1. SETUP SHEET
# ─────────────────────────────────────────────────────────────────────────────
def setup_sheet() -> None:
    """
    Ensure the 'Applications' worksheet exists and has the correct header row.
    Idempotent — safe to call on every run.
    """
    ws = _get_worksheet()
    existing = ws.row_values(1)

    if existing == HEADERS:
        log.info("[Sheets] Headers already in place ✓")
        return

    if not existing:
        # Empty sheet — write headers
        ws.update("A1", [HEADERS])
        _format_header_row(ws)
        log.info("[Sheets] ✅ Header row created")
    else:
        log.warning(
            f"[Sheets] Row 1 exists but doesn't match expected headers. "
            f"Found: {existing[:3]}… — leaving as-is to avoid data loss."
        )


def _format_header_row(ws: gspread.Worksheet) -> None:
    """Bold + freeze the header row."""
    try:
        ws.format("A1:K1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.23, "green": 0.47, "blue": 0.85},
        })
        ws.freeze(rows=1)
    except Exception as e:
        log.debug(f"[Sheets] Header formatting skipped: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  2. LOG APPLICATION
# ─────────────────────────────────────────────────────────────────────────────
def log_application(
    job: dict,
    method: str = "Manual",
    match_score: float = 0.0,
) -> None:
    """
    Append one row to the Applications sheet.

    Args:
        job:         Job dict (title, company, location, source, apply_url, cover_letter)
        method:      "EasyApply" | "Email" | "Manual"
        match_score: Float score from filter.py (0–120)
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    cover_sent = "Yes" if job.get("cover_letter") or job.get("email_sent") else "No"

    # Status reflects actual application success
    status = "Applied"
    if method == "DryRun":
        status = "DryRun"
    elif method == "Manual":
        status = "Manual"

    row = [
        now,
        job.get("company",   ""),
        job.get("title",     ""),
        job.get("location",  ""),
        job.get("source",    ""),
        method,
        round(match_score, 1),
        status,
        job.get("apply_url", ""),
        cover_sent,
        "",  # Notes — filled manually later
    ]

    try:
        ws = _get_worksheet()
        ws.append_row(row, value_input_option="USER_ENTERED")
        log.info(
            f"[Sheets] ✅ Logged: {job.get('title')} @ {job.get('company')} "
            f"[{method}] score={match_score}"
        )
    except Exception as e:
        log.error(f"[Sheets] Failed to log application: {e}")


def run_sheets_logger(jobs: list[dict]) -> None:
    """
    Log all jobs in the list to Google Sheets.
    Determines Apply Method from job flags set by apply_bot / email_bot.

    Args:
        jobs: Job list with 'applied', 'email_sent', 'match_score' attached
    """
    setup_sheet()
    logged = 0

    for job in jobs:
        # Determine apply method
        if job.get("applied"):
            method = "EasyApply"
        elif job.get("email_sent"):
            method = "Email"
        else:
            method = "Manual"

        try:
            log_application(
                job,
                method=method,
                match_score=job.get("match_score", 0.0),
            )
            logged += 1
            time.sleep(0.5)   # stay within Sheets API quota (60 writes/min)
        except Exception as e:
            log.error(f"[Sheets] Skipping {job.get('title')}: {e}")

    log.info(f"[Sheets] ✅ {logged}/{len(jobs)} applications logged")


# ─────────────────────────────────────────────────────────────────────────────
#  3. GET APPLIED URLs
# ─────────────────────────────────────────────────────────────────────────────
def get_applied_urls() -> list[str]:
    """
    Return all non-empty values from the 'Apply URL' column.
    Used by filter.py to skip already-applied jobs.
    """
    # Only count as "applied" if the bot actually succeeded.
    # DryRun  = just a test run      → retry for real
    # Manual  = bot couldn't apply   → retry tonight
    # EasyApply / Email = real apply → skip to avoid duplicates
    ACTUALLY_APPLIED = {"EasyApply", "Email"}
    try:
        ws   = _get_worksheet()
        rows = ws.get_all_values()
        if len(rows) < 2:
            return []

        urls = []
        for row in rows[1:]:
            if len(row) >= max(COL_URL, COL_METHOD):
                url    = row[COL_URL    - 1].strip()
                method = row[COL_METHOD - 1].strip()
                if url and method in ACTUALLY_APPLIED:
                    urls.append(url)

        log.info(f"[Sheets] Loaded {len(urls)} actually-applied URLs from Sheets")
        return urls
    except Exception as e:
        log.warning(f"[Sheets] Could not load applied URLs: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  4. DAILY STATS
# ─────────────────────────────────────────────────────────────────────────────
def get_daily_stats() -> dict:
    """
    Return a summary of today's applications from the sheet.

    Returns:
        {
            "date":       "2026-05-13",
            "total":      15,
            "EasyApply":  8,
            "Email":      5,
            "Manual":     2,
            "top_companies": ["Google", "Razorpay", ...]
        }
    """
    today_str = date.today().strftime("%Y-%m-%d")
    stats = {
        "date":          today_str,
        "total":         0,
        "EasyApply":     0,
        "Email":         0,
        "Manual":        0,
        "top_companies": [],
    }

    try:
        ws       = _get_worksheet()
        all_rows = ws.get_all_values()

        if not all_rows or len(all_rows) < 2:
            log.info("[Sheets] No data rows found")
            return stats

        header = all_rows[0]
        rows   = all_rows[1:]

        # Map header names → column indices
        try:
            date_idx   = header.index("Date")
            method_idx = header.index("Apply Method")
            company_idx= header.index("Company")
        except ValueError as e:
            log.warning(f"[Sheets] Header mismatch: {e}")
            return stats

        companies = []
        for row in rows:
            if len(row) <= max(date_idx, method_idx, company_idx):
                continue

            row_date = row[date_idx][:10]   # "YYYY-MM-DD HH:MM" → "YYYY-MM-DD"
            if row_date != today_str:
                continue

            stats["total"] += 1
            method = row[method_idx]
            if method in stats:
                stats[method] += 1

            company = row[company_idx].strip()
            if company:
                companies.append(company)

        stats["top_companies"] = companies[:10]

        log.info(
            f"[Sheets] Daily stats — Total: {stats['total']} | "
            f"EasyApply: {stats['EasyApply']} | "
            f"Email: {stats['Email']} | "
            f"Manual: {stats['Manual']}"
        )

    except Exception as e:
        log.error(f"[Sheets] Could not compute daily stats: {e}")

    return stats


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n── Sheets Logger — Standalone Test ──\n")

    # 1. Setup sheet
    print("Step 1: Setting up sheet …")
    setup_sheet()

    # 2. Log a test application
    print("\nStep 2: Logging a test application …")
    test_job = {
        "title":        "Full Stack Developer",
        "company":      "TestCorp",
        "location":     "Bangalore / Remote",
        "source":       "linkedin",
        "apply_url":    "https://linkedin.com/jobs/test-99999",
        "match_score":  87.5,
        "applied":      True,
        "email_sent":   False,
        "cover_letter": "Dear TestCorp team, I am excited to apply …",
    }
    log_application(test_job, method="EasyApply", match_score=87.5)

    # 3. Fetch applied URLs
    print("\nStep 3: Fetching applied URLs …")
    urls = get_applied_urls()
    print(f"  Found {len(urls)} applied URLs")
    for u in urls[:5]:
        print(f"  · {u}")

    # 4. Daily stats
    print("\nStep 4: Daily stats …")
    stats = get_daily_stats()
    print(f"  Date       : {stats['date']}")
    print(f"  Total      : {stats['total']}")
    print(f"  EasyApply  : {stats['EasyApply']}")
    print(f"  Email      : {stats['Email']}")
    print(f"  Manual     : {stats['Manual']}")
    print(f"  Companies  : {stats['top_companies'][:5]}")
