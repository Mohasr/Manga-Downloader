"""Followed manga manager — tracks favorites with latest chapter detection."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

STATE_FILE = "manga_downloader/cache/followed.json"


@dataclass
class FollowedManga:
    title: str
    url: str
    site: str = ""
    latest_known_chapter: float = 0
    last_checked: float = field(default_factory=time.time)
    cover_url: str = ""

    def to_dict(self) -> dict:
        return {
            "title": self.title, "url": self.url, "site": self.site,
            "latest_known_chapter": self.latest_known_chapter,
            "last_checked": self.last_checked, "cover_url": self.cover_url,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FollowedManga:
        return cls(
            title=d["title"], url=d["url"], site=d.get("site", ""),
            latest_known_chapter=d.get("latest_known_chapter", 0),
            last_checked=d.get("last_checked", time.time()),
            cover_url=d.get("cover_url", ""),
        )


class FollowManager:
    """Manages followed/favorite manga list."""

    def __init__(self, state_path: str | Path = STATE_FILE):
        self._path = Path(state_path)
        self._items: list[FollowedManga] = []
        self.load()

    @property
    def items(self) -> list[FollowedManga]:
        return list(self._items)

    @property
    def count(self) -> int:
        return len(self._items)

    def add(self, title: str, url: str, site: str = "", cover_url: str = "") -> FollowedManga:
        existing = self.find_by_url(url)
        if existing:
            return existing
        item = FollowedManga(title=title, url=url, site=site, cover_url=cover_url)
        self._items.append(item)
        self.save()
        return item

    def remove(self, url: str) -> bool:
        item = self.find_by_url(url)
        if item:
            self._items.remove(item)
            self.save()
            return True
        return False

    def find_by_url(self, url: str) -> FollowedManga | None:
        for item in self._items:
            if item.url.rstrip("/") == url.rstrip("/"):
                return item
        return None

    def find_by_title(self, title: str) -> FollowedManga | None:
        for item in self._items:
            if item.title.lower() == title.lower():
                return item
        return None

    def update_latest(self, url: str, chapter_num: float) -> None:
        item = self.find_by_url(url)
        if item:
            item.latest_known_chapter = chapter_num
            item.last_checked = time.time()
            self.save()

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"items": [i.to_dict() for i in self._items]}
        try:
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            tmp.replace(self._path)
        except PermissionError:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

    def load(self) -> bool:
        if not self._path.exists():
            return False
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            self._items = [FollowedManga.from_dict(i) for i in data.get("items", [])]
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def to_list(self) -> list[dict]:
        return [i.to_dict() for i in self._items]
