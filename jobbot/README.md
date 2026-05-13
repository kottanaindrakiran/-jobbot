# 🤖 JobBot — Automated Job Application Bot

> **Fully automated job hunting pipeline**: scrape → score → generate tailored resume + cover letter → auto-apply → cold email → log to Google Sheets → nightly digest.

---

## 📁 Folder Structure

```
jobbot/
├── profile.json        ← Your personal details, skills, target roles
├── scraper.py          ← Scrapes LinkedIn RSS, Naukri, Wellfound, Internshala
├── filter.py           ← Deduplicates & scores jobs by match quality
├── resume_gen.py       ← Claude API → ATS-optimised resume per JD
├── cover_gen.py        ← Claude API → Personalised cover letter per JD
├── apply_bot.py        ← Playwright auto-apply (LinkedIn Easy Apply + Naukri)
├── email_bot.py        ← Gmail API cold email with resume attached
├── sheets_logger.py    ← Logs every application to Google Sheets
├── digest.py           ← Sends nightly Gmail summary of all applications
├── main.py             ← Orchestrates everything — run this nightly
├── .env                ← Your real API keys (DO NOT commit)
├── .env.example        ← Template with placeholder values
├── requirements.txt    ← All Python dependencies
├── resumes/            ← Generated PDF/DOCX resumes saved here
└── logs/               ← Run logs saved here
```

---

## ⚙️ Setup Instructions

### 1. Clone / open the project

```powershell
cd "d:\MY APPS\JobBot\jobbot"
```

### 2. Create a virtual environment

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```powershell
pip install -r requirements.txt
```

### 4. Install Playwright browsers

```powershell
playwright install chromium
```

### 5. Configure environment variables

Your `.env` is already pre-filled. To verify:

```powershell
cat .env
```

Key variables:

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude AI for resume & cover letter generation |
| `LINKEDIN_EMAIL` / `LINKEDIN_PASSWORD` | LinkedIn auto-apply |
| `NAUKRI_EMAIL` / `NAUKRI_PASSWORD` | Naukri auto-apply |
| `GMAIL_CREDENTIALS_JSON` | Gmail service account (send emails) |
| `GOOGLE_SHEETS_CREDENTIALS` | Sheets service account (log applications) |
| `GOOGLE_SHEET_ID` | Your Google Sheet to log into |
| `MY_EMAIL` | Where nightly digest is sent |

### 6. Google Sheets — Grant access

Make sure your Google Sheet (`GOOGLE_SHEET_ID`) has been shared with the service account email:

```
jobbot-service@jobbot-496214.iam.gserviceaccount.com
```

> Go to your Google Sheet → Share → paste the service account email → Editor role.

### 7. Update your profile

Edit `profile.json` with your details (already pre-filled with Kiran's profile).

---

## 🚀 Running JobBot

### Full pipeline (nightly run)

```powershell
python main.py
```

### Dry run (no applying, no emailing — just scrape + score)

Edit `main.py` and set:
```python
DRY_RUN = True
```

Then run:
```powershell
python main.py
```

### Run individual modules

```powershell
# Test scraper only
python scraper.py

# Test filter only
python filter.py

# Test resume generation only
python resume_gen.py

# Test cover letter generation only
python cover_gen.py

# Test Google Sheets logging only
python sheets_logger.py

# Send a test digest email
python digest.py
```

---

## 🕐 Schedule Nightly Runs (Windows Task Scheduler)

1. Open **Task Scheduler** → Create Basic Task
2. Name: `JobBot Nightly`
3. Trigger: **Daily** at `11:00 PM`
4. Action: **Start a program**
   - Program: `d:\MY APPS\JobBot\jobbot\venv\Scripts\python.exe`
   - Arguments: `main.py`
   - Start in: `d:\MY APPS\JobBot\jobbot`
5. Finish → Enable

---

## 🔌 Pipeline Overview

```
main.py
  │
  ├─ 1. Load profile.json
  ├─ 2. scraper.py     → raw jobs (LinkedIn + Naukri + Wellfound + Internshala)
  ├─ 3. filter.py      → deduplicated + scored + ranked top 30
  ├─ 4. resume_gen.py  → Claude generates ATS resume per job → /resumes/
  ├─ 5. cover_gen.py   → Claude generates cover letter per job
  ├─ 6. apply_bot.py   → Playwright auto-applies (LinkedIn Easy Apply + Naukri)
  ├─ 7. email_bot.py   → Gmail cold email with resume attached
  ├─ 8. sheets_logger.py → logs all applications to Google Sheets
  └─ 9. digest.py      → sends nightly summary email to MY_EMAIL
```

---

## 🛡️ Security Notes

- **Never commit `.env`** — it contains real credentials.
- `.env.example` has placeholder values — safe to commit.
- Your service account private key is stored only in `.env`.
- Add `.env` to `.gitignore` immediately if you use git:

```powershell
echo ".env" >> .gitignore
echo "resumes/" >> .gitignore
echo "logs/" >> .gitignore
echo "venv/" >> .gitignore
```

---

## 📊 Google Sheet Columns

| Date | Title | Company | Source | Location | URL | Match Score | Applied (Bot) | Email Sent | Resume Path | Cover Path | Notes |

---

## 🐛 Troubleshooting

| Problem | Fix |
|---------|-----|
| `playwright install` fails | Run as Administrator |
| LinkedIn blocks login | Add 2FA backup code to `.env`, or use cookies |
| Claude API quota | Check [console.anthropic.com](https://console.anthropic.com) |
| Sheets permission denied | Share sheet with service account email |
| Gmail send fails | Verify Gmail API enabled on GCP project `jobbot-496214` |

---

## 👤 Author

**Kottana Indra Kiran**  
B.Tech CSE — SRM Institute of Science and Technology, Chennai (May 2026)  
📧 kottanaindrakiran@gmail.com  
🔗 [LinkedIn](https://www.linkedin.com/in/indra-kiran-kottana/) | [GitHub](https://github.com/kottanaindrakiran)
