"""
email_bot.py
------------
Sends personalised cold emails via Gmail API with resume PDF attached.

Features:
  - Extracts recruiter/company email from jd_text or apply_url
  - Builds MIME multipart message with cover letter body + PDF attachment
  - OAuth2 flow with token.json caching
  - Daily send counter (max 500/day) persisted in logs/email_counter.json
  - Graceful fallback — never crashes the pipeline

Public API:
    send_cold_email(job, cover_letter, resume_pdf_path)  -> bool
    run_email_bot(jobs)                                  -> jobs (with 'email_sent')
"""

import base64
import json
import logging
import os
import re
import time
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
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
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  [%(levelname)s]  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("email_bot")

PROFILE_PATH  = Path(__file__).parent / "profile.json"
LOGS_DIR      = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)

TOKEN_PATH    = LOGS_DIR / "token_gmail.json"
COUNTER_PATH  = LOGS_DIR / "email_counter.json"
CREDS_PATH    = Path(__file__).parent / "credentials_gmail.json"

SCOPES        = ["https://www.googleapis.com/auth/gmail.send"]
DAILY_LIMIT   = 490   # stay safely under Gmail's 500/day


# ─────────────────────────────────────────────────────────────────────────────
#  PROFILE
# ─────────────────────────────────────────────────────────────────────────────
def _load_profile() -> dict:
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
#  DAILY COUNTER
# ─────────────────────────────────────────────────────────────────────────────
def _load_counter() -> dict:
    today = str(date.today())
    if COUNTER_PATH.exists():
        data = json.loads(COUNTER_PATH.read_text())
        if data.get("date") == today:
            return data
    return {"date": today, "count": 0}


def _save_counter(counter: dict):
    COUNTER_PATH.write_text(json.dumps(counter, indent=2))


def _can_send() -> bool:
    counter = _load_counter()
    if counter["count"] >= DAILY_LIMIT:
        log.warning(f"[EmailBot] Daily limit reached ({DAILY_LIMIT} emails). Skipping.")
        return False
    return True


def _increment_counter():
    counter = _load_counter()
    counter["count"] += 1
    _save_counter(counter)
    log.info(f"[EmailBot] Daily send count: {counter['count']}/{DAILY_LIMIT}")


# ─────────────────────────────────────────────────────────────────────────────
#  GMAIL AUTH
# ─────────────────────────────────────────────────────────────────────────────
def _get_gmail_service():
    """
    Authenticate via OAuth2 and return Gmail API service.
    Reads credentials from:
      1. GMAIL_CREDENTIALS_JSON env var (JSON string) → writes to credentials_gmail.json
      2. credentials_gmail.json file directly
    Token cached in logs/token_gmail.json.
    """
    # Write credentials file from env var if provided
    creds_json_str = os.getenv("GMAIL_CREDENTIALS_JSON", "")
    if creds_json_str and not CREDS_PATH.exists():
        try:
            parsed = json.loads(creds_json_str)
            CREDS_PATH.write_text(json.dumps(parsed, indent=2))
            log.info(f"[EmailBot] Credentials written from env → {CREDS_PATH}")
        except json.JSONDecodeError:
            log.error("[EmailBot] GMAIL_CREDENTIALS_JSON is not valid JSON")
            raise

    if not CREDS_PATH.exists():
        raise FileNotFoundError(
            f"Gmail credentials not found at {CREDS_PATH}. "
            "Set GMAIL_CREDENTIALS_JSON in .env or place credentials_gmail.json in the project folder."
        )

    creds = None

    # Load cached token
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    # Refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("[EmailBot] Refreshing Gmail token …")
            creds.refresh(Request())
        else:
            log.info("[EmailBot] Starting OAuth2 flow (browser will open) …")
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
        log.info(f"[EmailBot] Token saved → {TOKEN_PATH}")

    return build("gmail", "v1", credentials=creds)


# ─────────────────────────────────────────────────────────────────────────────
#  EMAIL EXTRACTOR
# ─────────────────────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Domains we definitely don't want to email
_SKIP_DOMAINS = {
    "sentry.io", "example.com", "placeholder.com",
    "linkedin.com", "naukri.com", "wellfound.com",
    "internshala.com", "google.com", "github.com",
}


def _extract_recruiter_email(job: dict) -> str | None:
    """
    Look for a recruiter/company email in jd_text and apply_url.
    Returns the first valid-looking email, or None.
    """
    search_text = f"{job.get('jd_text', '')} {job.get('apply_url', '')}"
    matches = _EMAIL_RE.findall(search_text)

    for email in matches:
        domain = email.split("@")[-1].lower()
        if domain not in _SKIP_DOMAINS and not domain.endswith(".png"):
            log.info(f"[EmailBot] Found recruiter email: {email}")
            return email

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  MESSAGE BUILDER
# ─────────────────────────────────────────────────────────────────────────────
def _build_email_body(cover_letter: str) -> str:
    return f"""Hi Hiring Team,

{cover_letter}

My profile:
- GitHub:   https://github.com/kottanaindrakiran
- LinkedIn: https://www.linkedin.com/in/indra-kiran-kottana/
- LeetCode: https://leetcode.com/u/kottanaindrakiran/

Resume attached. Looking forward to hearing from you.

Best regards,
Kottana Indra Kiran
+91 7382538122"""


def _build_subject(job: dict) -> str:
    title = job.get("title", "Software Developer")
    return (
        f"Indra Kiran | {title} | SRM CSE 2026 | "
        f"Java + React + AI | GitHub: github.com/kottanaindrakiran"
    )


