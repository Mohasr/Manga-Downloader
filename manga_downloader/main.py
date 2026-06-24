"""Main entry point for Manga Downloader.

Usage:
    python -m manga_downloader.main [manga_url]
    manga-downloader [manga_url]
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    main()
