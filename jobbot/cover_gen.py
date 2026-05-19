"""
cover_gen.py
------------
Generates a personalised cover letter using a cascade of AI providers.

AI Cascade (auto-fallback when credits run out):
  1. Google Gemini (free 1500 req/day)
  2. Groq / LLaMA-3 (free 14400 req/day)
  3. OpenRouter (free models)
  4. Hand-crafted fallback template

Public API:
    generate_cover_letter(job)          → cover letter text (str)
    run_cover_gen(jobs, profile=None)   → jobs list with 'cover_letter' attached
"""

import logging
import os
import re
import time
from pathlib import Path

import requests
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

MAX_TOKENS = 500

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
#  AI CASCADE  (Gemini → Groq → OpenRouter → fallback)
# ─────────────────────────────────────────────────────────────────────────────
def _ai_gemini(prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": f"{SYSTEM_PROMPT}\n\n{prompt}"}]}],
        "generationConfig": {"maxOutputTokens": MAX_TOKENS, "temperature": 0.8},
    }
    r = requests.post(url, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def _ai_groq(prompt: str) -> str:
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.8,
    }
    r = requests.post(url, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _ai_openrouter(prompt: str) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        "max_tokens": MAX_TOKENS,
    }
    r = requests.post(url, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _call_ai_cascade(prompt: str, label: str) -> str | None:
    """Try each AI provider in order. Returns text or None."""
    for name, fn in [("Gemini", _ai_gemini), ("Groq", _ai_groq), ("OpenRouter", _ai_openrouter)]:
        try:
            log.info(f"[CoverGen] Trying {name} for: {label}")
            text = fn(prompt)
            log.info(f"[CoverGen] ✅ {name} returned {_word_count(text)} words")
            return text
        except Exception as e:
            log.warning(f"[CoverGen] {name} failed: {e} — trying next")
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  CORE FUNCTION
# ─────────────────────────────────────────────────────────────────────────────
def generate_cover_letter(job: dict) -> str:
    """
    Generate a tailored cover letter using AI cascade:
    Gemini → Groq → OpenRouter → fallback template.
    """
    title   = job.get("title",   "N/A")
    company = job.get("company", "N/A")
    label   = f"{title} @ {company}"
    log.info(f"[CoverGen] Generating cover letter: {label}")

    raw = _call_ai_cascade(_build_user_prompt(job), label)
    if raw:
        text = _clean_cover_letter(raw)
        log.info(f"[CoverGen] Cover letter generated ({_word_count(text)} words)")
        return text

    log.warning("[CoverGen] All AI providers failed — using fallback template")
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
