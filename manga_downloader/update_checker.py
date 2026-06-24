"""Update checker — detects new chapters for followed manga."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext, Page

try:
    from .follow_manager import FollowManager, FollowedManga
    from .utils.logger import info, success, warning
except ImportError:
    from follow_manager import FollowManager, FollowedManga
    from manga_downloader.utils.logger import info, success, warning


@dataclass
class UpdateResult:
    manga: FollowedManga
    previous_chapter: float
    current_latest: float
    new_chapters: int = 0
    checked_at: float = field(default_factory=time.time)

    @property
    def has_updates(self) -> bool:
        return self.new_chapters > 0


class UpdateChecker:
    """Checks followed manga for new chapters."""

    def __init__(self, follow_manager: FollowManager, profile_dir: str = "manga_downloader/browser_profile"):
        self._fm = follow_manager
        self._profile = profile_dir
        self._playwright = None
        self._context: BrowserContext | None = None

    async def initialize(self) -> None:
        if self._context is not None:
            return
        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self._profile),
            channel="chrome", headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

    async def check_all(self) -> list[UpdateResult]:
        await self.initialize()
        results = []
        for manga in self._fm.items:
            result = await self._check_one(manga)
            results.append(result)
        return results

    async def check_one(self, url: str) -> UpdateResult | None:
        await self.initialize()
        manga = self._fm.find_by_url(url)
        if not manga:
            return None
        return await self._check_one(manga)

    async def _check_one(self, manga: FollowedManga) -> UpdateResult:
        page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        try:
            resp = await page.goto(manga.url, wait_until="load", timeout=30000)
            await asyncio.sleep(2)
            for _ in range(10):
                await asyncio.sleep(1)
                try:
                    title = await page.title()
                    if "Just a moment" not in title and title:
                        break
                except Exception:
                    pass
            await asyncio.sleep(1)

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            chapter_links = soup.select(".wp-manga-chapter a")

            max_chapter = 0.0
            for a in chapter_links:
                href = a.get("href", "")
                if "javascript" in href:
                    continue
                parts = href.rstrip("/").split("/")
                for part in reversed(parts):
                    try:
                        if re.match(r"^\d+_\d+$", part):
                            part = part.replace("_", ".")
                        num = float(part)
                        if num > max_chapter:
                            max_chapter = num
                        break
                    except ValueError:
                        continue

            new_count = 0
            if manga.latest_known_chapter > 0 and max_chapter > manga.latest_known_chapter:
                new_count = int(max_chapter - manga.latest_known_chapter)

            self._fm.update_latest(manga.url, max_chapter)

            result = UpdateResult(
                manga=manga, previous_chapter=manga.latest_known_chapter,
                current_latest=max_chapter, new_chapters=new_count,
            )
            msg = f"{manga.title}: {manga.latest_known_chapter:.0f} -> {max_chapter:.0f}"
            if new_count > 0:
                success(f"  NEW: {msg} (+{new_count})")
            else:
                info(f"  {msg}")

            return result
        finally:
            if page and page != self._context.pages[0]:
                await page.close()

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
