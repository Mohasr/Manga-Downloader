"""Lek Manga provider — site-specific discovery and download logic.

Lek Manga (lek-manga.net) uses a similar structure to Manga Starz
and also uses Cloudflare. Shares all infrastructure from BaseMangaProvider.
"""

from __future__ import annotations

from ..models import Chapter, DiscoveryResult, DownloadStats, Manga, OutputFormat, ProgressData
from ..scraper.cloudflare import CloudflareDetectedError, DiscoveryValidationError
from ..utils.filesystem import chapter_dir, chapter_path, get_file_size, load_json, save_json, sanitize_filename
from ..utils.logger import chapter_info, error, info, success, warning
from .base_provider import BaseMangaProvider
from ..downloader.image_downloader import ChapterDownloadResult
from ..exporters.cbz_exporter import CbzExporter
from ..exporters.pdf_exporter import PdfExporter


class LekMangaProvider(BaseMangaProvider):
    """Lek Manga (lek-manga.net) provider."""

    SITE_NAME = "Lek Manga"
    SITE_DOMAINS = ["lek-manga.net"]

    async def discover_manga(self, url: str) -> DiscoveryResult:
        """Discover manga info and chapters from a Lek Manga manga page."""
        if self.scraper is None:
            raise RuntimeError("Provider not initialized.")

        self.discovery = await self.scraper.discover_manga(url)
        self.manga = self.discovery.manga

        self.stats = DownloadStats()
        if self.manga:
            self.stats.manga_title = self.manga.title
            self.stats.manga_slug = self.manga.slug

        if self.discovery.referer_requirements and self.session_mgr:
            self.session_mgr.set_referer_overrides(self.discovery.referer_requirements)

        return self.discovery

    async def discover_chapter_page(
        self, chapter_url: str, manga_slug: str = ""
    ) -> tuple[str, str, list[str]]:
        """Discover info from a direct chapter page without chapter list discovery."""
        if self.scraper is None:
            raise RuntimeError("Provider not initialized.")
        return await self.scraper.discover_chapter_page(chapter_url, manga_slug)

    async def discover_chapter_images(self, chapter: Chapter) -> list[str]:
        """Discover image URLs for a chapter."""
        if self.scraper is None:
            raise RuntimeError("Provider not initialized.")

        manga_url = self.manga.url if self.manga else ""
        image_urls = await self.scraper.discover_chapter_images(chapter, referer=manga_url)
        chapter.image_urls = image_urls
        chapter.page_count = len(image_urls)
        return image_urls

    async def download_chapter(
        self,
        chapter: Chapter,
        output_format: OutputFormat = OutputFormat.CBZ,
    ) -> bool:
        """Download and export a single chapter."""
        if self.downloader is None:
            raise RuntimeError("Provider not initialized or no manga discovered.")

        chapter_info(chapter.display_number, "Starting download...")

        if not chapter.image_urls:
            chapter.image_urls = await self.discover_chapter_images(chapter)

        if not chapter.image_urls:
            error(f"No images found for {chapter.display_number}")
            self._record_chapter_error(chapter, "No images found")
            return False

        manga_title = self.manga.title if self.manga else "Unknown Manga"
        safe_title = sanitize_filename(manga_title)
        ch_dir = chapter_dir(self.config.download_path, safe_title)
        ch_fn = chapter.safe_filename

        result: ChapterDownloadResult = await self.downloader.download_images(
            image_urls=chapter.image_urls,
            output_dir=str(ch_dir / ch_fn),
            chapter_name=chapter.display_number,
            referer=chapter.url,
        )

        if not result.image_paths:
            error(f"No images downloaded for {chapter.display_number}")
            self._record_chapter_error(chapter, "No images downloaded")
            return False

        self.stats.total_images += result.total_images
        self.stats.downloaded_images += result.downloaded
        self.stats.failed_images += result.failed
        self.stats.total_bytes += result.total_bytes

        success(f"Downloaded {result.downloaded} images for {chapter.display_number}")

        chap_ok = True
        chap_files: list[str] = []
        chap_errors: list[str] = []

        if output_format in (OutputFormat.PDF, OutputFormat.BOTH):
            pdf_path = chapter_path(self.config.download_path, safe_title, ch_fn, "pdf")
            try:
                pdf_exporter = PdfExporter(quality=self.config.export.pdf_quality)
                pdf_exporter.export(result.image_paths, str(pdf_path))
                chap_files.append(pdf_path.name)
                self.stats.total_bytes += get_file_size(pdf_path)
            except Exception as e:
                chap_errors.append(f"PDF export failed: {e}")
                chap_ok = False

        if output_format in (OutputFormat.CBZ, OutputFormat.BOTH):
            cbz_path = chapter_path(self.config.download_path, safe_title, ch_fn, "cbz")
            try:
                cbz_exporter = CbzExporter(compression=self.config.export.cbz_compression)
                cbz_exporter.export(result.image_paths, str(cbz_path))
                chap_files.append(cbz_path.name)
                self.stats.total_bytes += get_file_size(cbz_path)
            except Exception as e:
                chap_errors.append(f"CBZ export failed: {e}")
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
        """Download multiple chapters sequentially."""
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
                error(f"Failed to process {chapter.display_number}: {e}")
                results[chapter.display_number] = False
                self.stats.failed_chapters += 1
                self._record_chapter_error(chapter, str(e))

        return results

    def _record_chapter_error(self, chapter: Chapter, msg: str) -> None:
        self.stats.errors.append(f"[{chapter.display_number}] {msg}")

    async def get_headers(self) -> dict[str, str]:
        return self._headers

    def load_progress(self) -> ProgressData | None:
        """Load download progress from cache."""
        if self.manga is None:
            return None

        progress_path = self.config.progress_path
        data = load_json(progress_path)

        for key, value in data.items():
            slug = value.get("manga_slug", "")
            if slug == self.manga.slug:
                return ProgressData(
                    manga_slug=value.get("manga_slug", ""),
                    manga_title=value.get("manga_title", ""),
                    manga_url=value.get("manga_url", ""),
                    completed_chapters=value.get("completed_chapters", []),
                    failed_chapters=value.get("failed_chapters", []),
                    last_completed=value.get("last_completed"),
                )

        return None

    def _update_progress(self, chapter_num: float) -> None:
        """Update progress data after completing a chapter."""
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

    async def cleanup(self) -> None:
        if self.session_mgr:
            try:
                await self.session_mgr.close()
            except Exception:
                pass
        if self.cf_handler:
            try:
                await self.cf_handler.close()
            except Exception:
                pass
        if self.debug:
            info("[debug] Provider cleaned up")
