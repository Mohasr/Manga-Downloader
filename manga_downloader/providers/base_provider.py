"""Base provider interface for manga site downloaders.

Shared infrastructure (browser, Cloudflare, session, download/export
engines) is provided here. Site-specific providers inherit and
implement discovery methods.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

from ..browser.browser_manager import BrowserManager
from ..browser.playwright_manager import PlaywrightManager
from ..config import AppConfig
from ..downloader.image_downloader import ImageDownloader
from ..downloader.session_manager import SessionManager
from ..models import Chapter, DiscoveryResult, DownloadStats, Manga, OutputFormat
from ..scraper.cloudflare import CloudflareHandler
from ..scraper.manga_scraper import MangaScraper
from ..utils.logger import info, success


def _create_browser_manager(config: AppConfig, debug: bool) -> BrowserManager:
    """Factory: create the appropriate BrowserManager based on config."""
    profile_path = os.path.join(
        os.path.dirname(__file__), "..", config.profile_dir
    )
    backend = getattr(config.browser, "browser_backend", "playwright")
    if backend == "kameleo":
        from ..browser.kameleo_manager import KameleoManager
        return KameleoManager(profile_dir=profile_path, debug=debug,
                              kameleo_port=getattr(config.browser, "kameleo_port", 5050),
                              kameleo_executable=getattr(config.browser, "kameleo_executable", ""))
    return PlaywrightManager(profile_dir=profile_path, debug=debug)


class BaseMangaProvider(ABC):
    """Abstract base class for manga site providers."""

    SITE_NAME: str = "Unknown"
    SITE_DOMAINS: list[str] = []

    def __init__(self, config: AppConfig | None = None, debug: bool = False) -> None:
        self.config: AppConfig = config or AppConfig.get_instance()
        self.cf_handler: CloudflareHandler | None = None
        self.scraper: MangaScraper | None = None
        self.downloader: ImageDownloader | None = None
        self.session_mgr: SessionManager | None = None
        self.manga: Manga | None = None
        self.discovery: DiscoveryResult | None = None
        self._headers: dict[str, str] = {}
        self.stats: DownloadStats = DownloadStats()
        self.debug = debug
        self._browser_mgr: BrowserManager | None = None

    @classmethod
    def accepts_url(cls, url: str) -> bool:
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower().lstrip("www.")
        except Exception:
            return False
        for domain in cls.SITE_DOMAINS:
            if host == domain or host.endswith("." + domain):
                return True
        return False

    async def initialize(self) -> None:
        if self.cf_handler is not None:
            return

        info(f"Initializing {self.SITE_NAME} provider...")

        self._browser_mgr = _create_browser_manager(self.config, self.debug)
        info(f"Browser:     {self._browser_mgr.provider_name()}")

        self.cf_handler = CloudflareHandler(self._browser_mgr, debug=self.debug)
        await self.cf_handler.start(headless=self.config.browser.headless, mode="download")

        self.scraper = MangaScraper(self.cf_handler, debug=self.debug)

        browser_headers = await self.cf_handler.get_headers()
        self._headers = browser_headers

        self.session_mgr = SessionManager(
            headers=self._headers,
            timeout=self.config.download.request_timeout,
            max_retries=self.config.download.max_retries,
            retry_delay=self.config.download.retry_delay,
        )

        self.downloader = ImageDownloader(
            session_manager=self.session_mgr,
            max_concurrent=self.config.download.concurrent_downloads,
        )

        success(f"{self.SITE_NAME} provider initialized")

    @abstractmethod
    async def discover_manga(self, url: str) -> DiscoveryResult:
        ...

    @abstractmethod
    async def discover_chapter_images(self, chapter: Chapter) -> list[str]:
        ...

    @abstractmethod
    async def download_chapter(
        self, chapter: Chapter, output_format: OutputFormat = OutputFormat.CBZ
    ) -> bool:
        ...

    @abstractmethod
    async def download_chapters(
        self, chapters: list[Chapter], output_format: OutputFormat = OutputFormat.CBZ
    ) -> dict[str, bool]:
        ...

    @abstractmethod
    async def cleanup(self) -> None:
        ...

    async def get_headers(self) -> dict[str, str]:
        return self._headers
