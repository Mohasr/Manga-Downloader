"""Manga page scraper using Playwright and dynamic discovery.

Opens manga/chapter pages using Playwright, discovers chapter lists
and image URLs dynamically through HTML analysis and network monitoring.
Includes validation to reject Cloudflare challenge pages.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..discovery.html_analyzer import HTMLAnalyzer
from ..discovery.network_analyzer import NetworkAnalyzer
from ..models import Chapter, DiscoveryResult, Manga
from ..utils.logger import info, success, warning, error
from .cloudflare import CloudflareDetectedError, DiscoveryValidationError


class MangaScraper:
    """Scrapes manga and chapter pages using Playwright with dynamic discovery."""

    def __init__(self, cf_handler: Any, debug: bool = False) -> None:
        """Initialize the scraper.

        Args:
            cf_handler: CloudflareHandler instance for browser management.
            debug: Enable verbose debug logging.
        """
        self.cf = cf_handler
        self.network: NetworkAnalyzer = NetworkAnalyzer()
        self._attached: bool = False
        self.debug = debug

    async def _ensure_network_attached(self) -> None:
        """Attach network analyzer to the page if not already attached."""
        if not self._attached and self.cf._page:
            await self.network.attach_to_page(self.cf._page)
            self._attached = True

    async def _validate_page(self) -> None:
        """Validate that the current page is not a Cloudflare challenge.

        Raises:
            CloudflareDetectedError: If challenge page detected.
        """
        await self.cf.validate_page_content()

    async def discover_manga(self, url: str) -> DiscoveryResult:
        """Discover manga info and chapters from a manga page.

        Args:
            url: Manga page URL.

        Returns:
            DiscoveryResult with manga info and chapter list.

        Raises:
            CloudflareDetectedError: If the page is still a Cloudflare challenge.
            DiscoveryValidationError: If discovery yields invalid results.
        """
        info("Starting manga discovery phase...")
        self.network.reset()
        self._attached = False

        await self.cf.navigate_with_cf_bypass(url, caller="manga_discovery")
        await self._ensure_network_attached()

        await asyncio.sleep(3)

        await self._validate_page()

        html = await self.cf.get_page_content()
        current_url = await self.cf.get_page_url()

        analyzer = HTMLAnalyzer(html, current_url)

        title = analyzer.find_manga_title()
        slug = analyzer.find_manga_slug()

        self._validate_discovery_title(title)

        if self.debug:
            diag = await self.cf.get_diagnostics()
            info(f"[debug] Page title : {diag.get('title', 'N/A')}")
            info(f"[debug] Page URL   : {diag.get('url', 'N/A')}")
            info(f"[debug] Manga title: {title}")
            info(f"[debug] Manga slug : {slug}")

        raw_chapters = analyzer.find_chapter_links()

        if self.debug:
            info(f"[debug] Raw chapter links found: {len(raw_chapters)}")

        chapters: list[Chapter] = []
        for rc in raw_chapters:
            chapter = Chapter(
                number=rc.get("number", 0),
                title=rc.get("title", ""),
                url=rc.get("url", ""),
                slug=rc.get("slug", ""),
            )
            chapters.append(chapter)

        chapters.sort(key=lambda c: c.number)

        manga = Manga(
            title=title,
            slug=slug,
            url=url,
            chapters=chapters,
        )

        if self.debug:
            info(f"[debug] Final chapter count: {len(chapters)}")

        if not chapters:
            warning("No chapters discovered. The page structure may not contain chapter links.")
            warning("If this is a chapter page (not a manga listing), use a manga URL instead.")

        network_report = self.network.get_discovery_report()

        image_hosts = network_report.get("image_analysis", {}).get("cdn_hosts", [])
        referer_reqs = network_report.get("referer_requirements", {})

        headers = {}
        header_analysis = network_report.get("header_analysis", {})
        req_headers = header_analysis.get("request_headers", {})
        if isinstance(req_headers, dict):
            for k, v in req_headers.items():
                if k.lower() in ("user-agent", "accept", "accept-language", "accept-encoding"):
                    if isinstance(v, str):
                        headers[k] = v

        result = DiscoveryResult(
            manga=manga,
            site_patterns={
                "chapter_selector_strategy": "dynamic_html_analysis",
                "image_selector_strategy": "dynamic_html_analysis",
                "discovery_method": "playwright_browser",
            },
            chapter_selector_strategy="multi_strategy_html_analysis",
            image_selector_strategy="multi_strategy_html_analysis",
            image_hosts=image_hosts,
            required_headers=headers,
            referer_requirements=referer_reqs,
        )

        info(f"Discovery phase complete -- {len(chapters)} chapters found")
        return result

    async def discover_chapter_page(
        self, chapter_url: str, manga_slug: str = ""
    ) -> tuple[str, str, list[str]]:
        """Discover info from a chapter page without requiring chapter list discovery.

        This is the dedicated flow for direct chapter URLs.
        Navigates to the chapter page and extracts:
        - Page title (for manga title inference)
        - Manga slug (from URL path)
        - Image URLs

        Args:
            chapter_url: Direct chapter page URL.
            manga_slug: Expected manga slug (from URL parsing), used as fallback.

        Returns:
            Tuple of (manga_title, manga_slug, image_urls).

        Raises:
            CloudflareDetectedError: If the page is still a Cloudflare challenge.
        """
        info("Opening chapter page...")
        self.network.reset()
        self._attached = False

        await self.cf.navigate_with_cf_bypass(chapter_url, caller="chapter_discovery")
        await self._ensure_network_attached()

        await asyncio.sleep(2)

        await self._validate_page()

        html = await self.cf.get_page_content()
        current_url = await self.cf.get_page_url()

        analyzer = HTMLAnalyzer(html, current_url)

        raw_title = analyzer.find_manga_title()
        slug = manga_slug or analyzer.find_manga_slug()

        self._validate_discovery_title(raw_title)

        if self.debug:
            diag = await self.cf.get_diagnostics()
            info(f"[debug] Chapter page title : {diag.get('title', 'N/A')}")
            info(f"[debug] Chapter page URL   : {diag.get('url', 'N/A')}")
            info(f"[debug] Derived manga title: {raw_title}")
            info(f"[debug] Derived manga slug : {slug}")

        await self.cf._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)

        html = await self.cf.get_page_content()
        analyzer = HTMLAnalyzer(html, current_url)

        discovered_images = analyzer.find_image_urls()

        if not discovered_images:
            network_images = self.network.image_urls
            if network_images:
                warning(f"No images found in DOM, using {len(network_images)} from network")
                return raw_title, slug, network_images

        image_urls: list[str] = []
        for img in discovered_images:
            url = img.get("url", "")
            if url:
                image_urls.append(url)

        image_urls = list(dict.fromkeys(image_urls))

        if self.debug:
            info(f"[debug] Image URLs discovered: {len(image_urls)}")

        if image_urls:
            success(f"Found {len(image_urls)} images")
        else:
            warning("No images discovered on chapter page")

        return raw_title, slug, image_urls

    async def discover_chapter_images(
        self, chapter: Chapter, referer: str = ""
    ) -> list[str]:
        """Discover image URLs from a chapter page.

        Args:
            chapter: Chapter object with URL to scrape.
            referer: Referer URL for the request.

        Returns:
            List of image URLs in order.

        Raises:
            CloudflareDetectedError: If the page is still a Cloudflare challenge.
        """
        info(f"Discovering images for {chapter.display_number}...")
        self.network.reset()
        self._attached = False

        await self.cf.navigate_with_cf_bypass(chapter.url, caller="image_discovery")
        await self._ensure_network_attached()

        await asyncio.sleep(2)

        await self._validate_page()

        await self.cf._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)

        html = await self.cf.get_page_content()
        current_url = await self.cf.get_page_url()

        analyzer = HTMLAnalyzer(html, current_url)

        discovered_images = analyzer.find_image_urls()

        if not discovered_images:
            network_images = self.network.image_urls
            if network_images:
                warning(f"No images found in DOM, using {len(network_images)} from network")
                return network_images

        image_urls: list[str] = []
        for img in discovered_images:
            url = img.get("url", "")
            if url:
                image_urls.append(url)

        image_urls = list(dict.fromkeys(image_urls))

        if self.debug:
            info(f"[debug] Images found: {len(image_urls)}")

        if image_urls:
            success(f"Found {len(image_urls)} images")
        else:
            warning("No images discovered")

        return image_urls

    @staticmethod
    def _validate_discovery_title(title: str) -> None:
        """Validate that the discovered title is not a Cloudflare challenge page.

        Args:
            title: The extracted title string.

        Raises:
            DiscoveryValidationError: If the title appears to be a challenge page.
        """
        cf_titles = [
            "just a moment",
            "please wait",
            "checking your browser",
            "attention required",
            "one moment",
            "please stand by",
        ]

        title_lower = title.lower().strip()

        for cf_title in cf_titles:
            if cf_title in title_lower:
                raise DiscoveryValidationError(
                    f"Discovered title '{title}' matches Cloudflare challenge pattern. "
                    f"The page has not loaded properly."
                )

        if not title or title in ("", "Unknown Manga"):
            raise DiscoveryValidationError(
                "Could not determine manga title. The page may not be a valid manga page."
            )
