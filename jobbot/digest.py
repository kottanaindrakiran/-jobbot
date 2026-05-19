"""
digest.py
---------
Sends a nightly summary email to kottanaindrakiran@gmail.com
via Gmail API (reuses the same OAuth2 flow as email_bot.py).

Public API:
    send_digest(stats, top_jobs, profile=None) -> None
"""

import base64
import json
import logging
import os
from datetime import date
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("digest")

LOGS_DIR   = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)
TOKEN_PATH = LOGS_DIR / "token_gmail.json"
CREDS_PATH = Path(__file__).parent / "credentials_gmail.json"
SCOPES     = ["https://www.googleapis.com/auth/gmail.send"]

DIGEST_TO  = "kottanaindrakiran@gmail.com"
SHEET_ID   = os.getenv("GOOGLE_SHEET_ID", "")
SHEET_URL  = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"


# ─────────────────────────────────────────────────────────────────────────────
#  AUTH  (shared token with email_bot.py)
# ─────────────────────────────────────────────────────────────────────────────
def _get_gmail_service():
    # Bootstrap credentials file from env var if needed
    creds_json_str = os.getenv("GMAIL_CREDENTIALS_JSON", "")
    if creds_json_str and not CREDS_PATH.exists():
        try:
            CREDS_PATH.write_text(
                json.dumps(json.loads(creds_json_str), indent=2)
            )
            log.info(f"[Digest] Credentials written from env → {CREDS_PATH}")
        except json.JSONDecodeError as e:
            log.error(f"[Digest] Invalid GMAIL_CREDENTIALS_JSON: {e}")
            raise

    if not CREDS_PATH.exists():
        raise FileNotFoundError(
            f"Gmail credentials not found at {CREDS_PATH}. "
            "Set GMAIL_CREDENTIALS_JSON in .env or place credentials_gmail.json here."
        )

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow  = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ─────────────────────────────────────────────────────────────────────────────
#  BODY BUILDER
# ─────────────────────────────────────────────────────────────────────────────
def _build_body(stats: dict, top_jobs: list) -> str:
    today      = date.today().strftime("%d %B %Y")
    total_sent = stats.get("applied", 0) + stats.get("emailed", 0)

    # Top companies block
    if top_jobs:
        lines = []
        for i, job in enumerate(top_jobs[:10], 1):
            score  = job.get("match_score", 0)
            method = (
                "🧪 DryRun"   if job.get("dry_run")    else
                "✅ Applied"  if job.get("applied")    else
                "📧 Emailed"  if job.get("email_sent") else
                "⏭  Skipped"
            )
            lines.append(
                f"  {i:2}. {job.get('company', 'N/A'):25s}"
                f"  {job.get('title', 'N/A'):30s}"
                f"  score={score:<5.1f}  {method}"
            )
        top_companies_block = "\n".join(lines)
    else:
        top_companies_block = "  (no jobs processed)"

    return f"""JobBot Nightly Report — {today}
{'─' * 55}

Summary:
  Auto-applied (LinkedIn / Naukri) : {stats.get('applied',  0)}
  Cold emails sent                 : {stats.get('emailed',  0)}
  Failed / errored                 : {stats.get('failed',   0)}
  Skipped (no email / manual)      : {stats.get('skipped',  0)}
  ─────────────────────────────────────
  Total applications sent          : {total_sent}

Top companies applied tonight:
{top_companies_block}

View full tracker:
  {SHEET_URL}

{'─' * 55}
Keep going bro, interviews incoming. 🚀
{'─' * 55}

— JobBot
"""


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────
def send_digest(
    stats: dict,
    top_jobs: list,
    profile: dict | None = None,
) -> None:
    """
    Send a nightly summary email to kottanaindrakiran@gmail.com.

    Args:
        stats:    Dict with keys: applied, emailed, failed, skipped, scraped, filtered
        top_jobs: List of top job dicts to display (up to 10 shown)
        profile:  Unused — kept for API compatibility with main.py signature
    """
    today     = date.today().isoformat()
    total     = stats.get("applied", 0) + stats.get("emailed", 0)
    subject   = f"JobBot Report — {today} — {total} applications sent"
    body      = _build_body(stats, top_jobs)

    log.info(f"[Digest] Sending digest to {DIGEST_TO} …")
    log.info(f"[Digest] Subject: {subject}")

    try:
        service = _get_gmail_service()

        msg             = MIMEText(body, "plain", "utf-8")
        msg["From"]     = DIGEST_TO   # send from self → to self
        msg["To"]       = DIGEST_TO
        msg["Subject"]  = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        log.info(f"[Digest] ✅ Digest sent successfully")

    except HttpError as e:
        log.error(f"[Digest] Gmail API error {e.resp.status}: {e}")
    except FileNotFoundError as e:
        log.error(f"[Digest] Auth error: {e}")
    except Exception as e:
        log.error(f"[Digest] Unexpected error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mock_stats = {
        "scraped":  214,
        "filtered": 52,
        "applied":  18,
        "emailed":  7,
        "failed":   2,
        "skipped":  25,
    }
    mock_jobs = [
        {"title": "Full Stack Developer", "company": "Razorpay",   "match_score": 94.5, "applied": True,  "email_sent": False},
        {"title": "Backend Developer",    "company": "Zepto",       "match_score": 88.0, "applied": True,  "email_sent": False},
        {"title": "AI Engineer",          "company": "Sarvam AI",   "match_score": 91.2, "applied": False, "email_sent": True},
        {"title": "SDE-1",                "company": "Swiggy",      "match_score": 82.5, "applied": True,  "email_sent": False},
        {"title": "React Developer",      "company": "CRED",        "match_score": 79.0, "applied": False, "email_sent": True},
        {"title": "Java Developer",       "company": "PhonePe",     "match_score": 85.5, "applied": True,  "email_sent": False},
    ]

    # Preview the email body first
    print(_build_body(mock_stats, mock_jobs))

    # Uncomment to actually send:
    # send_digest(mock_stats, mock_jobs)