def _build_mime_message(
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    resume_pdf_path: str,
) -> dict:
    """Build a base64-encoded Gmail API message dict with PDF attachment."""
    msg = MIMEMultipart()
    msg["From"]    = sender
    msg["To"]      = recipient
    msg["Subject"] = subject

    # Plain-text body
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # PDF attachment
    pdf_path = Path(resume_pdf_path)
    if pdf_path.exists():
        with open(pdf_path, "rb") as f:
            part = MIMEApplication(f.read(), _subtype="pdf")
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=pdf_path.name,
            )
            msg.attach(part)
        log.info(f"[EmailBot] Attached: {pdf_path.name}")
    else:
        log.warning(f"[EmailBot] Resume PDF not found at {resume_pdf_path} — sending without attachment")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────
def send_cold_email(
    job: dict,
    cover_letter: str,
    resume_pdf_path: str,
    profile: dict | None = None,
) -> bool:
    """
    Send a personalised cold email for the given job.

    Args:
        job:             Job dict (needs 'title', 'company', 'jd_text', 'apply_url')
        cover_letter:    Generated cover letter text (from cover_gen)
        resume_pdf_path: Absolute path to the tailored resume PDF
        profile:         Preloaded profile; loaded from profile.json if None

    Returns:
        True  — email sent successfully
        False — no recruiter email found, daily limit hit, or send error
    """
    if profile is None:
        profile = _load_profile()

    # ── Guard: daily limit ────────────────────────────────────────────────────
    if not _can_send():
        return False

    # ── Guard: find a recruiter email ─────────────────────────────────────────
    recruiter_email = _extract_recruiter_email(job)
    if not recruiter_email:
        log.info(
            f"[EmailBot] No recruiter email in JD for "
            f"'{job.get('title')} @ {job.get('company')}' — skipping"
        )
        return False

    sender  = profile.get("email", "kottanaindrakiran@gmail.com")
    subject = _build_subject(job)
    body    = _build_email_body(cover_letter)

    log.info(
        f"[EmailBot] Sending → {recruiter_email}  |  "
        f"{job.get('title')} @ {job.get('company')}"
    )

    try:
        service = _get_gmail_service()
        message = _build_mime_message(sender, recruiter_email, subject, body, resume_pdf_path)
        service.users().messages().send(userId="me", body=message).execute()

        _increment_counter()
        job["recruiter_email"] = recruiter_email
        log.info(f"[EmailBot] ✅ Email sent to {recruiter_email}")
        return True

    except HttpError as e:
        if e.resp.status == 429:
            log.warning("[EmailBot] Gmail rate limit (429) — pausing 60s")
            time.sleep(60)
        else:
            log.error(f"[EmailBot] Gmail API error {e.resp.status}: {e}")
        return False
    except Exception as e:
        log.error(f"[EmailBot] Unexpected error: {e}")
        return False


def run_email_bot(
    jobs: list[dict],
    profile: dict | None = None,
    delay: float = 3.0,
) -> list[dict]:
    """
    Send cold emails for all jobs that have a recruiter email in the JD.
    Attaches 'email_sent': True/False to each job dict.

    Args:
        jobs:    Job list (must have 'cover_letter' and 'resume_path' attached)
        profile: Preloaded profile dict; loaded from profile.json if None
        delay:   Seconds between sends (default 3s)

    Returns:
        Same jobs list with 'email_sent' key added.
    """
    if profile is None:
        profile = _load_profile()

    log.info(f"[EmailBot] Starting cold email run for {len(jobs)} jobs …")

    # Show current daily counter
    counter = _load_counter()
    log.info(f"[EmailBot] Today's send count so far: {counter['count']}/{DAILY_LIMIT}")

    for i, job in enumerate(jobs, 1):
        log.info(f"[EmailBot] [{i}/{len(jobs)}] {job.get('title')} @ {job.get('company')}")

        cover_letter = job.get("cover_letter", "")
        resume_path  = job.get("resume_path",  "")

        job["email_sent"] = send_cold_email(
            job, cover_letter, resume_path, profile
        )

        if job["email_sent"] and i < len(jobs):
            time.sleep(delay)

    sent = sum(1 for j in jobs if j.get("email_sent"))
    log.info(f"[EmailBot] ✅ {sent} cold emails sent out of {len(jobs)} jobs")
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_jobs = [
        {
            "title":       "Full Stack Developer",
            "company":     "TechCorp",
            "jd_text":     "Contact us at careers@techcorp.io. React, Java, Spring Boot required.",
            "apply_url":   "https://techcorp.io/jobs/123",
            "cover_letter": (
                "Dear TechCorp Hiring Team,\n\n"
                "I built AcadNet — a Spring Boot / React platform — which maps directly "
                "to your full-stack requirements. I'm excited about TechCorp's mission "
                "and would love to contribute to your engineering team.\n\n"
                "I'd love to discuss this opportunity further."
            ),
            "resume_path": str(next(Path("resumes").glob("*.pdf"), "")),
        },
        {
            "title":       "AI Engineer",
            "company":     "No-Email Corp",
            "jd_text":     "Apply via our portal at https://noemail.com/apply",
            "apply_url":   "https://noemail.com/apply",
            "cover_letter": "Great company, great role.",
            "resume_path": "",
        },
    ]

    profile = _load_profile()

    # Dry-run: just check email extraction
    print("\n── Email extraction test ──")
    for job in test_jobs:
        email = _extract_recruiter_email(job)
        print(f"  {job['company']:20s} → {email or 'No email found'}")

    # Uncomment to actually send:
    # result = run_email_bot(test_jobs, profile)
    # for job in result:
    #     print(f"{'✅' if job['email_sent'] else '❌'}  {job['title']} @ {job['company']}")
