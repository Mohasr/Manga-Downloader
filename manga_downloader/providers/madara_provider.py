"""Generic Madara provider — requests-centric.

Replaces MangaStarzProvider and LekMangaProvider with a single
provider that works for any WordPress + Madara theme site.

Uses CookieManager for CF clearance and requests.Session for all
HTTP operations. No browser, no Playwright, no aiohttp.

Supports:
  - manga-starz.net  (starzmanga theme — Madara fork)
  - lek-manga.net    (standard madara theme)
  - Any future Madara-based site (config-driven)
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..config import AppConfig
from ..cookie_manager import CookieManager
from ..downloader.image_downloader import ChapterDownloadResult, ImageDownloader
from ..downloader.session_manager import SessionManager
from ..exporters.cbz_exporter import CbzExporter
from ..exporters.pdf_exporter import PdfExporter
from ..models import Chapter, DiscoveryResult, DownloadStats, Manga, OutputFormat, ProgressData
from ..utils.filesystem import chapter_dir, chapter_path, get_file_size, load_json, save_json, sanitize_filename
from ..utils.logger import chapter_info, error, info, success, warning
from .base_provider import BaseMangaProvider


class MadaraProvider(BaseMangaProvider):
    """Generic provider for WordPress Madara theme sites.

    Configuration per site (see SITES dict).  No browser needed
    at runtime — cookies are provided via CookieManager.
    """

    SITE_NAME = "Madara"
    SITE_DOMAINS: list[str] = []

    # ------------------------------------------------------------------
    # Site registry — add sites here.  Each entry maps domain -> config.
    # ------------------------------------------------------------------
    _SITES: dict[str, dict[str, Any]] = {
        "manga-starz.net": {
            "name": "Manga Starz",
            "manga_path": "/manga/{slug}/",
            "chapter_path": "/manga/{manga_slug}/{chapter_slug}/",
            "chapter_list_style": False,  # list-style chapters are default
            "image_container_selector": ".read-container, .reading-content, .image-list",
            "image_selector": "img.wp-manga-chapter-img, img",
            "image_attr_priority": ["data-src", "data-lazy-src", "src"],
            "chapter_element_selector": ".wp-manga-chapter a",
            "title_selector": "h1, .post-title h1, .post-title h3",
        },
        "lek-manga.net": {
            "name": "Lek Manga",
            "manga_path": "/manga/{slug}/",
            "chapter_path": "/manga/{manga_slug}/{chapter_slug}/",
            "chapter_list_style": True,  # append ?style=list
            "image_container_selector": ".read-container, .reading-content, .image-list",
            "image_selector": "img.wp-manga-chapter-img, img",
            "image_attr_priority": ["data-src", "data-lazy-src", "src"],
            "chapter_element_selector": ".wp-manga-chapter a",
            "title_selector": "h1, .post-title h1, .post-title h3",
        },
    }

    def __init__(
        self,
        config: AppConfig | None = None,
        cookie_manager: CookieManager | None = None,
        debug: bool = False,
    ) -> None:
        super().__init__(config=config, debug=debug)
        self._cookie_mgr = cookie_manager or CookieManager()
        self._session: requests.Session | None = None
        self._site_url: str = ""
        self._site_config: dict[str, Any] = {}
        self._manga_slug: str = ""

    # ------------------------------------------------------------------
    # BaseMangaProvider interface (NO browser!)
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        if self._session is not None:
            return

        info(f"Initializing {self.SITE_NAME} provider (requests-only)...")

        if not self._cookie_mgr.loaded:
            self._cookie_mgr.load()

        # Create session with ALL available cookies from all sites
        self._session = self._cookie_mgr.create_session_all_sites()

        # Check cookie validity
        valid_sites = [s for s in self._cookie_mgr.sites if self._cookie_mgr.has_valid_cookies(s)]
        info(f"  Sites with valid cookies: {', '.join(valid_sites) if valid_sites else 'NONE'}")
        info(f"  Cookies captured: {self._cookie_mgr.captured_at_display}")

        # aiohttp session for image downloads (can pass cookies too)
        self.session_mgr = SessionManager(
            headers=dict(self._session.headers),
            timeout=self.config.download.request_timeout,
            max_retries=self.config.download.max_retries,
            retry_delay=self.config.download.retry_delay,
        )
        self.downloader = ImageDownloader(
            session_manager=self.session_mgr,
            max_concurrent=self.config.download.concurrent_downloads,
        )
        self._headers = dict(self._session.headers)

        info(f"  Cookies: {self._cookie_mgr.captured_at_display}")
        info(f"  cf_clearance: {'VALID' if self._cookie_mgr.has_valid_cookies(self.SITE_NAME) else 'MISSING'}")

        success(f"{self.SITE_NAME} provider initialized")

    async def discover_manga(self, url: str) -> DiscoveryResult:
        if self._session is None:
            await self.initialize()

        self._detect_site_config(url)

        info(f"Discovering manga: {url}")
        resp = self._session.get(url, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Manga page returned {resp.status_code}")

        html = resp.text
        soup = BeautifulSoup(html, "lxml")

        # Title
        title = "Unknown Manga"
        for sel in self._site_config.get("title_selector", "h1").split(","):
            title_el = soup.select_one(sel.strip())
            if title_el:
                title = title_el.get_text(strip=True)
                break

        # Slug from URL
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        if not slug or slug == "manga":
            slug = url.rstrip("/").split("/")[-2]

        self._manga_slug = slug

        # Chapters from HTML (.wp-manga-chapter elements)
        chapters: list[Chapter] = []
        for el in soup.select(self._site_config["chapter_element_selector"]):
            href = el.get("href", "")
            if not href or "javascript" in href:
                continue
            text = el.get_text(strip=True)
            num = self._parse_chapter_number(text, href)
            chapter_slug = href.rstrip("/").rsplit("/", 1)[-1]
            chapters.append(Chapter(
                number=num,
                title=text or f"Chapter {num}",
                url=href,
                slug=chapter_slug,
            ))

        # Sort oldest-first (typical Madara lists newest-first)
        chapters.sort(key=lambda c: c.number)

        manga = Manga(title=title, slug=slug, url=url, chapters=chapters)
        self.manga = manga
        self.stats = DownloadStats(manga_title=title, manga_slug=slug)

        info(f"  Title: {title}")
        info(f"  Chapters: {len(chapters)}")

        return DiscoveryResult(
            manga=manga,
            site_patterns={"theme": "madara"},
            chapter_selector_strategy="html_wp_manga_chapter",
            image_selector_strategy="html_madara_reader",
        )

    async def discover_chapter_page(
        self, chapter_url: str, manga_slug: str = ""
    ) -> tuple[str, str, list[str]]:
        """Discover info from a direct chapter URL (backward compat)."""
        if self._session is None:
            await self.initialize()

        self._detect_site_config(chapter_url)

        resp = self._session.get(chapter_url, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Chapter page returned {resp.status_code}")

        soup = BeautifulSoup(resp.text, "lxml")

        # Title from chapter page
        title = "Unknown Manga"
        for sel in self._site_config.get("title_selector", "h1").split(","):
            title_el = soup.select_one(sel.strip())
            if title_el:
                title = title_el.get_text(strip=True)
                break

        slug = manga_slug or self._extract_slug_from_url(chapter_url)

        # Extract images directly
        images = self._extract_images_from_soup(soup)
        return title, slug, images

    async def discover_chapter_images(self, chapter: Chapter) -> list[str]:
        if self._session is None:
            await self.initialize()

        chapter_url = self._build_chapter_url(chapter.slug or str(chapter.number))
        info(f"Discovering images: {chapter_url}")

        resp = self._session.get(chapter_url, timeout=30)
        if resp.status_code != 200:
            warning(f"Chapter page returned {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        images = self._extract_images_from_soup(soup)

        chapter.image_urls = images
        chapter.page_count = len(images)
        info(f"  Images: {len(images)}")
        return images

    async def download_chapter(
        self,
        chapter: Chapter,
        output_format: OutputFormat = OutputFormat.CBZ,
    ) -> bool:
        if self.downloader is None:
            await self.initialize()

        chapter_info(chapter.display_number, "Starting download...")

        if not chapter.image_urls:
            await self.discover_chapter_images(chapter)

        if not chapter.image_urls:
            error(f"No images found for {chapter.display_number}")
            self._record_error(chapter, "No images found")
            return False

        manga_title = self.manga.title if self.manga else "Unknown Manga"
        safe_title = sanitize_filename(manga_title)
        ch_dir = chapter_dir(self.config.download_path, safe_title)
        ch_fn = chapter.safe_filename

        result: ChapterDownloadResult = await self.downloader.download_images(
            image_urls=chapter.image_urls,
            output_dir=str(ch_dir / ch_fn),
            chapter_name=chapter.display_number,
            referer=self.manga.url if self.manga else "",
        )

        if not result.image_paths:
            error(f"No images downloaded for {chapter.display_number}")
            self._record_error(chapter, "No images downloaded")
            return False

        self.stats.total_images += result.total_images
        self.stats.downloaded_images += result.downloaded
        self.stats.failed_images += result.failed
        self.stats.total_bytes += result.total_bytes

        success(f"Downloaded {result.downloaded} images for {chapter.display_number}")

        # Export
        chap_ok = True
        chap_files: list[str] = []
        chap_errors: list[str] = []

        if output_format in (OutputFormat.PDF, OutputFormat.BOTH):
            pdf_path = chapter_path(self.config.download_path, safe_title, ch_fn, "pdf")
            try:
                PdfExporter(quality=self.config.export.pdf_quality).export(
                    result.image_paths, str(pdf_path)
                )
                chap_files.append(pdf_path.name)
                self.stats.total_bytes += get_file_size(pdf_path)
            except Exception as e:
                chap_errors.append(f"PDF: {e}")
                chap_ok = False

        if output_format in (OutputFormat.CBZ, OutputFormat.BOTH):
            cbz_path = chapter_path(self.config.download_path, safe_title, ch_fn, "cbz")
            try:
                CbzExporter(compression=self.config.export.cbz_compression).export(
                    result.image_paths, str(cbz_path)
                )
                chap_files.append(cbz_path.name)
                self.stats.total_bytes += get_file_size(cbz_path)
            except Exception as e:
                chap_errors.append(f"CBZ: {e}")
                chap_ok = False

        self.stats.output_files.extend(chap_files)

        if chap_ok:
            self._update_progress(chapter.number)
        else:
            for err in chap_errors:
                error(err)
                self.stats.errors.append(f"[{chapter.display_number}] {err}")

        return chap_ok

    async def download_chapters(
        self,
        chapters: list[Chapter],
        output_format: OutputFormat = OutputFormat.CBZ,
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
        self._session = None

    async def get_headers(self) -> dict[str, str]:
        return self._headers

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

        # Fallback for unknown Madara sites
        self._site_config = self._SITES.get(
            next(iter(self._SITES)), self._SITES["lek-manga.net"]
        )
        self._site_url = f"https://{host}"
        self.SITE_NAME = host
        self.SITE_DOMAINS = [host]

    def _build_chapter_url(self, chapter_slug: str) -> str:
        manga_slug = self._manga_slug
        path = self._site_config["chapter_path"].format(
            slug=manga_slug, manga_slug=manga_slug, chapter_slug=chapter_slug
        )
        url = f"{self._site_url}{path}"
        if self._site_config.get("chapter_list_style"):
            sep = "&" if "?" in url else "?"
            url += f"{sep}style=list"
        return url

    @staticmethod
    def _parse_chapter_number(text: str, href: str) -> float:
        # Try numeric suffix in URL
        parts = href.rstrip("/").split("/")
        for part in reversed(parts):
            try:
                if re.match(r"^\d+_\d+$", part):
                    part = part.replace("_", ".")
                return float(part)
            except ValueError:
                continue
        # Try numeric in text
        m = re.search(r"(\d+\.?\d*)", text or "")
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return 0.0

    @staticmethod
    def _extract_slug_from_url(url: str) -> str:
        from urllib.parse import urlparse
        segments = [s for s in urlparse(url).path.strip("/").split("/") if s]
        if len(segments) >= 2 and segments[0].lower() == "manga":
            return segments[1]
        return segments[-1] if segments else ""

    def _extract_images_from_soup(self, soup: BeautifulSoup) -> list[str]:
        images: list[str] = []
        for container_sel in self._site_config["image_container_selector"].split(","):
            container = soup.select_one(container_sel.strip())
            if not container:
                continue
            for img in container.select(self._site_config["image_selector"]):
                if img.parent and img.parent.name == "noscript":
                    continue
                src = None
                for attr in self._site_config["image_attr_priority"]:
                    val = img.get(attr)
                    if val:
                        src = val.strip()
                        break
                if not src:
                    srcset = img.get("data-lazy-srcset", "")
                    if srcset:
                        src = srcset.split()[0]
                if src and src not in images:
                    images.append(src)
            if images:
                break
        return images

    def _record_error(self, chapter: Chapter, msg: str) -> None:
        self.stats.errors.append(f"[{chapter.display_number}] {msg}")

    def _update_progress(self, chapter_num: float) -> None:
        if self.manga is None:
            return
        progress_path = self.config.progress_path
        data = load_json(progress_path)
        if not isinstance(data, dict):
            data = {}
        slug = self.manga.slug
        if slug not in data:
            data[slug] = {
                "manga_slug": slug,
                "manga_title": self.manga.title,
                "manga_url": self.manga.url,
                "completed_chapters": [],
                "failed_chapters": [],
                "last_completed": None,
            }
        entry = data[slug]
        completed: list[float] = list(entry.get("completed_chapters", []))
        if chapter_num not in completed:
            completed.append(chapter_num)
        entry["completed_chapters"] = completed
        entry["last_completed"] = chapter_num
        save_json(progress_path, data)

    def load_progress(self) -> ProgressData | None:
        if self.manga is None:
            return None
        progress_path = self.config.progress_path
        data = load_json(progress_path)
        for _key, value in data.items():
            if isinstance(value, dict) and value.get("manga_slug") == self.manga.slug:
                return ProgressData(
                    manga_slug=value.get("manga_slug", ""),
                    manga_title=value.get("manga_title", ""),
                    manga_url=value.get("manga_url", ""),
                    completed_chapters=value.get("completed_chapters", []),
                    failed_chapters=value.get("failed_chapters", []),
                    last_completed=value.get("last_completed"),
                )
        return None

    @classmethod
    def accepts_url(cls, url: str) -> bool:
        """Accept any URL — Madara provider is the catch-all."""
        try:
            from urllib.parse import urlparse
            host = urlparse(url).netloc.lower().lstrip("www.")
            return any(
                host == domain or host.endswith("." + domain)
                for domain in cls._SITES
            )
        except Exception:
            return False
