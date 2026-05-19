"""
resume_gen.py
-------------
Generates an ATS-optimised PDF resume for each job using a cascade of AI providers.

AI Cascade (auto-fallback when credits run out):
  1. Google Gemini (free 1500 req/day)
  2. Groq / LLaMA-3 (free 14400 req/day)
  3. OpenRouter (free models)
  4. Base template (no AI)

Pipeline:
  1. Build a job-tailored prompt from profile.json
  2. Call AI cascade → resume text
  3. Render resume text → professional PDF via ReportLab
  4. Save to /resumes/{company}_{role}_{timestamp}.pdf
  5. Return filepath
"""

import json
import logging
import os
import re
import textwrap
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ─────────────────────────────────────────────────────────────────────────────
#  BOOTSTRAP
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("resume_gen")

PROFILE_PATH  = Path(__file__).parent / "profile.json"
RESUMES_DIR   = Path(__file__).parent / "resumes"
RESUMES_DIR.mkdir(exist_ok=True)

MAX_TOKENS = 2000

# ─────────────────────────────────────────────────────────────────────────────
#  PROFILE
# ─────────────────────────────────────────────────────────────────────────────
def _load_profile() -> dict:
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)

# ─────────────────────────────────────────────────────────────────────────────
#  STEP 1 — PROMPT BUILDER
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are an expert ATS resume writer. "
    "Generate a resume that maximizes ATS keyword matching for the given job. "
    "Stay 100% truthful to the candidate profile. "
    "Use clean formatting with clear sections. "
    "Do NOT fabricate any experience, skills, or achievements not present in the profile."
)

