"""
cover_gen.py
------------
Generates a personalised, human-sounding cover letter for each job
using Claude AI (claude-sonnet-4-20250514).

Public API:
    generate_cover_letter(job)          → cover letter text (str)
    run_cover_gen(jobs, profile=None)   → jobs list with 'cover_letter' attached
"""

import logging
import os
import re
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
#  BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cover_gen")

CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS   = 500

# ─────────────────────────────────────────────────────────────────────────────
#  PROMPTS
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = "You are an expert career coach writing personalized cover letters."


def _build_user_prompt(job: dict) -> str:
    jd_snippet = (job.get("jd_text") or "")[:500].strip()
    company    = job.get("company", "the company")
    title      = job.get("title",   "the role")

    return f"""Write a 150-word cover letter for this candidate applying to this role.

Candidate: Kottana Indra Kiran
Email: kottanaindrakiran@gmail.com
Phone: 7382538122
GitHub: https://github.com/kottanaindrakiran
Education: B.Tech CSE, SRM IST, CGPA 7.7, graduating May 2026
Key Projects:
  - AcadNet (Java / Spring Boot / React full-stack academic platform with real-time collaboration)
  - HealthBridge AI (FastAPI / RAG / Supabase / pgvector — healthcare triage with voice & image analysis)
  - SkillSwap (React / NLP — real-time skill-sharing platform with recommendation engine)
Internship: Java Full Stack Developer at Eduskills Foundation (Jan – Mar 2025)
Skills: Java, Spring Boot, React, Python, FastAPI, Supabase, Docker, LLMs, RAG, PostgreSQL, TypeScript, WebSockets

Applying for: {title} at {company}
Job Description keywords: {jd_snippet}

Rules:
- Address it to the Hiring Team at {company} specifically (use the company name)
- Match 2-3 keywords from the JD naturally — do NOT just list them
- Mention ONE specific project most relevant to this exact role and why it's relevant
- End with a confident, polite call to action (e.g. "I'd love to discuss...")
- Sound human and enthusiastic — NOT robotic or AI-generated
- Avoid clichés like "I am writing to express my interest" or "passion for technology"
- Strict maximum: 150 words
- Return ONLY the cover letter text. No subject line. No date. No explanation."""


# ─────────────────────────────────────────────────────────────────────────────
#  FALLBACK TEMPLATE
# ─────────────────────────────────────────────────────────────────────────────
def _fallback_cover_letter(job: dict) -> str:
    """
    Returns a solid hand-crafted cover letter when Claude is unavailable.
    Picks the most relevant project heuristically from the job title.
    """
    title   = job.get("title",   "Software Developer")
    company = job.get("company", "your company")
    jd_lower = (job.get("jd_text") or "").lower()

    # Pick most relevant project based on JD keywords
    if any(kw in jd_lower for kw in ["rag", "llm", "ai", "ml", "machine learning", "nlp"]):
        project_line = (
            "HealthBridge AI — a FastAPI/RAG platform I built that handles real-time "
            "medical triage using voice, image, and text inputs with pgvector similarity search"
        )
    elif any(kw in jd_lower for kw in ["java", "spring", "backend", "microservice"]):
        project_line = (
            "AcadNet — a production-grade Spring Boot / React platform with JWT auth, "
            "Redis caching, WebSocket collaboration, and Dockerised microservices"
        )
    else:
        project_line = (
            "SkillSwap — a React / Supabase platform with an NLP recommendation engine "
            "and real-time chat built for thousands of concurrent users"
        )

    return f"""Dear Hiring Team at {company},

I'm a final-year B.Tech CSE student at SRM IST (CGPA 7.7, graduating May 2026) with hands-on experience across full-stack development, AI/ML integration, and cloud deployment.

One project that speaks directly to the {title} role is {project_line}. This gave me practical experience with the exact challenges your team likely faces daily.

During my internship at Eduskills Foundation as a Java Full Stack Developer, I built and shipped REST APIs used in production — strengthening my ability to deliver reliable, well-tested code on schedule.

I'd love to discuss how my background aligns with what {company} is building. Thank you for your time and consideration.

Warm regards,
Kottana Indra Kiran
kottanaindrakiran@gmail.com | 7382538122
github.com/kottanaindrakiran"""


# ─────────────────────────────────────────────────────────────────────────────
#  POST-PROCESSING
# ─────────────────────────────────────────────────────────────────────────────
def _clean_cover_letter(text: str) -> str:
    """
    Strip any accidental markdown fences, leading/trailing whitespace,
    or "Here is the cover letter:" preambles Claude sometimes adds.
    """
    # Remove markdown code fences
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"```", "", text)

    # Remove common AI preamble lines
    preamble_patterns = [
        r"^here(?:'s| is)(?: the| a)? cover letter.*?:\s*\n",
        r"^cover letter:\s*\n",
        r"^---+\s*\n",
        r"^subject:.*?\n",
        r"^\[.*?\]\s*\n",           # [Date] or [Your Name] placeholders
    ]
    for pat in preamble_patterns:
        text = re.sub(pat, "", text, flags=re.IGNORECASE | re.MULTILINE)

    return text.strip()


