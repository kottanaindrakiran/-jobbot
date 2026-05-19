"""
apply_bot.py
------------
Auto-applies to jobs on LinkedIn, Naukri, Internshala, and Wellfound using Playwright.

Public API:
    apply_linkedin(job, resume_pdf_path, profile)    -> bool
    apply_naukri(job, resume_pdf_path, profile)      -> bool
    apply_internshala(job, resume_pdf_path, profile) -> bool
    apply_wellfound(job, resume_pdf_path, profile)   -> bool
    run_apply_bot(jobs, profile)                     -> jobs (with 'applied' key)
"""

import asyncio
import json
import logging
import random
import time
from pathlib import Path

from dotenv import load_dotenv
import os
from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PWTimeout

# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  [%(levelname)s]  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("apply_bot")

PROFILE_PATH = Path(__file__).parent / "profile.json"
COOKIES_DIR  = Path(__file__).parent / "logs"
COOKIES_DIR.mkdir(exist_ok=True)

LI_EMAIL   = os.getenv("LINKEDIN_EMAIL")
LI_PASS    = os.getenv("LINKEDIN_PASSWORD")
NK_EMAIL   = os.getenv("NAUKRI_EMAIL")
NK_PASS    = os.getenv("NAUKRI_PASSWORD")
IS_EMAIL   = os.getenv("INTERNSHALA_EMAIL")
IS_PASS    = os.getenv("INTERNSHALA_PASSWORD")
WF_EMAIL   = os.getenv("WELLFOUND_EMAIL")
WF_PASS    = os.getenv("WELLFOUND_PASSWORD")

HEADLESS   = os.getenv("GITHUB_ACTIONS") == "true"
NAV_TIMEOUT = 30_000   # ms
ACT_TIMEOUT = 10_000   # ms


def _load_profile() -> dict:
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


async def _sleep():
    await asyncio.sleep(random.uniform(3, 5))


# ─────────────────────────────────────────────────────────────────────────────
#  COOKIE HELPERS  (skip login if session still valid)
# ─────────────────────────────────────────────────────────────────────────────
async def _save_cookies(context, name: str):
    path = COOKIES_DIR / f"cookies_{name}.json"
    cookies = await context.cookies()
    path.write_text(json.dumps(cookies), encoding="utf-8")
    log.info(f"[ApplyBot] Cookies saved → {path}")


