"""Cloudflare challenge handler — platform-agnostic browser orchestration.

Delegates browser lifecycle to a BrowserManager backend.
Supported backends: PlaywrightManager, KameleoManager.

All page.goto() calls are lock-protected to prevent concurrent
navigation. After CF indicators clear, a stability check prevents
false success detection during redirect transitions.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page

from ..browser.browser_manager import BrowserManager
from ..utils.logger import console, info, success, warning, error


class CloudflareDetectedError(Exception):
    """Raised when a Cloudflare challenge page is detected instead of target content."""


class DiscoveryValidationError(Exception):
    """Raised when discovery yields invalid/unexpected results."""


class CloudflareHandler:
    """Handles Cloudflare challenges using any BrowserManager backend.

    All browser lifecycle (launch, close, headers) is delegated to the
    BrowserManager. This class handles only CF detection, stability checks,
    and navigation.
    """

    _CF_TITLES: list[str] = [
        "just a moment", "please wait", "checking your browser",
        "attention required", "ddos protection", "cloudflare",
        "one moment", "please stand by", "verifying", "i am human",
    ]

    _CF_URL_PATTERNS: list[str] = ["/cdn-cgi/", "challenge-platform", "cf-challenge"]

    _CF_BODY_INDICATORS: list[str] = [
        "Checking your browser", "Please wait", "DDoS protection",
        "Just a moment", "Please stand by", "Verify you are human",
        "cf-browser-verification", "challenge-platform", "Verifying you are human",
    ]

    _CF_SELECTORS: str = (
        "#challenge-running, .cf-browser-verification, #cf-challenge, "
        "iframe[src*=\"cloudflare\"], div[class*=\"challenge\"], "
        "#challenge-form, #challenge-stage, #cf-content, "
        ".main-wrapper[class*=\"challenge\"]"
    )

    _STABILITY_WAIT = 5.0
    _STABILITY_CHECKS = 3

    def __init__(
        self,
        browser: BrowserManager,
        debug: bool = False,
    ) -> None:
        self._browser = browser
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._nav_lock = asyncio.Lock()
        self.debug = debug

    async def start(self, headless: bool = False, mode: str = "unknown") -> BrowserContext:
        """Start browser via the injected BrowserManager."""
        self._context, self._page = await self._browser.start(headless=headless, mode=mode)
        return self._context

    async def navigate_with_cf_bypass(
        self,
        url: str,
        timeout: int = 120_000,
        wait_until: str = "load",
        caller: str = "unknown",
    ) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started. Call start() first.")

        async with self._nav_lock:
            if self.debug:
                info(f"[debug][nav:{caller}] START -> {url}")
            else:
                info(f"Navigating to: {url}")

            try:
                await self._page.goto(url, timeout=timeout, wait_until=wait_until)
            except Exception as e:
                if "Navigation to" in str(e) and "interrupted" in str(e):
                    warning("Navigation interrupted, waiting for current page to settle...")
                    await asyncio.sleep(3)
                    try:
                        await self._page.wait_for_load_state("load", timeout=10_000)
                    except Exception:
                        pass

            if self.debug:
                title = await self._page.evaluate("() => document.title")
                info(f"[debug][nav:{caller}] GOTO done -- title='{title}' url='{self._page.url}'")

            await self._wait_for_cloudflare(timeout, caller=caller)

            if self.debug:
                title = await self._page.evaluate("() => document.title")
                info(f"[debug][nav:{caller}] CF passed -- title='{title}'")

            return self._page

    async def _wait_for_cloudflare(
        self, timeout: int = 120_000, caller: str = "unknown"
    ) -> None:
        if self._page is None:
            return

        poll_interval = 2.0
        elapsed = 0.0
        max_wait = timeout / 1000.0
        warned = False

        while elapsed < max_wait:
            try:
                title = await self._page.evaluate("() => document.title")
                body_text = await self._page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                )
                current_url = self._page.url

                if self.debug:
                    info(f"[debug][cf:{caller}] elapsed={elapsed:.0f}s title='{title[:60]}'")

                title_cf = any(t in title.lower() for t in self._CF_TITLES)
                body_cf = any(i.lower() in body_text.lower() for i in self._CF_BODY_INDICATORS)
                url_cf = any(p in current_url for p in self._CF_URL_PATTERNS)

                if not (title_cf or body_cf or url_cf):
                    selectors_cf = await self._page.evaluate(
                        f"() => {{ return !!document.querySelector(\"{self._CF_SELECTORS}\"); }}"
                    )
                    if not selectors_cf:
                        if self.debug:
                            info(f"[debug][cf:{caller}] Indicators clear, checking stability")
                        if await self._check_stability(caller):
                            if self.debug:
                                info(f"[debug][cf:{caller}] Stability confirmed")
                            return
                        else:
                            if self.debug:
                                info(f"[debug][cf:{caller}] Stability FAILED, CF returned")
                            warned = False
                            continue

                if not warned:
                    warning("Cloudflare challenge detected! Complete the verification in the browser window.")
                    title_info = title[:100]
                    url_info = current_url[:100]
                    cookie_info = len(await self._context.cookies()) if self._context else 0
                    cf_present = any(
                        c.get("name") == "cf_clearance"
                        for c in (await self._context.cookies() if self._context else [])
                    )
                    info(f"  CF diag: title='{title_info}'")
                    info(f"  CF diag: url='{url_info}'")
                    info(f"  CF diag: cookies={cookie_info} cf_clearance={'YES' if cf_present else 'NO'}")
                    if self.debug:
                        info(f"[debug][cf:{caller}] CF: title_cf={title_cf} body_cf={body_cf} url_cf={url_cf}")
                    warned = True

                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            except Exception as e:
                if self.debug:
                    warning(f"[debug][cf:{caller}] Polling error: {e}")
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

        title = "unknown"
        url = "unknown"
        try:
            if self._page:
                title = await self._page.evaluate("() => document.title")
                url = self._page.url
        except Exception:
            pass

        if self.debug:
            info(f"[debug][cf:{caller}] TIMEOUT: title='{title}' url='{url}'")

        error("Cloudflare verification timeout. The challenge page is still active.")
        raise CloudflareDetectedError(
            f"Cloudflare challenge not resolved after {max_wait:.0f}s. "
            f"Title: '{title}', URL: '{url}'"
        )

    async def _check_stability(self, caller: str = "") -> bool:
        check_interval = self._STABILITY_WAIT / self._STABILITY_CHECKS
        for i in range(self._STABILITY_CHECKS):
            await asyncio.sleep(check_interval)
            try:
                title = await self._page.evaluate("() => document.title")
                current_url = self._page.url
                body_text = await self._page.evaluate(
                    "() => document.body ? document.body.innerText : ''"
                )
            except Exception:
                return False
            if any(t in title.lower() for t in self._CF_TITLES) or \
               any(i.lower() in body_text.lower() for i in self._CF_BODY_INDICATORS) or \
               any(p in current_url for p in self._CF_URL_PATTERNS):
                return False
            selectors_cf = await self._page.evaluate(
                f"() => {{ return !!document.querySelector(\"{self._CF_SELECTORS}\"); }}"
            )
            if selectors_cf:
                return False
        return True

    async def is_cloudflare_challenge(self) -> bool:
        if self._page is None:
            return False
        try:
            title = await self._page.evaluate("() => document.title")
            current_url = self._page.url
        except Exception:
            return False
        return any(t in title.lower() for t in self._CF_TITLES) or \
               any(p in current_url for p in self._CF_URL_PATTERNS)

    async def validate_page_content(self) -> dict[str, str]:
        if self._page is None:
            raise RuntimeError("Browser not started.")
        title = await self._page.evaluate("() => document.title")
        current_url = self._page.url
        title_cf = any(t in title.lower() for t in self._CF_TITLES)
        url_cf = any(p in current_url for p in self._CF_URL_PATTERNS)
        result = {"title": title, "url": current_url,
                  "is_valid": not (title_cf or url_cf), "reason": ""}
        if title_cf:
            result["reason"] = f"Title matches Cloudflare challenge: '{title}'"
        elif url_cf:
            result["reason"] = f"URL matches Cloudflare challenge pattern: '{current_url}'"
        if not result["is_valid"]:
            raise CloudflareDetectedError(result["reason"])
        return result

    async def get_diagnostics(self) -> dict[str, str]:
        if self._page is None:
            return {"title": "N/A", "url": "N/A", "cf_status": "no_page",
                    "cookie_count": "0",
                    "browser_type": self._browser.browser_type or "N/A",
                    "browser_version": self._browser.browser_version or "N/A"}
        try:
            title = await self._page.evaluate("() => document.title")
            url = self._page.url
            cookie_count = str(await self._get_cookie_count())
            cf_active = "yes" if await self.is_cloudflare_challenge() else "no"
            return {"title": title, "url": url,
                    "cf_status": f"challenge_active={cf_active}",
                    "cookie_count": cookie_count,
                    "browser_type": self._browser.browser_type or "Google Chrome",
                    "browser_version": self._browser.browser_version or ""}
        except Exception as e:
            return {"title": f"Error: {e}", "url": "Error",
                    "cf_status": "error", "cookie_count": "0",
                    "browser_type": self._browser.browser_type or "N/A",
                    "browser_version": self._browser.browser_version or "N/A"}

    async def _get_cookie_count(self) -> int:
        if self._context is None:
            return 0
        try:
            return len(await self._context.cookies())
        except Exception:
            return 0

    async def check_cf_persistence(self, quiet: bool = False) -> bool:
        if self._context is None:
            return False
        cookies = await self._context.cookies()
        for c in cookies:
            if c.get("name", "") == "cf_clearance":
                if not quiet:
                    success(f"cf_clearance found: domain={c.get('domain','')} len={len(c.get('value',''))} expires={c.get('expires','')}")
                return True
        if not quiet:
            warning("No cf_clearance cookie found")
        return False

    async def collect_profile_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {}
        cookies = await self._context.cookies() if self._context else []
        stats["cookie_count"] = len(cookies)
        stats["domains"] = sorted(set(c.get("domain", "?") for c in cookies))

        profile_path = Path(self._browser.profile_dir)
        stats["profile_size_bytes"] = sum(
            f.stat().st_size for f in profile_path.rglob("*") if f.is_file()
        ) if profile_path.exists() else 0
        stats["file_count"] = sum(1 for _ in profile_path.rglob("*")) if profile_path.exists() else 0
        stats["history_exists"] = (profile_path / "History").exists()

        try:
            stats["ls_origins"] = await self._page.evaluate(
                "() => { try { return Object.keys(localStorage); } catch(e) { return []; } }"
            )
        except Exception:
            stats["ls_origins"] = []
        try:
            stats["idb_origins"] = await self._page.evaluate(
                "() => { try { return indexedDB.databases ? indexedDB.databases().then(d => d.map(x => x.name)).catch(() => []) : []; } catch(e) { return []; } }"
            )
        except Exception:
            stats["idb_origins"] = []

        return stats

    async def get_page_content(self) -> str:
        if self._page is None:
            return ""
        return await self._page.content()

    async def get_page_url(self) -> str:
        if self._page is None:
            return ""
        return self._page.url

    async def close(self) -> None:
        await self._browser.close()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._context

    async def get_cookies_async(self) -> list[dict[str, Any]]:
        if self._context is None:
            return []
        return await self._context.cookies()

    async def get_headers(self) -> dict[str, str]:
        return await self._browser.get_headers()