def _build_user_prompt(profile: dict, job: dict) -> str:
    return f"""Candidate Profile:
{json.dumps(profile, indent=2)}

Job Title: {job.get('title', 'N/A')}
Company: {job.get('company', 'N/A')}
Job Description:
{job.get('jd_text', 'Not provided')}

Generate an ATS-optimized resume with these exact sections in order:
1. CONTACT INFO — name, email, phone, github, linkedin
2. TECHNICAL SKILLS — reorganize to mirror JD keywords first, then remaining skills
3. PROJECTS — highlight the 2-3 most relevant projects for THIS specific role. Include tech stack and 2 bullet points per project quantifying impact.
4. EDUCATION — degree, institute, CGPA, graduation year
5. EXPERIENCE / INTERNSHIP — role, company, duration, 3 bullet points using STAR format
6. CERTIFICATIONS — list all certifications

Rules:
- Mirror exact keywords from the job description naturally throughout
- Use strong action verbs (Built, Designed, Implemented, Optimised, Led)
- Keep total length under 600 words
- Return ONLY the resume text. No explanation. No markdown code fences. No commentary."""


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 2 — AI CASCADE  (Gemini → Groq → OpenRouter → base template)
# ─────────────────────────────────────────────────────────────────────────────
def _call_gemini(prompt: str, system: str) -> str:
    """Call Google Gemini API (free 1500 req/day)."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": f"{system}\n\n{prompt}"}]}],
        "generationConfig": {"maxOutputTokens": MAX_TOKENS, "temperature": 0.7},
    }
    r = requests.post(url, json=body, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _call_groq(prompt: str, system: str) -> str:
    """Call Groq LLaMA-3 API (free 14400 req/day)."""
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set")
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.7,
    }
    r = requests.post(url, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _call_openrouter(prompt: str, system: str) -> str:
    """Call OpenRouter free models (Llama / Mistral)."""
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        "max_tokens": MAX_TOKENS,
    }
    r = requests.post(url, headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _call_ai(prompt: str, system: str, label: str) -> str | None:
    """
    Try each AI provider in order. Return text on first success.
    Returns None if ALL providers fail (caller uses base template).
    """
    providers = [
        ("Gemini",      _call_gemini),
        ("Groq",        _call_groq),
        ("OpenRouter",  _call_openrouter),
    ]
    for name, fn in providers:
        try:
            log.info(f"[ResumeGen] Trying {name} for: {label}")
            text = fn(prompt, system)
            log.info(f"[ResumeGen] ✅ {name} returned {len(text)} chars")
            return text
        except Exception as e:
            log.warning(f"[ResumeGen] {name} failed: {e} — trying next provider")
    log.warning("[ResumeGen] All AI providers failed — using base template")
    return None


def _call_claude(profile: dict, job: dict) -> str:
    """Legacy wrapper kept for compatibility — now routes to AI cascade."""
    prompt = _build_user_prompt(profile, job)
    label  = f"{job.get('title')} @ {job.get('company')}"
    result = _call_ai(prompt, SYSTEM_PROMPT, label)
    if result is None:
        raise RuntimeError("All providers failed")
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  STEP 3 — PDF RENDERER
# ─────────────────────────────────────────────────────────────────────────────

# ── Colour palette ────────────────────────────────────────────────────────────
DARK      = colors.HexColor("#1a1a2e")   # near-black for headings
ACCENT    = colors.HexColor("#0077b6")   # professional blue for name / rules
BODY_GREY = colors.HexColor("#333333")   # body text
LIGHT_BG  = colors.HexColor("#f0f4f8")  # subtle header background


def _make_styles() -> dict:
    base = getSampleStyleSheet()

    styles = {
        "name": ParagraphStyle(
            "name",
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=24,
            textColor=ACCENT,
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "contact": ParagraphStyle(
            "contact",
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            textColor=BODY_GREY,
            alignment=TA_CENTER,
            spaceAfter=10,
        ),
        "section_heading": ParagraphStyle(
            "section_heading",
            fontName="Helvetica-Bold",
            fontSize=10,
            textColor=DARK,
            spaceBefore=10,
            spaceAfter=2,
            leading=14,
        ),
        "body": ParagraphStyle(
            "body",
            fontName="Helvetica",
            fontSize=9,
            textColor=BODY_GREY,
            leading=13,
            spaceAfter=2,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            fontName="Helvetica",
            fontSize=9,
            textColor=BODY_GREY,
            leading=13,
            leftIndent=12,
            spaceAfter=1,
            bulletIndent=4,
        ),
        "subheading": ParagraphStyle(
            "subheading",
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=DARK,
            leading=12,
            spaceAfter=1,
        ),
    }
    return styles


def _rule(width: float = 0) -> HRFlowable:
    return HRFlowable(
        width=width or "100%",
        thickness=0.7,
        color=ACCENT,
        spaceAfter=4,
        spaceBefore=2,
    )


def _sanitise(text: str) -> str:
    """Escape ReportLab special XML chars."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _parse_resume_sections(raw_text: str) -> dict[str, list[str]]:
    """
    Parse the Claude-generated plain-text resume into named sections.
    Returns {section_name: [line, line, ...]}
    """
    section_keywords = [
        "CONTACT", "TECHNICAL SKILLS", "SKILLS",
        "PROJECTS", "EDUCATION", "EXPERIENCE",
        "INTERNSHIP", "CERTIFICATIONS",
    ]
    pattern = re.compile(
        r"^\s*(" + "|".join(re.escape(k) for k in section_keywords) + r")\s*[:\-]?\s*$",
        re.IGNORECASE | re.MULTILINE,
    )

    sections: dict[str, list[str]] = {}
    lines = raw_text.splitlines()

    current_section = "HEADER"
    sections[current_section] = []

    for line in lines:
        m = pattern.match(line)
        if m:
            current_section = m.group(1).upper().strip()
            if current_section not in sections:
                sections[current_section] = []
        else:
            sections.setdefault(current_section, []).append(line)

    return sections