async def _load_cookies(context, name: str) -> bool:
    path = COOKIES_DIR / f"cookies_{name}.json"
    alt_path = COOKIES_DIR / f"{name}_cookies.json"
    if not path.exists() and alt_path.exists():
        path = alt_path
    if not path.exists():
        log.info(f"[ApplyBot] Cookie file not found for {name}: {path}")
        return False
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            log.warning(f"[ApplyBot] Cookie file for {name} is empty!")
            return False
        cookies = json.loads(content)
        await context.add_cookies(cookies)
        log.info(f"[ApplyBot] Cookies loaded successfully for {name} ({len(cookies)} cookies) ← {path}")
        return True
    except json.JSONDecodeError as je:
        log.error(f"[ApplyBot] Cookie file for {name} has invalid JSON: {je}")
        return False
    except Exception as e:
        log.error(f"[ApplyBot] Failed to load cookies for {name}: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  LINKEDIN
# ─────────────────────────────────────────────────────────────────────────────
async def _li_login(page: Page, context) -> bool:
    """Login to LinkedIn. Returns True on success."""
    # Try saved cookies first
    if await _load_cookies(context, "linkedin"):
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        if "feed" in page.url:
            log.info("[LinkedIn] Session restored via cookies ✓")
            return True

    log.info("[LinkedIn] Logging in …")
    await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    await page.fill("#username", LI_EMAIL, timeout=ACT_TIMEOUT)
    await page.fill("#password", LI_PASS,  timeout=ACT_TIMEOUT)
    await page.click("button[type=submit]",  timeout=ACT_TIMEOUT)
    await page.wait_for_load_state("domcontentloaded")

    if "checkpoint" in page.url or "captcha" in page.url:
        log.warning("[LinkedIn] CAPTCHA / checkpoint detected — manual intervention needed")
        return False

    if "feed" in page.url or "mynetwork" in page.url:
        log.info("[LinkedIn] Login successful ✓")
        await _save_cookies(context, "linkedin")
        return True

    log.error(f"[LinkedIn] Unexpected URL after login: {page.url}")
    return False


async def _li_fill_form_step(page: Page, profile: dict):
    """Fill common Easy Apply form fields on the current step."""
    # Phone number
    phone_inputs = await page.query_selector_all("input[id*='phoneNumber'], input[name*='phone']")
    for inp in phone_inputs:
        val = await inp.input_value()
        if not val.strip():
            await inp.fill(profile.get("phone", ""))

    # Resume upload
    file_inputs = await page.query_selector_all("input[type=file]")
    for fi in file_inputs:
        pass  # handled per-call with explicit path

    # Years of experience → answer 0 for all (fresher)
    exp_inputs = await page.query_selector_all(
        "input[id*='experience'], input[id*='years'], select[id*='experience']"
    )
    for inp in exp_inputs:
        tag = await inp.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            # Pick lowest option
            await inp.evaluate("el => { el.selectedIndex = 0; el.dispatchEvent(new Event('change')); }")
        else:
            val = await inp.input_value()
            if not val.strip():
                await inp.fill("0")

    # Salary / CTC fields
    salary_inputs = await page.query_selector_all(
        "input[id*='salary'], input[id*='ctc'], input[id*='compensation']"
    )
    for inp in salary_inputs:
        val = await inp.input_value()
        if not val.strip():
            await inp.fill("500000")

    # Yes/No radio — default to "Yes" for relocate / willing questions
    radios = await page.query_selector_all("input[type=radio]")
    for radio in radios:
        label = await radio.evaluate("el => (el.labels[0]?.textContent || '').toLowerCase()")
        if "yes" in label:
            checked = await radio.is_checked()
            if not checked:
                await radio.check()

    # Dropdowns with "Select" placeholder → pick first real option
    selects = await page.query_selector_all("select")
    for sel in selects:
        val = await sel.input_value()
        if not val or val.lower() in ("", "select", "please select"):
            await sel.evaluate(
                "el => { if(el.options.length > 1) { el.selectedIndex = 1; "
                "el.dispatchEvent(new Event('change')); } }"
            )


async def _apply_linkedin_async(job: dict, resume_pdf_path: str, profile: dict | None = None) -> bool:
    """
    Apply to a LinkedIn job via Easy Apply.

    Returns:
        True  — application submitted successfully
        False — no Easy Apply button, already applied, or error
    """
    if profile is None:
        profile = _load_profile()

    url = job.get("apply_url", "")
    log.info(f"[LinkedIn] Applying → {job.get('title')} @ {job.get('company')}")

    try:
        async with async_playwright() as pw:
            browser: Browser = await pw.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            # ── Login ──────────────────────────────────────────────────────────
            if not await _li_login(page, context):
                await browser.close()
                return False

            # ── Navigate to job ────────────────────────────────────────────────
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(2)

            # ── Check Easy Apply button ────────────────────────────────────────
            easy_apply_btn = await page.query_selector(
                "button.jobs-apply-button, "
                "button[aria-label*='Easy Apply'], "
                "button[data-control-name*='jobdetails_topcard_inapply']"
            )
            if not easy_apply_btn:
                log.info(f"[LinkedIn] No Easy Apply button — external job, skipping")
                await browser.close()
                return False

            # Already applied?
            already = await page.query_selector("span.artdeco-inline-feedback__message")
            if already:
                text = (await already.inner_text()).lower()
                if "applied" in text:
                    log.info("[LinkedIn] Already applied to this job")
                    await browser.close()
                    return False

            await easy_apply_btn.click()
            await asyncio.sleep(1.5)

            # ── Multi-step Easy Apply modal ────────────────────────────────────
            MAX_STEPS = 8
            submitted = False

            for step in range(MAX_STEPS):
                log.info(f"[LinkedIn] Modal step {step + 1}")

                # Upload resume if file input visible
                file_input = await page.query_selector("input[type=file]")
                if file_input and resume_pdf_path and Path(resume_pdf_path).exists():
                    await file_input.set_input_files(resume_pdf_path)
                    await asyncio.sleep(1)
                    log.info(f"[LinkedIn] Resume uploaded ✓")

                # Fill common fields
                await _li_fill_form_step(page, profile)
                await asyncio.sleep(0.8)

                # Check for Submit button
                submit_btn = await page.query_selector(
                    "button[aria-label='Submit application'], "
                    "button[aria-label*='submit']"
                )
                if submit_btn:
                    await submit_btn.click()
                    await asyncio.sleep(2)
                    log.info(f"[LinkedIn] ✅ Submitted: {job['title']} @ {job['company']}")
                    submitted = True
                    break

                # Check for Next / Continue / Review button
                next_btn = await page.query_selector(
                    "button[aria-label='Continue to next step'], "
                    "button[aria-label='Review your application'], "
                    "button[aria-label*='Next'], "
                    "button[aria-label*='next']"
                )
                if next_btn:
                    await next_btn.click()
                    await asyncio.sleep(1.2)
                    continue

                # Check for "Application submitted" confirmation overlay
                confirm = await page.query_selector(
                    "div[data-test-modal] h2, "
                    "h2.t-24, "
                    "div.artdeco-modal__content h2"
                )
                if confirm:
                    txt = (await confirm.inner_text()).lower()
                    if "submitted" in txt or "sent" in txt:
                        log.info(f"[LinkedIn] ✅ Confirmed submitted: {job['title']}")
                        submitted = True
                        break

                # Dismiss modal if stuck
                dismiss_btn = await page.query_selector(
                    "button[aria-label='Dismiss'], button[data-test-modal-close-btn]"
                )
                if dismiss_btn:
                    await dismiss_btn.click()
                break

            await browser.close()
            return submitted

    except PWTimeout as e:
        log.warning(f"[LinkedIn] Timeout: {e}")
        return False
    except Exception as e:
        log.error(f"[LinkedIn] Error applying to {job.get('title')}: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  NAUKRI
# ─────────────────────────────────────────────────────────────────────────────
async def _naukri_login(page: Page, context) -> bool:
    """Login to Naukri. Returns True on success."""
    if await _load_cookies(context, "naukri"):
        await page.goto("https://www.naukri.com/mnjuser/homepage", timeout=NAV_TIMEOUT)
        if "nlogin" not in page.url and "login" not in page.url:
            log.info("[Naukri] Session restored via cookies ✓")
            return True

    log.info("[Naukri] Logging in …")
    await page.goto("https://www.naukri.com/nlogin/login", timeout=NAV_TIMEOUT)
    await asyncio.sleep(1.5)

    try:
        await page.fill("input[placeholder*='Email']", NK_EMAIL, timeout=ACT_TIMEOUT)
        await page.fill("input[placeholder*='Password']", NK_PASS, timeout=ACT_TIMEOUT)
        await page.click("button[type=submit]", timeout=ACT_TIMEOUT)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
    except Exception as e:
        log.warning(f"[Naukri] Login form error: {e}")
        return False

    if "mnjuser" in page.url or await page.query_selector("a[href*='nlogin/logout']"):
        log.info("[Naukri] Login successful ✓")
        await _save_cookies(context, "naukri")
        return True

    log.error(f"[Naukri] Login failed, URL: {page.url}")
    return False


async def _apply_naukri_async(job: dict, resume_pdf_path: str, profile: dict | None = None) -> bool:
    """
    Apply to a Naukri job listing.

    Returns:
        True  — application submitted
        False — already applied, no apply button, or error
    """
    if profile is None:
        profile = _load_profile()

    url = job.get("apply_url", "")
    log.info(f"[Naukri] Applying → {job.get('title')} @ {job.get('company')}")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            if not await _naukri_login(page, context):
                await browser.close()
                return False

            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(2)

            # ── Already applied? ───────────────────────────────────────────────
            applied_tag = await page.query_selector(
                "button.already-applied, span.already-applied, div[class*='alreadyApplied']"
            )
            if applied_tag:
                log.info("[Naukri] Already applied to this job")
                await browser.close()
                return False

            # ── Click Apply button ─────────────────────────────────────────────
            apply_btn = await page.query_selector(
                "button#apply-button, "
                "button.apply-button, "
                "a#apply-button, "
                "button[data-ga-track*='Apply']"
            )
            if not apply_btn:
                log.info("[Naukri] No Apply button found — skipping")
                await browser.close()
                return False

            await apply_btn.click()
            await asyncio.sleep(2)

            # ── Resume upload prompt ───────────────────────────────────────────
            file_input = await page.query_selector("input[type=file]")
            if file_input and resume_pdf_path and Path(resume_pdf_path).exists():
                await file_input.set_input_files(resume_pdf_path)
                await asyncio.sleep(1.5)
                log.info("[Naukri] Resume uploaded ✓")

            # ── Chatbot / questionnaire skip ───────────────────────────────────
            # Naukri sometimes shows a quick-apply chatbot — fill mandatory fields
            for _ in range(5):
                text_inputs = await page.query_selector_all(
                    "div.chatbot_InputText input, div.botMsg input"
                )
                if text_inputs:
                    inp = text_inputs[0]
                    placeholder = await inp.get_attribute("placeholder") or ""
                    if "name" in placeholder.lower():
                        await inp.fill(profile.get("name", ""))
                    elif "phone" in placeholder.lower() or "mobile" in placeholder.lower():
                        await inp.fill(profile.get("phone", ""))
                    elif "experience" in placeholder.lower():
                        await inp.fill("0")
                    elif "current ctc" in placeholder.lower() or "salary" in placeholder.lower():
                        await inp.fill("0")
                    elif "expected" in placeholder.lower():
                        await inp.fill("500000")
                    else:
                        await inp.fill("Yes")

                    send_btn = await page.query_selector(
                        "button.sendMsg, button[class*='send'], button.chatbot-submit"
                    )
                    if send_btn:
                        await send_btn.click()
                        await asyncio.sleep(1)
                    continue

                # Submit button
                submit_btn = await page.query_selector(
                    "button[type=submit], button.submit-btn, button[data-qa='submit']"
                )
                if submit_btn:
                    await submit_btn.click()
                    await asyncio.sleep(2)
                    break

                break  # no more inputs

            # ── Confirm success ────────────────────────────────────────────────
            success_el = await page.query_selector(
                "div.nI-gNb-drawer, "
                "div[class*='successMessage'], "
                "div[class*='applied-success']"
            )
            success_text = await page.evaluate(
                "document.body.innerText"
            )
            submitted = (
                success_el is not None
                or "successfully applied" in success_text.lower()
                or "application sent" in success_text.lower()
            )

            if submitted:
                log.info(f"[Naukri] ✅ Applied: {job['title']} @ {job['company']}")
            else:
                log.warning(f"[Naukri] ⚠️  Could not confirm submission for {job['title']}")

            await browser.close()
            return submitted

    except PWTimeout as e:
        log.warning(f"[Naukri] Timeout: {e}")
        return False
    except Exception as e:
        log.error(f"[Naukri] Error applying to {job.get('title')}: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  INTERNSHALA
# ─────────────────────────────────────────────────────────────────────────────
async def _internshala_login(page: Page, context) -> bool:
    """Login to Internshala. Returns True on success."""
    if await _load_cookies(context, "internshala"):
        await page.goto("https://internshala.com/student/dashboard", timeout=NAV_TIMEOUT)
        if "login" not in page.url.lower() and "internshala.com" in page.url:
            log.info("[Internshala] Session restored via cookies ✓")
            return True

    log.info("[Internshala] Logging in …")
    await page.goto("https://internshala.com/login/student", timeout=NAV_TIMEOUT)
    await asyncio.sleep(1.5)

    try:
        await page.fill("input#email", IS_EMAIL, timeout=ACT_TIMEOUT)
        await page.fill("input#password", IS_PASS, timeout=ACT_TIMEOUT)
        await page.click("button#login_submit", timeout=ACT_TIMEOUT)
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(2)
    except Exception as e:
        log.warning(f"[Internshala] Login form error: {e}")
        return False

    if "dashboard" in page.url or "internshala.com/student" in page.url:
        log.info("[Internshala] Login successful ✓")
        await _save_cookies(context, "internshala")
        return True

    log.error(f"[Internshala] Login failed — URL: {page.url}")
    return False


async def _apply_internshala_async(job: dict, resume_pdf_path: str, profile: dict | None = None) -> bool:
    """
    Apply to an Internshala job listing via Playwright.
    Returns True if application submitted successfully.
    """
    if not IS_EMAIL or not IS_PASS:
        log.warning("[Internshala] Credentials not set — skipping")
        return False
    if profile is None:
        profile = _load_profile()

    url = job.get("apply_url", "")
    log.info(f"[Internshala] Applying → {job.get('title')} @ {job.get('company')}")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            if not await _internshala_login(page, context):
                await browser.close()
                return False

            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(2)

            # Already applied?
            already = await page.query_selector(".already_applied, .already-applied")
            if already:
                log.info("[Internshala] Already applied to this job")
                await browser.close()
                return False

            # Click Apply Now button
            apply_btn = await page.query_selector(
                "#apply_now_btn, "
                "button.apply_now, "
                "a.apply_now_btn, "
                ".apply-button"
            )
            if not apply_btn:
                log.info("[Internshala] No Apply button found — skipping")
                await browser.close()
                return False

            await apply_btn.click()
            await asyncio.sleep(2)

            # Fill cover letter if textarea appears
            cover_ta = await page.query_selector("textarea#cover_letter, textarea[name='cover_letter']")
            if cover_ta:
                cover_text = job.get("cover_letter", 
                    f"I am a passionate Full Stack Developer (Java, React, Spring Boot) "
                    f"graduating from SRM IST in 2026. I am excited to apply for "
                    f"{job.get('title')} at {job.get('company')} and believe I can "
                    f"contribute meaningfully to your team from day one."
                )
                await cover_ta.fill(cover_text[:500])  # Internshala caps at 500 chars
                await asyncio.sleep(0.5)

            # Click the final submit / apply button inside modal
            submit_btn = await page.query_selector(
                "button#submit, "
                "button[type=submit], "
                "#apply_popup button.btn-primary, "
                ".modal button.apply_now"
            )
            if submit_btn:
                await submit_btn.click()
                await asyncio.sleep(2)

            # Confirm success
            page_text = await page.evaluate("document.body.innerText")
            submitted = any(kw in page_text.lower() for kw in [
                "successfully applied", "application sent", "applied successfully",
                "thank you for applying", "you have applied"
            ])

            # Also check if we're now showing "Applied" badge
            applied_badge = await page.query_selector(".already_applied, .applied-label")
            if applied_badge:
                submitted = True

            if submitted:
                log.info(f"[Internshala] ✅ Applied: {job['title']} @ {job['company']}")
            else:
                log.warning(f"[Internshala] ⚠️  Could not confirm submission for {job['title']}")

            await browser.close()
            return submitted

    except PWTimeout as e:
        log.warning(f"[Internshala] Timeout: {e}")
        return False
    except Exception as e:
        log.error(f"[Internshala] Error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  WELLFOUND  (AngelList Talent)
# ─────────────────────────────────────────────────────────────────────────────
async def _wellfound_login(page: Page, context) -> bool:
    """Login to Wellfound. Returns True on success."""
    if await _load_cookies(context, "wellfound"):
        await page.goto("https://wellfound.com/jobs", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        if "jobs" in page.url and "login" not in page.url:
            log.info("[Wellfound] Session restored via cookies ✓")
            return True

    log.info("[Wellfound] Logging in …")
    await page.goto("https://wellfound.com/login", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
    await asyncio.sleep(2)

    try:
        await page.fill("input[name='user[email]'], input[type='email']", WF_EMAIL, timeout=ACT_TIMEOUT)
        await page.fill("input[name='user[password]'], input[type='password']", WF_PASS, timeout=ACT_TIMEOUT)
        await page.click("input[type='submit'], button[type='submit']", timeout=ACT_TIMEOUT)
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)
    except Exception as e:
        log.warning(f"[Wellfound] Login form error: {e}")
        return False

    if "login" not in page.url:
        log.info("[Wellfound] Login successful ✓")
        await _save_cookies(context, "wellfound")
        return True

    log.error(f"[Wellfound] Login failed — URL: {page.url}")
    return False


async def _apply_wellfound_async(job: dict, resume_pdf_path: str, profile: dict | None = None) -> bool:
    """
    Apply to a Wellfound job listing.
    Returns True if application submitted successfully.
    """
    if not WF_EMAIL or not WF_PASS:
        log.warning("[Wellfound] Credentials not set — skipping")
        return False
    if profile is None:
        profile = _load_profile()

    url = job.get("apply_url", "")
    log.info(f"[Wellfound] Applying → {job.get('title')} @ {job.get('company')}")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            if not await _wellfound_login(page, context):
                await browser.close()
                return False

            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(2)

            # Click Apply button
            apply_btn = await page.query_selector(
                "button[data-test='apply-btn'], "
                "a[data-test='apply-btn'], "
                "button.styles_applyButton__"
            )
            if not apply_btn:
                log.info("[Wellfound] No Apply button found — skipping")
                await browser.close()
                return False

            await apply_btn.click()
            await asyncio.sleep(2)

            # Fill introduction/cover letter
            intro_ta = await page.query_selector("textarea[placeholder*='introduction'], textarea[name*='note']")
            if intro_ta:
                intro = (
                    f"Hi, I'm Indra Kiran, a Full Stack Developer (Java, React, Spring Boot, AI) "
                    f"graduating from SRM IST in 2026. I'm very excited about the "
                    f"{job.get('title')} role at {job.get('company')}. "
                    f"I've built production-grade apps and would love to contribute to your team."
                )
                await intro_ta.fill(intro)
                await asyncio.sleep(0.5)

            # Submit
            submit = await page.query_selector("button[type='submit'], button.apply-submit")
            if submit:
                await submit.click()
                await asyncio.sleep(2)

            page_text = await page.evaluate("document.body.innerText")
            submitted = any(kw in page_text.lower() for kw in [
                "application sent", "applied", "thank you", "successfully"
            ])

            if submitted:
                log.info(f"[Wellfound] ✅ Applied: {job['title']} @ {job['company']}")
            else:
                log.warning(f"[Wellfound] ⚠️  Could not confirm: {job['title']}")

            await browser.close()
            return submitted

    except PWTimeout as e:
        log.warning(f"[Wellfound] Timeout: {e}")
        return False
    except Exception as e:
        log.error(f"[Wellfound] Error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  SYNC WRAPPERS  — main.py calls these directly (no await needed)
# ─────────────────────────────────────────────────────────────────────────────
def _run_async(coro):
    """Run an async coroutine synchronously."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def apply_linkedin(job: dict, resume_pdf_path: str, profile: dict | None = None) -> bool:
    """Sync wrapper — apply to LinkedIn job."""
    return _run_async(_apply_linkedin_async(job, resume_pdf_path, profile))


def apply_naukri(job: dict, resume_pdf_path: str, profile: dict | None = None) -> bool:
    """Sync wrapper — apply to Naukri job."""
    return _run_async(_apply_naukri_async(job, resume_pdf_path, profile))


def apply_internshala(job: dict, resume_pdf_path: str, profile: dict | None = None) -> bool:
    """Sync wrapper — apply to Internshala job."""
    return _run_async(_apply_internshala_async(job, resume_pdf_path, profile))


def apply_wellfound(job: dict, resume_pdf_path: str, profile: dict | None = None) -> bool:
    """Sync wrapper — apply to Wellfound job."""
    return _run_async(_apply_wellfound_async(job, resume_pdf_path, profile))


# ─────────────────────────────────────────────────────────────────────────────
#  ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────
async def _run_apply_bot_async(jobs: list[dict], profile: dict) -> list[dict]:
    """Async core — iterate jobs and apply via the right bot."""
    for i, job in enumerate(jobs, 1):
        source = job.get("source", "")
        resume = job.get("resume_path", "")

        log.info(f"\n[ApplyBot] [{i}/{len(jobs)}] {job.get('title')} @ {job.get('company')}  [{source}]")

        if source == "linkedin":
            job["applied"] = await _apply_linkedin_async(job, resume, profile)
        elif source == "naukri":
            job["applied"] = await _apply_naukri_async(job, resume, profile)
        elif source == "internshala":
            job["applied"] = await _apply_internshala_async(job, resume, profile)
        elif source == "wellfound":
            job["applied"] = await _apply_wellfound_async(job, resume, profile)
        else:
            log.info(f"[ApplyBot] Source '{source}' not supported — skipping")
            job["applied"] = False

        # Polite random delay between applications
        if i < len(jobs):
            delay = random.uniform(3, 5)
            log.info(f"[ApplyBot] Waiting {delay:.1f}s before next application …")
            await asyncio.sleep(delay)

    return jobs


def run_apply_bot(jobs: list[dict], profile: dict | None = None) -> list[dict]:
    """
    Synchronous wrapper — apply to all jobs in the list.
    Attaches 'applied': True/False to each job dict.

    Args:
        jobs:    Filtered job list (must have 'resume_path' attached)
        profile: Preloaded profile dict; loaded from profile.json if None

    Returns:
        Same jobs list with 'applied' key added.
    """
    if profile is None:
        profile = _load_profile()

    log.info(f"[ApplyBot] Starting — {len(jobs)} jobs queued")
    result = asyncio.run(_run_apply_bot_async(jobs, profile))

    applied = sum(1 for j in result if j.get("applied"))
    log.info(f"\n[ApplyBot] ✅ Applied to {applied}/{len(jobs)} jobs")
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  STANDALONE TEST
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    profile = _load_profile()

    # Replace with a real job URL to test live
    test_jobs = [
        {
            "title":      "Software Developer",
            "company":    "Test Company",
            "source":     "linkedin",
            "apply_url":  "https://www.linkedin.com/jobs/view/4219082285",
            "resume_path": str(next(Path("resumes").glob("*.pdf"), "")),
        },
    ]

    result = run_apply_bot(test_jobs, profile)
    for job in result:
        status = "✅ Applied" if job.get("applied") else "❌ Not applied"
        print(f"{status} — {job['title']} @ {job['company']}")
