# 🤖 JobBot — Automated Job Application Bot

> **Sleep at 11 PM. Wake up to 100+ job applications sent. Zero manual effort.**

Built by [Kottana Indra Kiran](https://github.com/kottanaindrakiran) — Full Stack + AI Engineer

[![Python](https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python)](https://python.org)
[![Playwright](https://img.shields.io/badge/Playwright-Automation-green?style=flat-square)](https://playwright.dev)
[![Claude AI](https://img.shields.io/badge/Claude-AI%20Powered-orange?style=flat-square)](https://anthropic.com)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Scheduled-purple?style=flat-square)](https://github.com/features/actions)
[![License](https://img.shields.io/badge/License-MIT-lightgrey?style=flat-square)](LICENSE)

---

## 🚀 What It Does

JobBot is a **fully automated job application pipeline** that runs every night while you sleep:

1. **Scrapes** 200–500 fresh jobs from LinkedIn, Naukri, Wellfound, and Internshala
2. **Filters** jobs using AI keyword matching against your skill profile
3. **Generates** a custom ATS-optimized resume per job description using Claude AI
4. **Writes** a personalized cover letter for every application
5. **Auto-applies** via Playwright (LinkedIn Easy Apply + Naukri)
6. **Cold emails** recruiters directly using Gmail API
7. **Logs** every application to Google Sheets
8. **Sends you a Gmail digest** — wake up to a full report of what was applied overnight

---

## 🏗️ System Architecture

```
Your Profile (profile.json)
         │
         ▼
┌─────────────────────┐
│   Job Scraper       │  ← LinkedIn RSS, Naukri, Wellfound, Internshala
└────────┬────────────┘
         │ 200–500 raw jobs
         ▼
┌─────────────────────┐
│   Smart Filter      │  ← Dedup + keyword match score + freshness
└────────┬────────────┘
         │ Top 100 quality matches
         ▼
┌─────────────────────┐
│   Claude AI         │  ← ATS Resume generator + Cover letter
└────────┬────────────┘
         │
    ┌────┴──────┐
    ▼           ▼
Playwright    Gmail API
Auto-Apply    Cold Email
    │           │
    └─────┬─────┘
          ▼
   Google Sheets Logger
          │
          ▼
   Gmail Digest to You
```

---

## 🎯 Target Roles

- Full Stack Developer
- Software Development Engineer (SDE)
- Java Developer
- Frontend Developer / React Developer
- AI Engineer / ML Engineer
- Backend Developer

**Location:** Any city in India + Remote

---

## 🛠️ Tech Stack

| Component | Tool | Cost |
|-----------|------|------|
| Job scraping | `requests` + `BeautifulSoup` + LinkedIn RSS | ✅ Free |
| ATS resume AI | Claude API (`claude-sonnet`) | ✅ Free tier |
| Cover letter AI | Claude API | ✅ Free tier |
| Auto-apply bot | `Playwright` (headless Chromium) | ✅ Free |
| Email outreach | Gmail API (OAuth2) | ✅ Free (500/day) |
| Application tracker | Google Sheets API + `gspread` | ✅ Free |
| Scheduler | GitHub Actions (cron) | ✅ Free (2000 min/mo) |
| PDF generation | `reportlab` + `python-docx` | ✅ Free |

**Total monthly cost: ₹0**

---

## 📁 Project Structure

```
jobbot/
├── .github/
│   └── workflows/
│       └── nightly_jobbot.yml    # GitHub Actions cron scheduler
├── jobbot/
│   ├── profile.json              # Your details — edit this once
│   ├── scraper.py                # Scrapes jobs from all sources
│   ├── filter.py                 # Dedup + match scoring
│   ├── resume_gen.py             # Claude AI ATS resume generator
│   ├── cover_gen.py              # Claude AI cover letter generator
│   ├── apply_bot.py              # Playwright auto-apply bot
│   ├── email_bot.py              # Gmail API cold email sender
│   ├── sheets_logger.py          # Google Sheets tracker
│   ├── digest.py                 # Nightly Gmail summary
│   └── main.py                   # Orchestrates everything
├── resumes/                      # Generated PDF resumes (gitignored)
├── logs/                         # Run logs (gitignored)
├── .env.example                  # Environment variable template
├── requirements.txt              # All dependencies
└── README.md
```

---

## ⚡ Quick Setup

### 1. Clone the repo

```bash
git clone https://github.com/kottanaindrakiran/-jobbot.git
cd -jobbot
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure your profile

Edit `jobbot/profile.json` with your details:

```json
{
  "name": "Your Name",
  "email": "your@gmail.com",
  "phone": "9876543210",
  "github": "https://github.com/yourusername",
  "linkedin": "https://linkedin.com/in/yourprofile",
  "target_roles": ["Full Stack Developer", "React Developer", "SDE"],
  "skills": { ... },
  "projects": [ ... ]
}
```

### 3. Set up API credentials

Copy `.env.example` to `.env` and fill in:

```bash
cp .env.example .env
```

```env
ANTHROPIC_API_KEY=your_key_here
LINKEDIN_EMAIL=your@email.com
LINKEDIN_PASSWORD=yourpassword
NAUKRI_EMAIL=your@email.com
NAUKRI_PASSWORD=yourpassword
GMAIL_CREDENTIALS_JSON=path/to/credentials.json
GOOGLE_SHEETS_CREDENTIALS=path/to/service_account.json
GOOGLE_SHEET_ID=your_sheet_id
MY_EMAIL=your@gmail.com
```

### 4. Enable APIs (one-time setup)

| API | Where | Guide |
|-----|-------|-------|
| Anthropic API Key | [console.anthropic.com](https://console.anthropic.com) | Free tier available |
| Gmail API OAuth2 | [Google Cloud Console](https://console.cloud.google.com) → APIs → Gmail API | Enable + download credentials.json |
| Google Sheets API | Same Google Cloud project → Sheets API | Enable + create service account |

### 5. Run manually (test)

```bash
cd jobbot
python main.py
```

### 6. Deploy on GitHub Actions (auto-runs nightly)

Add these secrets to your GitHub repo → Settings → Secrets:

```
ANTHROPIC_API_KEY
LINKEDIN_EMAIL
LINKEDIN_PASSWORD
NAUKRI_EMAIL
NAUKRI_PASSWORD
GMAIL_CREDENTIALS_JSON
GOOGLE_SHEETS_CREDENTIALS
GOOGLE_SHEET_ID
```

Push to main → GitHub Actions runs every night at **11:30 PM IST** automatically.

---

## 📊 What You Get Every Morning

**Gmail subject:** `JobBot Report — 2026-05-20 — 94 applications sent`

```
JobBot Nightly Report — 20 May 2026

Summary:
- Auto-applied (LinkedIn/Naukri): 76
- Cold emails sent: 18
- Failed/skipped: 6
- Total: 94

Top companies applied tonight:
1. Razorpay — Full Stack Developer (match: 87%)
2. Zepto — React Developer (match: 84%)
3. PhonePe — Java Developer (match: 81%)
...

View full tracker: [Google Sheet link]
```

---

## 🔍 Application Tracker (Google Sheets)

Every job is logged automatically:

| Date | Company | Role | Source | Method | Match % | Status |
|------|---------|------|--------|--------|---------|--------|
| 20-May | Razorpay | Full Stack Dev | LinkedIn | EasyApply | 87% | Applied |
| 20-May | Zepto | React Dev | Naukri | EasyApply | 84% | Applied |
| 20-May | SaaS Startup | AI Engineer | Wellfound | Email | 79% | Applied |

---

## ⚙️ Configuration Options

In `profile.json` you can tune:

- `target_roles` — which roles to search
- `salary_expectation` — used in screening questions
- `location_preference` — "Remote" / "Any" / specific city
- `skills` — used for ATS keyword matching score

In `filter.py`:
- `MIN_MATCH_SCORE` — default 30%, raise to get fewer but better matches
- `MAX_JOBS_PER_RUN` — default 100

---

## 🛡️ Important Notes

- **LinkedIn rate limiting** — bot sleeps 5–10s between applications, mimics human behavior
- **Credentials security** — never commit `.env` or `credentials.json` (already in `.gitignore`)
- **Gmail limit** — Gmail API allows 500 emails/day free, bot respects this
- **ATS resumes** — Claude generates truthful resumes, only reorganizes and keyword-matches your real profile
- **LinkedIn ToS** — use at your own discretion; bot uses slow, human-like interactions

---

## 🧠 How the ATS Resume AI Works

For each job, Claude gets:
- Your full profile (skills, projects, experience)
- The exact job description

Claude returns a resume where:
- Your skills are reordered to match JD keywords
- Most relevant projects are highlighted first
- Keywords from the JD appear naturally
- Everything stays 100% truthful — no hallucinations

---

## 👨‍💻 About the Author

**Kottana Indra Kiran** — B.Tech CSE, SRM IST Chennai (2026)

- 🐙 GitHub: [kottanaindrakiran](https://github.com/kottanaindrakiran)
- 💼 LinkedIn: [indra-kiran-kottana](https://www.linkedin.com/in/indra-kiran-kottana/)
- 🏆 LeetCode: [kottanaindrakiran](https://leetcode.com/u/kottanaindrakiran/)
- 📧 Email: kottanaindrakiran@gmail.com

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

> **Built because applying to jobs manually is a waste of time. Let the bot grind while you build.**
