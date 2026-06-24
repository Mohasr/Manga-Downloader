"""Filesystem utilities for Manga Downloader."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename.

    Removes characters that are invalid on Windows/macOS/Linux filesystems.
    """
    forbidden = r'[<>:"/\\|?*\x00-\x1f]'
    sanitized = re.sub(forbidden, "_", name)
    sanitized = sanitized.strip(". ")
    if not sanitized:
        sanitized = "untitled"
    max_len = 200
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len]
    return sanitized


def ensure_dir(path: str | Path) -> Path:
    """Ensure a directory exists, creating it if necessary.

    Args:
        path: Directory path.

    Returns:
        Path object for the directory.
    """
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_json(path: str | Path) -> dict[str, Any]:
    """Load a JSON file, returning empty dict if not found or invalid."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_json(path: str | Path, data: dict[str, Any] | list[Any]) -> None:
    """Save data to a JSON file atomically."""
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    tmp.replace(p)


def chapter_dir(download_dir: str | Path, manga_title: str) -> Path:
    """Get the download directory for a manga series.

    Args:
        download_dir: Root downloads directory.
        manga_title: Title of the manga.

    Returns:
        Path to the manga's download directory.
    """
    safe_title = sanitize_filename(manga_title)
    return ensure_dir(Path(download_dir) / safe_title)


def chapter_path(
    download_dir: str | Path,
    manga_title: str,
    chapter_filename: str,
    extension: str,
) -> Path:
    """Get the full path for a chapter export file.

    Args:
        download_dir: Root downloads directory.
        manga_title: Title of the manga.
        chapter_filename: Chapter filename (e.g. "Chapter_001").
        extension: File extension (e.g. "pdf", "cbz").

    Returns:
        Full path to the export file.
    """
    ext = extension.lstrip(".")
    manga_dir = chapter_dir(download_dir, manga_title)
    return manga_dir / f"{chapter_filename}.{ext}"


def get_downloaded_images(download_dir: str | Path, manga_title: str, chapter_filename: str) -> set[int]:
    """Get the set of already downloaded image indices for a chapter.

    Args:
        download_dir: Root downloads directory.
        manga_title: Title of the manga.
        chapter_filename: Chapter filename.

    Returns:
        Set of image indices already downloaded.
    """
    p = Path(download_dir) / sanitize_filename(manga_title) / chapter_filename
    if not p.exists():
        return set()
    downloaded: set[int] = set()
    for f in p.iterdir():
        if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
            stem = f.stem
            try:
                idx = int(re.findall(r"\d+", stem)[-1])
            except (IndexError, ValueError):
                # Extract the last number from the filename
                nums = re.findall(r"\d+", stem)
                if nums:
                    idx = int(nums[-1])
                else:
                    continue
            downloaded.add(idx)
    return downloaded


def get_file_size(path: str | Path) -> int:
    """Get file size in bytes, returning 0 if file doesn't exist."""
    p = Path(path)
    if not p.exists():
        return 0
    return p.stat().st_size


def disk_space_available(path: str | Path) -> int:
    """Get available disk space in bytes for the drive containing the path."""
    try:
        import shutil
        return shutil.disk_usage(path).free
    except (ImportError, OSError):
        return 2 ** 40