def _build_contact_block(profile: dict, styles: dict) -> list:
    """Build name + contact info header block."""
    flowables = []
    flowables.append(Paragraph(_sanitise(profile.get("name", "")), styles["name"]))

    parts = []
    if profile.get("phone"):
        parts.append(profile["phone"])
    if profile.get("email"):
        parts.append(profile["email"])
    if profile.get("location"):
        parts.append(profile["location"])
    if profile.get("linkedin"):
        parts.append(profile["linkedin"])
    if profile.get("github"):
        parts.append(profile["github"])
    if profile.get("leetcode"):
        parts.append(profile["leetcode"])

    contact_line = "  |  ".join(parts)
    flowables.append(Paragraph(_sanitise(contact_line), styles["contact"]))
    flowables.append(_rule())
    return flowables


def _add_section(
    flowables: list,
    heading: str,
    lines: list[str],
    styles: dict,
) -> None:
    """Render a single resume section with heading + body lines."""
    if not any(l.strip() for l in lines):
        return

    flowables.append(Paragraph(heading.title(), styles["section_heading"]))
    flowables.append(_rule())

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flowables.append(Spacer(1, 2))
            continue

        # Bullet lines
        if stripped.startswith(("•", "-", "*", "–")):
            text = stripped.lstrip("•-*– ").strip()
            flowables.append(
                Paragraph(f"• {_sanitise(text)}", styles["bullet"])
            )
        # Sub-headings (ALL CAPS or ends with ":")
        elif stripped.isupper() or (stripped.endswith(":") and len(stripped) < 60):
            flowables.append(Paragraph(_sanitise(stripped), styles["subheading"]))
        else:
            flowables.append(Paragraph(_sanitise(stripped), styles["body"]))


