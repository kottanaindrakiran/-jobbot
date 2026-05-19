import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright

COOKIES_DIR = Path(__file__).parent / "logs"
COOKIES_DIR.mkdir(exist_ok=True)

async def run():
    print("==============================================")
    print(" JobBot Cookie Generator (Non-Headless Mode) ")
    print("==============================================")
    print("This script will open a browser window so you can manually log in")
    print("to each platform and solve any CAPTCHAs. The session cookies will")
    print("be saved, and you can copy them to GitHub Secrets.")
    
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized"
            ]
        )
        context = await browser.new_context(
            no_viewport=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        platforms = [
            ("Internshala", "https://internshala.com/login/student", "cookies_internshala.json", "https://internshala.com/student/dashboard"),
            ("LinkedIn", "https://www.linkedin.com/login", "cookies_linkedin.json", "https://www.linkedin.com/feed/"),
            ("Naukri", "https://www.naukri.com/nlogin/login", "cookies_naukri.json", "https://www.naukri.com/mnjuser/homepage"),
            ("Wellfound", "https://wellfound.com/login", "cookies_wellfound.json", "https://wellfound.com/jobs")
        ]

        for name, login_url, cookie_file, success_url in platforms:
            print(f"\n[>] Navigating to {name}...")
            await page.goto(login_url)
            print(f"Please log in to {name} in the browser window.")
            print("Waiting for you to log in...")
            
            # Wait until the URL changes to the success URL or similar indicating login success
            while True:
                await asyncio.sleep(2)
                if page.url.startswith(success_url) or "login" not in page.url:
                    break

            print(f"[{name}] Login detected! Saving cookies...")
            cookies = await context.cookies()
            cookie_path = COOKIES_DIR / cookie_file
            with open(cookie_path, "w") as f:
                json.dump(cookies, f)
            print(f"Saved to {cookie_path} \u2713")
            
        await browser.close()
        
    print("\n==============================================")
    print("Done! Now copy the contents of the generated JSON files")
    print("into your GitHub Secrets (e.g., INTERNSHALA_COOKIES)")
    print("==============================================")

if __name__ == "__main__":
    asyncio.run(run())
