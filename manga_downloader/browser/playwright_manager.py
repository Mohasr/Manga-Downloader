"""Playwright Chrome browser backend.

Wraps launch_persistent_context(channel="chrome") in the BrowserManager
interface. This is the original backend, preserved and isolated.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, BrowserContext, Page

from .browser_manager import BrowserManager
from ..utils.logger import info, success, warning, error


class PlaywrightManager(BrowserManager):
    """Browser backend using Playwright + real Google Chrome.

    Uses launch_persistent_context(channel="chrome") with a persistent
    user data directory for cookie/cache/LocalStorage persistence.
    """

    PROVIDER = "Playwright Chrome"

    async def start(self, headless: bool = False, mode: str = "unknown") -> tuple[BrowserContext, Page]:
        self._playwright = await async_playwright().start()

        profile_path = str(Path(self.profile_dir).resolve())

        info(f"Browser:     {self.PROVIDER}")
        info(f"Profile:     {profile_path}")
        info(f"Headless:    {headless}")

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            channel="chrome",
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        self._page = (
            self._context.pages[0] if self._context.pages
            else await self._context.new_page()
        )

        await self._page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        try:
            await asyncio.wait_for(
                self._page.goto("about:blank", wait_until="domcontentloaded"), timeout=10
            )
        except (asyncio.TimeoutError, Exception):
            pass

        self._browser_ua = await self._page.evaluate("() => navigator.userAgent")
        self._browser_type = await self._page.evaluate(
            "() => { const ua = navigator.userAgent; "
            "return ua.includes('Chrome/') && !ua.includes('Chromium') ? 'Google Chrome' : "
            "(ua.includes('Chromium') ? 'Chromium' : ua.includes('Edge/') ? 'Edge' : 'Unknown'); }"
        )
        self._browser_version = await self._page.evaluate(
            "() => { const m = navigator.userAgent.match(/Chrome\\/(\\d+\\.\\d+\\.\\d+\\.\\d+)/); "
            "return m ? m[1] : 'unknown'; }"
        )

        cookie_count = len(await self._context.cookies()) if self._context else 0
        info(f"Browser:     {self._browser_type} {self._browser_version}")
        info(f"Cookies:     {cookie_count}")

        return self._context, self._page

    async def close(self) -> None:
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            finally:
                self._page = None
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            finally:
                self._context = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            finally:
                self._playwright = None

    async def get_headers(self) -> dict[str, str]:
        if self._page is None:
            return {}
        try:
            user_agent = await self._page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            )
        return {
            "User-Agent": user_agent,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }

    def provider_name(self) -> str:
        return self.PROVIDER

    def collect_fingerprint(self) -> dict[str, Any]:
        return {"backend": self.PROVIDER, "note": "Playwright Chrome — standard fingerprint"}
