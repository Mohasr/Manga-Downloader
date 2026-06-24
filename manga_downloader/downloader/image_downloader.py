"""Asynchronous image downloader with progress tracking and retry support."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.progress import Progress, TaskID

from ..utils.filesystem import ensure_dir, get_downloaded_images, get_file_size
from ..utils.logger import create_progress, error, info, warning
from .session_manager import SessionManager


@dataclass
class ChapterDownloadResult:
    """Result of downloading a single chapter's images."""

    image_paths: list[str] = field(default_factory=list)
    total_images: int = 0
    downloaded: int = 0
    failed: int = 0
    failed_indices: list[int] = field(default_factory=list)
    total_bytes: int = 0


class ImageDownloader:
    """Downloads images asynchronously with connection pooling and progress tracking."""

    def __init__(
        self,
        session_manager: SessionManager,
        max_concurrent: int = 5,
    ) -> None:
        """Initialize the downloader.

        Args:
            session_manager: SessionManager for HTTP connections.
            max_concurrent: Maximum concurrent downloads.
        """
        self.session_mgr = session_manager
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def download_images(
        self,
        image_urls: list[str],
        output_dir: str,
        chapter_name: str = "",
        referer: str = "",
        resume: bool = True,
    ) -> ChapterDownloadResult:
        """Download a list of images asynchronously.

        Args:
            image_urls: Ordered list of image URLs.
            output_dir: Directory to save images.
            chapter_name: Chapter name for progress display.
            referer: Referer URL for requests.
            resume: Whether to skip already downloaded images.

        Returns:
            ChapterDownloadResult with paths, counts, and stats.
        """
        ensure_dir(output_dir)

        if not image_urls:
            warning(f"No images to download for {chapter_name}")
            return ChapterDownloadResult(total_images=0)

        task_desc = chapter_name or "Downloading"

        existing: set[int] = set()
        if resume:
            try:
                ch_dir = Path(output_dir)
                ch_name = ch_dir.name
                # download_dir is root, manga_title is parent dir, chapter_filename is this dir
                existing = get_downloaded_images(
                    str(ch_dir.parent.parent),  # root download dir
                    ch_dir.parent.name,         # manga title (e.g. "Berserk")
                    ch_name,                    # chapter dir (e.g. "Chapter_001")
                )
            except Exception:
                pass

        results: dict[int, str] = {}
        failed_indices: list[int] = []
        byte_counts: dict[int, int] = {}

        sem = self._semaphore

        async def _download_one(index: int, url: str) -> None:
            async with sem:
                try:
                    ext = self._get_extension(url)
                    filename = f"{index + 1:04d}{ext}"
                    filepath = os.path.join(output_dir, filename)

                    if resume and (index + 1) in existing and os.path.exists(filepath):
                        results[index] = filepath
                        byte_counts[index] = get_file_size(filepath)
                        return

                    success_flag = await self.session_mgr.download_to_file(
                        url=url,
                        filepath=filepath,
                        referer=referer,
                    )

                    if success_flag and not _is_valid_image(filepath):
                        os.remove(filepath)
                        success_flag = False

                    if success_flag:
                        results[index] = filepath
                        byte_counts[index] = get_file_size(filepath)
                    else:
                        failed_indices.append(index + 1)

                except Exception:
                    failed_indices.append(index + 1)

        info(f"Downloading {len(image_urls)} images for {task_desc}...")

        with create_progress() as progress:
            task: TaskID = progress.add_task(
                f"[cyan]{task_desc}",
                total=len(image_urls),
            )

            tasks = [
                _download_one(i, url)
                for i, url in enumerate(image_urls)
            ]

            for coro in asyncio.as_completed(tasks):
                await coro
                progress.advance(task)

        ordered = [
            results[i]
            for i in sorted(results.keys())
            if i in results
        ]

        total_bytes = sum(byte_counts.values())

        if failed_indices:
            warning(f"Failed to download {len(failed_indices)} images: {failed_indices[:5]}{'...' if len(failed_indices) > 5 else ''}")

        return ChapterDownloadResult(
            image_paths=ordered,
            total_images=len(image_urls),
            downloaded=len(ordered),
            failed=len(failed_indices),
            failed_indices=failed_indices,
            total_bytes=total_bytes,
        )

    async def download_single(
        self,
        url: str,
        output_dir: str,
        filename: str = "",
        referer: str = "",
    ) -> str | None:
        """Download a single image.

        Args:
            url: Image URL.
            output_dir: Output directory.
            filename: Output filename (auto-generated if empty).
            referer: Referer URL.

        Returns:
            File path on success, None on failure.
        """
        ensure_dir(output_dir)

        if not filename:
            ext = self._get_extension(url)
            filename = f"image_{hash(url)}{ext}"

        filepath = os.path.join(output_dir, filename)
        success_flag = await self.session_mgr.download_to_file(
            url=url,
            filepath=filepath,
            referer=referer,
        )

        return filepath if success_flag else None

    @staticmethod
    def _get_extension(url: str) -> str:
        """Get the file extension from a URL."""
        url_lower = url.split("?")[0].lower()
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
            if url_lower.endswith(ext):
                return ext
        return ".jpg"


def _is_valid_image(filepath: str, min_size: int = 100) -> bool:
    """Check if a file contains a valid image by reading its magic bytes."""
    try:
        if os.path.getsize(filepath) < min_size:
            return False
        with open(filepath, "rb") as f:
            header = f.read(12)
        valid_headers = [
            (b'\xff\xd8\xff', "JPEG"),
            (b'\x89PNG\r\n\x1a\n', "PNG"),
            (b'RIFF', "WEBP"),
            (b'GIF8', "GIF"),
        ]
        for magic, _fmt in valid_headers:
            if header.startswith(magic):
                return True
        return False
    except (OSError, IOError):
        return False
