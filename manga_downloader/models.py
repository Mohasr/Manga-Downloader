"""Data models for Manga Downloader."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OutputFormat(str, Enum):
    """Output format for chapter export."""

    PDF = "pdf"
    CBZ = "cbz"
    BOTH = "both"


class DownloadStatus(str, Enum):
    """Status of a chapter download."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Chapter:
    """Represents a manga chapter."""

    number: float
    title: str
    url: str
    slug: str = ""
    image_urls: list[str] = field(default_factory=list)
    status: DownloadStatus = DownloadStatus.PENDING
    page_count: int = 0

    @property
    def display_number(self) -> str:
        """Format chapter number for display and filenames."""
        if self.number == int(self.number):
            return f"Chapter {int(self.number):03d}"
        return f"Chapter {self.number:05.1f}"

    @property
    def safe_filename(self) -> str:
        """Filesystem-safe chapter filename."""
        if self.number == int(self.number):
            return f"Chapter_{int(self.number):03d}"
        return f"Chapter_{self.number:05.1f}"


@dataclass
class Manga:
    """Represents a manga series."""

    title: str
    slug: str
    url: str
    chapters: list[Chapter] = field(default_factory=list)
    cover_url: str = ""
    author: str = ""
    description: str = ""

    @property
    def chapter_count(self) -> int:
        """Total number of chapters."""
        return len(self.chapters)

    @property
    def sorted_chapters(self) -> list[Chapter]:
        """Return chapters sorted by number."""
        return sorted(self.chapters, key=lambda c: c.number)


@dataclass
class DiscoveryResult:
    """Results from the discovery phase."""

    manga: Manga
    site_patterns: dict[str, Any] = field(default_factory=dict)
    chapter_selector_strategy: str = ""
    image_selector_strategy: str = ""
    image_hosts: list[str] = field(default_factory=list)
    required_headers: dict[str, str] = field(default_factory=dict)
    referer_requirements: dict[str, str] = field(default_factory=dict)


@dataclass
class DownloadStats:
    """Aggregated download statistics for summary display."""

    manga_title: str = ""
    manga_slug: str = ""
    total_chapters: int = 0
    completed_chapters: int = 0
    failed_chapters: int = 0
    total_images: int = 0
    downloaded_images: int = 0
    failed_images: int = 0
    total_bytes: int = 0
    elapsed_seconds: float = 0.0
    output_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    chapter_stats: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total_size_display(self) -> str:
        """Human-readable total size."""
        return _format_bytes(self.total_bytes)

    @property
    def elapsed_display(self) -> str:
        """Human-readable elapsed time."""
        total_secs = int(self.elapsed_seconds)
        hours = total_secs // 3600
        minutes = (total_secs % 3600) // 60
        seconds = total_secs % 60
        if hours > 0:
            return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"
        return f"{minutes:02d}m {seconds:02d}s"


def _format_bytes(num_bytes: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1000:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1000
    return f"{num_bytes:.1f} PB"


@dataclass
class ProgressData:
    """Tracks download progress for resume support."""

    manga_slug: str
    manga_title: str
    manga_url: str
    completed_chapters: list[float] = field(default_factory=list)
    failed_chapters: list[float] = field(default_factory=list)
    last_completed: float | None = None

    def mark_completed(self, chapter_num: float) -> None:
        """Mark a chapter as completed."""
        if chapter_num not in self.completed_chapters:
            self.completed_chapters.append(chapter_num)
        self.last_completed = chapter_num

    def mark_failed(self, chapter_num: float) -> None:
        """Mark a chapter as failed."""
        if chapter_num not in self.failed_chapters:
            self.failed_chapters.append(chapter_num)

    def is_completed(self, chapter_num: float) -> bool:
        """Check if a chapter has been completed."""
        return chapter_num in self.completed_chapters