def _text_to_pdf(resume_text: str, profile: dict, output_path: Path) -> None:
    """
    Convert Claude's plain-text resume → styled PDF using ReportLab.
    """
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    styles    = _make_styles()
    flowables = []

    # ── Header (always use profile data — not Claude's text) ─────────────────
    flowables.extend(_build_contact_block(profile, styles))
    flowables.append(Spacer(1, 4))

    # ── Parse body sections from Claude output ────────────────────────────────
    sections = _parse_resume_sections(resume_text)

    # Section render order
    ORDERED_SECTIONS = [
        "TECHNICAL SKILLS", "SKILLS",
        "PROJECTS",
        "EDUCATION",
        "EXPERIENCE", "INTERNSHIP",
        "CERTIFICATIONS",
    ]

    rendered: set[str] = set()

    for key in ORDERED_SECTIONS:
        if key in sections and key not in rendered:
            _add_section(flowables, key, sections[key], styles)
            rendered.add(key)

    # Any extra sections Claude added that we didn't anticipate
    for key, lines in sections.items():
        if key not in rendered and key not in ("HEADER", "CONTACT"):
            _add_section(flowables, key, lines, styles)

    doc.build(flowables)
    log.info(f"[ResumeGen] PDF saved → {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
#  FALLBACK — BASE TEMPLATE RESUME
# ─────────────────────────────────────────────────────────────────────────────
def _build_base_template(profile: dict) -> str:
    """
    Construct a reasonable plain-text resume from profile.json
    when Claude is unavailable.
    """
    p = profile
    edu   = p.get("education", {})
    intern_ = p.get("internship", {})
    skills  = p.get("skills", {})
    projects = p.get("projects", [])
    certs    = p.get("certifications", [])

    skills_flat = []
    for vals in skills.values():
        if isinstance(vals, list):
            skills_flat.extend(vals)

    proj_lines = ""
    for proj in projects:
        proj_lines += f"\n{proj.get('name', '')}\n"
        proj_lines += f"  • {proj.get('description', '')}\n"
        proj_lines += f"  • Stack: {', '.join(proj.get('stack', []))}\n"

    cert_lines = "\n".join(f"  • {c}" for c in certs)

    return textwrap.dedent(f"""
TECHNICAL SKILLS
  {', '.join(skills_flat)}

PROJECTS
{proj_lines}
EDUCATION
  {edu.get('degree','')} | {edu.get('institute','')}
  CGPA: {edu.get('cgpa','')} | Graduating: {edu.get('graduation','')}

EXPERIENCE / INTERNSHIP
  {intern_.get('role','')} — {intern_.get('company','')}
  {intern_.get('duration','')}
  • Developed backend services using {intern_.get('stack','')}
  • Collaborated with cross-functional teams on production features
  • Followed Agile methodology with sprint planning and code reviews

CERTIFICATIONS
{cert_lines}
""").strip()


# ─────────────────────────────────────────────────────────────────────────────
#  FILENAME BUILDER
# ─────────────────────────────────────────────────────────────────────────────
def _make_filename(job: dict) -> Path:
    def slugify(s: str) -> str:
        s = re.sub(r"[^\w\s-]", "", s.lower())
        return re.sub(r"[\s-]+", "_", s).strip("_")[:30]

    company = slugify(job.get("company", "company"))
    role    = slugify(job.get("title",   "role"))
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    return RESUMES_DIR / f"{company}_{role}_{ts}.pdf"


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────
def generate_resume(job: dict, profile: dict | None = None) -> str:
    """
    Generate an ATS-optimised PDF resume tailored to `job`.

    Args:
        job:     A job dict from scraper/filter (needs 'title', 'company', 'jd_text')
        profile: Preloaded profile dict; loaded from profile.json if None

    Returns:
        Absolute filepath string of the generated PDF.
    """
    if profile is None:
        profile = _load_profile()

    output_path = _make_filename(job)

    # ── Try AI Cascade ───────────────────────────────────────────────────────
    try:
        resume_text = _call_claude(profile, job)
    except Exception as e:
        log.warning(f"[ResumeGen] AI error or all providers failed: {e} — using base template")
        resume_text = _build_base_template(profile)

    # ── Render to PDF ──────────────────────────────────────────────────────────
    try:
        _text_to_pdf(resume_text, profile, output_path)
    except Exception as e:
        log.error(f"[ResumeGen] PDF generation failed: {e}")
        raise

    return str(output_path)


def run_resume_gen(jobs: list[dict], profile: dict | None = None) -> list[dict]:
    """
    Generate a tailored resume for every job in the list.
    Attaches 'resume_path' to each job dict.

    Args:
        jobs:    Filtered job list from filter.run_filter()
        profile: Preloaded profile dict; loaded from profile.json if None

    Returns:
        Same job list with 'resume_path' attached.
    """
    if profile is None:
        profile = _load_profile()

    log.info(f"[ResumeGen] Generating resumes for {len(jobs)} jobs …")

    for i, job in enumerate(jobs, 1):
        log.info(f"[ResumeGen] [{i}/{len(jobs)}] {job.get('title')} @ {job.get('company')}")
        try:
            path = generate_resume(job, profile)
            job["resume_path"] = path
        except Exception as e:
            log.error(f"[ResumeGen] Failed: {e}")
            job["resume_path"] = None

    generated = sum(1 for j in jobs if j.get("resume_path"))
    log.info(f"[ResumeGen] ✅ {generated}/{len(jobs)} resumes generated → {RESUMES_DIR}")
    return jobs


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_job = {
        "title":    "Full Stack Developer",
        "company":  "TechCorp India",
        "location": "Bangalore / Remote",
        "jd_text": (
            "We are looking for a Full Stack Developer with strong React, Java, "
            "Spring Boot, PostgreSQL, Docker, and REST API skills. "
            "Experience with JWT Auth, WebSockets, and Microservices is a plus. "
            "You will build scalable web applications and collaborate with our "
            "cross-functional product team."
        ),
        "apply_url": "https://linkedin.com/jobs/test-12345",
        "source":    "linkedin",
    }

    print("Generating test resume …")
    path = generate_resume(test_job)
    print(f"\n✅ Resume saved to: {path}")
