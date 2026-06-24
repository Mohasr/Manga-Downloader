"""Manga metadata collector — extracts author, genres, status, cover from manga page."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup


@dataclass
class MangaMetadata:
    title: str = ""
    alternative_titles: list[str] = field(default_factory=list)
    author: str = ""
    artist: str = ""
    genres: list[str] = field(default_factory=list)
    status: str = ""  # Ongoing, Completed, etc.
    description: str = ""
    cover_url: str = ""
    url: str = ""
    site: str = ""
    chapter_count: int = 0
    collected_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "alternative_titles": self.alternative_titles,
            "author": self.author,
            "artist": self.artist,
            "genres": self.genres,
            "status": self.status,
            "description": self.description,
            "cover_url": self.cover_url,
            "url": self.url,
            "site": self.site,
            "chapter_count": self.chapter_count,
            "collected_at": self.collected_at,
        }


class MetadataCollector:
    """Extracts manga metadata from Madara theme HTML pages."""

    def extract(self, html: str, url: str = "", site: str = "") -> MangaMetadata:
        soup = BeautifulSoup(html, "lxml")
        meta = MangaMetadata(url=url, site=site)

        # Title
        for sel in ["h1", ".post-title h1", ".post-title h3"]:
            el = soup.select_one(sel)
            if el:
                meta.title = el.get_text(strip=True)
                break

        # Alternative titles
        alt_el = soup.select_one(".summary-alter, .alternative-title")
        if alt_el:
            meta.alternative_titles = [t.strip() for t in alt_el.get_text(strip=True).split(";") if t.strip()]

        # Author
        for sel in [".author-content a", ".artist-content a"]:
            for el in soup.select(sel):
                name = el.get_text(strip=True)
                if name and name not in meta.author:
                    if meta.author:
                        meta.author += ", "
                    meta.author += name

        # Artist (try artist-content first)
        artist_el = soup.select_one(".artist-content")
        if artist_el:
            meta.artist = artist_el.get_text(strip=True)

        # Genres
        for el in soup.select(".genres-content a"):
            genre = el.get_text(strip=True)
            if genre and genre not in meta.genres:
                meta.genres.append(genre)

        # Status
        status_el = soup.select_one(".post-status .summary-content")
        if status_el:
            meta.status = status_el.get_text(strip=True)

        # Description
        desc_el = soup.select_one(".summary__content, .description-summary, .manga-excerpt")
        if desc_el:
            meta.description = desc_el.get_text(strip=True)[:2000]

        # Cover
        cover_el = soup.select_one(".summary_image img, .tab-thumb img")
        if cover_el:
            meta.cover_url = cover_el.get("data-src") or cover_el.get("src") or ""

        # Chapter count
        chapter_els = soup.select(".wp-manga-chapter a")
        meta.chapter_count = len(chapter_els)

        return meta

    def save(self, metadata: MangaMetadata, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(metadata.to_dict(), f, indent=2, ensure_ascii=False)
