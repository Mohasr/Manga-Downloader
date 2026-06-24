"""One-time Cloudflare cookie setup for Manga Downloader.

Launches a visible Chrome browser via Playwright. The user solves
Cloudflare Turnstile challenges manually. Cookies are captured and
saved for subsequent downloader sessions.

Usage:
    python setup_cookies.py
    python setup_cookies.py --check
"""

import asyncio, json, sys, time
from datetime import datetime
from pathlib import Path

CACHE = Path("manga_downloader") / "cache"
PROFILE = Path("manga_downloader") / "browser_profile"
COOKIES_JSON = CACHE / "cookies.json"
CACHE.mkdir(parents=True, exist_ok=True)

SITES = {
    "Manga Starz": [
        "https://manga-starz.net/manga/berserk/",
        "https://manga-starz.net/manga/berserk/5/",
    ],
    "Lek Manga": [
        "https://lek-manga.net/manga/apotheosis/",
        "https://lek-manga.net/manga/under-the-oak-tree/",
    ],
}


async def capture(sites=None, headless=False):
    if sites is None:
        sites = SITES
    from playwright.async_api import async_playwright

    print("=" * 60)
    print("COOKIE SETUP - Capture Cloudflare clearance cookies")
    print("=" * 60)
    if not headless:
        print("\nA VISIBLE Chrome window will open.")
        print("Solve each Cloudflare challenge manually when it appears.\n")

    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE.resolve()), channel="chrome", headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--start-minimized"],
        viewport={"width": 1920, "height": 1080})
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

    for site_name, urls in sites.items():
        print(f"\n{'='*40}\n  {site_name}\n{'='*40}")
        for url in urls:
            print(f"  -> {url}")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                for i in range(30):
                    await asyncio.sleep(2)
                    try:
                        title = await page.title()
                    except Exception:
                        title = ""
                    if "Just a moment" in title:
                        if i == 0 and not headless:
                            print("     CF challenge - solve it in the browser window...")
                    elif title:
                        print(f"     OK: {title[:80]}")
                        break
                else:
                    print("     CF did not clear - continuing")
            except Exception as e:
                print(f"     Error: {e}")
        await asyncio.sleep(1)

    all_cookies = await ctx.cookies()
    ua = await page.evaluate("() => navigator.userAgent")

    cookies_by_site = {}
    for site_name in sites:
        site_cookies = []
        for c in all_cookies:
            cd = c.get("domain", "").lower()
            parts = site_name.lower().split()
            if parts[0].replace("-", "") in cd.replace("-", "").replace(".", ""):
                site_cookies.append({
                    "name": c["name"], "value": c["value"],
                    "domain": c.get("domain", ""), "path": c.get("path", "/"),
                    "expires": c.get("expires", -1),
                    "httpOnly": c.get("httpOnly", False),
                    "secure": c.get("secure", False),
                    "sameSite": c.get("sameSite", "Lax"),
                })
        cookies_by_site[site_name] = site_cookies
        cf = next((c for c in site_cookies if c["name"] == "cf_clearance"), None)
        print(f"\n  {site_name}: {len(site_cookies)} cookies, cf_clearance={'YES' if cf else 'NO'}")
        if cf and cf.get("expires", -1) > 0:
            print(f"    expires: {datetime.fromtimestamp(cf['expires']).strftime('%Y-%m-%d %H:%M')}")

    await ctx.close()
    await pw.stop()

    export = {
        "user_agent": ua, "sites": cookies_by_site,
        "captured_at": time.time(),
        "captured_at_human": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(COOKIES_JSON, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2)
    print(f"\nSaved: {COOKIES_JSON}")
    return export


async def main():
    import argparse
    p = argparse.ArgumentParser(description="One-time Cloudflare cookie setup")
    p.add_argument("--check", action="store_true", help="Check existing cookies")
    args = p.parse_args()

    if args.check:
        if COOKIES_JSON.exists():
            with open(COOKIES_JSON, encoding="utf-8") as f:
                d = json.load(f)
            print("Existing cookies:")
            for n, cs in d.get("sites", {}).items():
                cf = next((c for c in cs if c["name"] == "cf_clearance"), None)
                print(f"  {n}: cf_clearance={'present' if cf else 'missing'}")
                if cf and cf.get("expires", -1) > 0:
                    print(f"    expires in: {(cf['expires'] - time.time()) / 3600:.0f}h")
        else:
            print("No cookies.json found. Run without --check to capture.")
        return
    await capture()

if __name__ == "__main__":
    asyncio.run(main())

