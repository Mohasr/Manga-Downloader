"""Browser-based Madara provider — production workflow.

Uses Chrome + Playwright + persistent profile for all CF-protected
page navigation. CDP captures images from original browser responses
(Network.loadingFinished event — no re-fetch, no truncation).
BS4 parses chapter lists and image URLs from HTML.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext, Page

from ..config import AppConfig
from ..downloader.image_downloader import ChapterDownloadResult, ImageDownloader
from ..downloader.session_manager import SessionManager
from ..exporters.cbz_exporter import CbzExporter
from ..exporters.pdf_exporter import PdfExporter
from ..models import Chapter, DiscoveryResult, DownloadStats, DownloadStatus, Manga, OutputFormat, ProgressData
from ..utils.filesystem import chapter_dir, chapter_path, get_file_size, load_json, save_json, sanitize_filename
from ..utils.logger import chapter_info, error, info, success, warning
from .base_provider import BaseMangaProvider


class BrowserMadaraProvider(BaseMangaProvider):
    """Production provider: Playwright navigation + CDP image capture.

    Config per site via _SITES dict.  Uses persistent browser profile
    for Cloudflare trust.  No cookies.json dependency for navigation.
    """

    SITE_NAME = "Madara (Browser)"
    SITE_DOMAINS: list[str] = []

    _SITES: dict[str, dict[str, Any]] = {
        "manga-starz.net": {
            "name": "Manga Starz",
            "chapter_element_selector": ".wp-manga-chapter a",
            "title_selector": "h1, .post-title h1, .post-title h3",
            "image_container_selector": ".read-container, .reading-content, .image-list",
            "image_selector": "img",
        },
        "lek-manga.net": {
            "name": "Lek Manga",
            "chapter_element_selector": ".wp-manga-chapter a",
            "title_selector": "h1, .post-title h1, .post-title h3",
            "image_container_selector": ".read-container, .reading-content, .image-list",
            "image_selector": "img",
        },
    }

    def __init__(self, config: AppConfig | None = None, debug: bool = False) -> None:
        super().__init__(config=config, debug=debug)
        self._playwright: Any = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._site_url: str = ""
        self._site_config: dict[str, Any] = {}
        self._manga_slug: str = ""

    async def initialize(self) -> None:
        if self._context is not None:
            return

        info(f"Initializing {self.SITE_NAME} provider (browser)...")

        profile_path = str(self.config.profile_path.resolve())
        info(f"  Profile: {profile_path}")

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            channel=self.config.browser.chrome_channel,
            headless=self.config.browser.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-position=-32000,-32000",
            ],
            viewport={
                "width": self.config.browser.viewport_width,
                "height": self.config.browser.viewport_height,
            },
        )
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        await self._page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        # Session manager for non-CF requests (Ajax, static CDN)
        self.session_mgr = SessionManager(
            timeout=self.config.download.request_timeout,
            max_retries=self.config.download.max_retries,
            retry_delay=self.config.download.retry_delay,
        )
        self.downloader = ImageDownloader(
            session_manager=self.session_mgr,
            max_concurrent=self.config.download.concurrent_downloads,
        )

        info(f"  Browser: {await self._page.evaluate('() => navigator.userAgent')}..."[:100])
        success(f"{self.SITE_NAME} provider initialized")

    async def discover_manga(self, url: str) -> DiscoveryResult:
        if self._page is None:
            await self.initialize()

        self._detect_site_config(url)

        info(f"Discovering manga: {url}")
        t0 = time.time()

        resp = await self._page.goto(url, wait_until="load", timeout=30000)
        await asyncio.sleep(2)
        # Wait for CF to clear
        for _ in range(15):
            await asyncio.sleep(1)
            try:
                title = await self._page.title()
                if "Just a moment" not in title and title:
                    break
            except Exception:
                pass
        await asyncio.sleep(1)

        for attempt in range(3):
            try:
                html = await self._page.content()
                if len(html) > 500:
                    break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
        soup = BeautifulSoup(html, "lxml")

        # Title
        title = "Unknown Manga"
        for sel in self._site_config["title_selector"].split(","):
            title_el = soup.select_one(sel.strip())
            if title_el:
                title = title_el.get_text(strip=True)
                break

        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if not slug or slug == "manga":
            slug = url.rstrip("/").split("/")[-2]
        self._manga_slug = slug

        # Chapters
        chapters: list[Chapter] = []
        for el in soup.select(self._site_config["chapter_element_selector"]):
            href = el.get("href", "")
            if not href or "javascript" in href:
                continue
            text = el.get_text(strip=True)
            num = self._parse_chapter_number(text, href)
            ch_slug = href.rstrip("/").rsplit("/", 1)[-1]
            chapters.append(Chapter(number=num, title=text or f"Chapter {num}",
                                    url=href, slug=ch_slug))

        chapters.sort(key=lambda c: c.number)

        manga = Manga(title=title, slug=slug, url=url, chapters=chapters)
        self.manga = manga
        self.stats = DownloadStats(manga_title=title, manga_slug=slug)

        info(f"  Title: {title}  Chapters: {len(chapters)}  ({time.time() - t0:.1f}s)")
        return DiscoveryResult(
            manga=manga,
            chapter_selector_strategy="browser_html_wp_manga_chapter",
            image_selector_strategy="cdp_loading_finished",
        )

    async def discover_chapter_images(self, chapter: Chapter) -> list[str]:
        """Not used in browser workflow — CDP captures during download_chapter."""
        return chapter.image_urls

    async def download_chapter(
        self, chapter: Chapter, output_format: OutputFormat = OutputFormat.CBZ
    ) -> bool:
        if self._page is None:
            await self.initialize()

        chapter_info(chapter.display_number, "Starting download...")
        t0 = time.time()

        manga_title = self.manga.title if self.manga else "Unknown Manga"
        safe_title = sanitize_filename(manga_title)
        ch_dir = chapter_dir(self.config.download_path, safe_title)
        ch_fn = chapter.safe_filename
        out_dir = ch_dir / ch_fn
        out_dir.mkdir(parents=True, exist_ok=True)

        # Build chapter URL
        ch_url = chapter.url
        if "?style=list" not in ch_url:
            ch_url += "?style=list"

        # CDP session for capturing images via Network.loadingFinished
        cdp = await self._context.new_cdp_session(self._page)  # type: ignore[union-attr]
        await cdp.send("Network.enable")
        await cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})

        saved: dict[int, str] = {}
        pending: list[asyncio.Task] = []
        url_map: dict[str, str] = {}

        async def on_response(params):
            rid = params.get("requestId", "")
            url = params.get("response", {}).get("url", "")
            if any(domain in url for domain in self.SITE_DOMAINS):
                url_map[rid] = url

        async def on_loading_finished(params):
            rid = params.get("requestId", "")
            url = url_map.get(rid, "")
            if not url:
                return
            if not any(url.lower().endswith(e) for e in [".jpg", ".jpeg", ".png", ".webp"]):
                return
            if not any(domain in url for domain in self.SITE_DOMAINS) and "s2manhwa" not in url:
                return

            async def get():
                try:
                    result = await cdp.send("Network.getResponseBody", {"requestId": rid})
                    b = result.get("body", "")
                    if result.get("base64Encoded"):
                        b = base64.b64decode(b)
                    elif isinstance(b, str):
                        b = b.encode("latin-1")
                    if len(b) > 500:
                        name = url.split("/")[-1]
                        digits = "".join(c for c in name if c.isdigit())
                        idx = int(digits) if digits else len(saved) + 1
                        ext = ".jpg"
                        for e in [".png", ".webp", ".jpeg"]:
                            if name.lower().endswith(e): ext = e; break
                        path = out_dir / f"{idx:04d}{ext}"
                        path.write_bytes(b)
                        saved[idx] = str(path)
                except Exception:
                    pass
            pending.append(asyncio.ensure_future(get()))

        cdp.on("Network.responseReceived", on_response)
        cdp.on("Network.loadingFinished", on_loading_finished)

        # Navigate to chapter
        await self._page.goto(ch_url, wait_until="load", timeout=30000)
        await asyncio.sleep(2)
        # Scroll to trigger lazy loads
        for i in range(15):
            await self._page.evaluate(f"window.scrollTo(0, {(i+1)*800})")
            await asyncio.sleep(0.3)
        await asyncio.sleep(3)

        if pending:
            await asyncio.gather(*pending)
        await cdp.detach()

        # Validate downloaded images
        valid_paths: list[str] = []
        for idx in sorted(saved.keys()):
            p = saved[idx]
            if _is_valid_image_file(p):
                valid_paths.append(p)

        failed_count = len(saved) - len(valid_paths)
        if failed_count > 0:
            warning(f"  {failed_count} corrupted image(s) removed")

        self.stats.total_images += len(saved)
        self.stats.downloaded_images += len(valid_paths)
        self.stats.failed_images += failed_count

        success(f"Downloaded {len(valid_paths)} images for {chapter.display_number}")

        if not valid_paths:
            error(f"No valid images for {chapter.display_number}")
            self._record_error(chapter, "No valid images")
            return False

        # Export
        chap_ok = True
        chap_files: list[str] = []
        chap_errors: list[str] = []

        if output_format in (OutputFormat.PDF, OutputFormat.BOTH):
            pdf_path = chapter_path(self.config.download_path, safe_title, ch_fn, "pdf")
            try:
                PdfExporter(quality=self.config.export.pdf_quality).export(valid_paths, str(pdf_path))
                chap_files.append(pdf_path.name)
                self.stats.total_bytes += get_file_size(pdf_path)
            except Exception as e:
                chap_errors.append(f"PDF: {e}")
                chap_ok = False

        if output_format in (OutputFormat.CBZ, OutputFormat.BOTH):
            cbz_path = chapter_path(self.config.download_path, safe_title, ch_fn, "cbz")
            try:
                CbzExporter(compression=self.config.export.cbz_compression).export(valid_paths, str(cbz_path))
                chap_files.append(cbz_path.name)
                self.stats.total_bytes += get_file_size(cbz_path)
            except Exception as e:
                chap_errors.append(f"CBZ: {e}")
                chap_ok = False

        self.stats.output_files.extend(chap_files)

        if chap_ok:
            chapter.status = DownloadStatus.COMPLETED
            self._update_progress(chapter.number)
        else:
            for err in chap_errors:
                error(err)
                self.stats.errors.append(f"[{chapter.display_number}] {err}")

        elapsed = time.time() - t0
        info(f"  Chapter {chapter.display_number} complete in {elapsed:.1f}s")
        return chap_ok

    async def download_chapters(
        self, chapters: list[Chapter], output_format: OutputFormat = OutputFormat.CBZ
    ) -> dict[str, bool]:
        self.stats.total_chapters = len(chapters)
        results: dict[str, bool] = {}
        for i, chapter in enumerate(chapters, 1):
            info(f"Processing chapter {i}/{len(chapters)}: {chapter.display_number}")
            try:
                ok = await self.download_chapter(chapter, output_format)
                results[chapter.display_number] = ok
                if ok:
                    self.stats.completed_chapters += 1
                else:
                    self.stats.failed_chapters += 1
            except Exception as e:
                error(f"Failed: {chapter.display_number}: {e}")
                results[chapter.display_number] = False
                self.stats.failed_chapters += 1
                self._record_error(chapter, str(e))
        return results

    async def cleanup(self) -> None:
        if self.session_mgr:
            try:
                await self.session_mgr.close()
            except Exception:
                pass
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass

    async def get_headers(self) -> dict[str, str]:
        return {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _detect_site_config(self, url: str) -> None:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().lstrip("www.")
        self.SITE_DOMAINS = [host]
        for domain, cfg in self._SITES.items():
            if host == domain or host.endswith("." + domain):
                self._site_config = cfg
                self.SITE_NAME = cfg["name"]
                self._site_url = f"https://{domain}"
                return
        self._site_config = next(iter(self._SITES.values()))
        self._site_url = f"https://{host}"
        self.SITE_NAME = host
        self.SITE_DOMAINS = [host]

    @staticmethod
    def _parse_chapter_number(text: str, href: str) -> float:
        parts = href.rstrip("/").split("/")
        for part in reversed(parts):
            try:
                if re.match(r"^\d+_\d+$", part):
                    part = part.replace("_", ".")
                return float(part)
            except ValueError:
                continue
        m = re.search(r"(\d+\.?\d*)", text or "")
        if m:
            try: return float(m.group(1))
            except ValueError: pass
        return 0.0

    @classmethod
    def accepts_url(cls, url: str) -> bool:
        try:
            from urllib.parse import urlparse
            host = urlparse(url).netloc.lower().lstrip("www.")
            return any(host == k or host.endswith("." + k) for k in cls._SITES)
        except Exception:
            return False

    def _record_error(self, chapter: Chapter, msg: str) -> None:
        self.stats.errors.append(f"[{chapter.display_number}] {msg}")

    def _update_progress(self, chapter_num: float) -> None:
        if self.manga is None: return
        progress_path = self.config.progress_path
        data = load_json(progress_path)
        if not isinstance(data, dict): data = {}
        slug = self.manga.slug
        if slug not in data:
            data[slug] = {"manga_slug": slug, "manga_title": self.manga.title,
                          "manga_url": self.manga.url, "completed_chapters": [],
                          "failed_chapters": [], "last_completed": None}
        entry = data[slug]
        completed = list(entry.get("completed_chapters", []))
        if chapter_num not in completed:
            completed.append(chapter_num)
        entry["completed_chapters"] = completed
        entry["last_completed"] = chapter_num
        save_json(progress_path, data)

    def load_progress(self) -> ProgressData | None:
        if self.manga is None: return None
        data = load_json(self.config.progress_path)
        for _k, v in data.items():
            if isinstance(v, dict) and v.get("manga_slug") == self.manga.slug:
                return ProgressData(**v)
        return None


def _is_valid_image_file(filepath: str, min_size: int = 100) -> bool:
    try:
        if os.path.getsize(filepath) < min_size:
            return False
        with open(filepath, "rb") as f:
            header = f.read(12)
        for magic in (b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'RIFF', b'GIF8'):
            if header.startswith(magic):
                return True
        return False
    except (OSError, IOError):
        return False