def _word_count(text: str) -> int:
    return len(text.split())


# ─────────────────────────────────────────────────────────────────────────────
#  CORE FUNCTION
# ─────────────────────────────────────────────────────────────────────────────
def generate_cover_letter(job: dict) -> str:
    """
    Generate a tailored cover letter for the given job.

    Args:
        job: A job dict with at least 'title', 'company', 'jd_text'

    Returns:
        Cover letter as a plain-text string (≤150 words).
        Falls back to a hand-crafted template if Claude is unavailable.
    """
    title   = job.get("title",   "N/A")
    company = job.get("company", "N/A")
    log.info(f"[CoverGen] Generating cover letter: {title} @ {company}")

    # ── Try Claude ─────────────────────────────────────────────────────────────
    try:
        client  = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": _build_user_prompt(job)}
            ],
        )
        raw  = message.content[0].text
        text = _clean_cover_letter(raw)

        words = _word_count(text)
        log.info(f"[CoverGen] ✅ Claude returned {words} words for '{title} @ {company}'")
        return text

    except anthropic.APIConnectionError as e:
        log.warning(f"[CoverGen] Connection error: {e} — using fallback template")
    except anthropic.RateLimitError:
        log.warning("[CoverGen] Rate limit hit — using fallback template")
    except anthropic.APIStatusError as e:
        log.warning(f"[CoverGen] API error {e.status_code}: {e.message} — using fallback")
    except Exception as e:
        log.warning(f"[CoverGen] Unexpected error: {e} — using fallback template")

    return _fallback_cover_letter(job)


# ─────────────────────────────────────────────────────────────────────────────
#  BULK RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def run_cover_gen(
    jobs: list[dict],
    delay: float = 1.5,
) -> list[dict]:
    """
    Generate cover letters for all jobs in the list.
    Attaches 'cover_letter' (str) to each job dict.

    Args:
        jobs:  Filtered job list (output of filter.run_filter)
        delay: Seconds to wait between Claude calls (avoids rate limits)

    Returns:
        Same jobs list with 'cover_letter' key added.
    """
    log.info(f"[CoverGen] Starting cover letter generation for {len(jobs)} jobs …")

    for i, job in enumerate(jobs, 1):
        log.info(f"[CoverGen] [{i}/{len(jobs)}] {job.get('title')} @ {job.get('company')}")
        try:
            job["cover_letter"] = generate_cover_letter(job)
        except Exception as e:
            log.error(f"[CoverGen] Unrecoverable error for job {i}: {e}")
            job["cover_letter"] = _fallback_cover_letter(job)

        # Polite delay between API calls to avoid rate limits
        if i < len(jobs):
            time.sleep(delay)

    generated = sum(1 for j in jobs if j.get("cover_letter"))
    log.info(f"[CoverGen] ✅ {generated}/{len(jobs)} cover letters generated")
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_jobs = [
        {
            "title":    "Full Stack Developer",
            "company":  "Zepto",
            "location": "Bangalore",
            "jd_text": (
                "Looking for a Full Stack Developer with React, Java, Spring Boot, "
                "REST APIs, PostgreSQL, Docker. Experience with JWT, WebSockets, "
                "and microservices architecture is a strong plus. You'll be working "
                "on high-scale consumer-facing features for our quick-commerce platform."
            ),
            "source": "linkedin",
        },
        {
            "title":    "AI Engineer",
            "company":  "Sarvam AI",
            "location": "Remote",
            "jd_text": (
                "We're building India's foundational LLMs. Looking for an AI Engineer "
                "with Python, FastAPI, RAG pipelines, pgvector, LLM fine-tuning, "
                "and Supabase experience. You'll work on multimodal AI and NLP systems."
            ),
            "source": "wellfound",
        },
        {
            "title":    "Backend Developer",
            "company":  "Razorpay",
            "location": "Bangalore / Remote",
            "jd_text": (
                "Backend Developer with Java, Spring Boot, Microservices, Redis, "
                "PostgreSQL, Docker, and Kubernetes. You'll own critical payment "
                "infrastructure components serving millions of transactions daily."
            ),
            "source": "naukri",
        },
    ]

    print("\n" + "═" * 65)
    print("  🤖  CoverGen — Standalone Test")
    print("═" * 65)

    for job in test_jobs:
        letter = generate_cover_letter(job)
        print(f"\n{'─'*65}")
        print(f"  {job['title']}  @  {job['company']}  [{job['source']}]")
        print(f"  Word count: {_word_count(letter)}")
        print(f"{'─'*65}")
        print(letter)

    print("\n" + "═" * 65)
    print("  All cover letters generated ✅")
    print("═" * 65)
