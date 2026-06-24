"""Download queue manager — sequential execution with pause/resume/cancel.

Stores queue state to disk for persistence across restarts.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

STATE_FILE = "manga_downloader/cache/queue_state.json"


class QueueItemStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


@dataclass
class QueueItem:
    url: str
    status: QueueItemStatus = QueueItemStatus.PENDING
    chapters: list[float] = field(default_factory=list)
    manga_title: str = ""
    added_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    result: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url, "status": self.status.value,
            "chapters": self.chapters, "manga_title": self.manga_title,
            "added_at": self.added_at, "completed_at": self.completed_at,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QueueItem:
        return cls(
            url=d["url"], status=QueueItemStatus(d.get("status", "pending")),
            chapters=d.get("chapters", []), manga_title=d.get("manga_title", ""),
            added_at=d.get("added_at", time.time()),
            completed_at=d.get("completed_at"),
            result=d.get("result", ""),
        )


class DownloadQueue:
    """Sequential manga download queue with state persistence."""

    def __init__(self, state_path: str | Path = STATE_FILE):
        self._path = Path(state_path)
        self._items: list[QueueItem] = []
        self._paused = False
        self._active_index: int | None = None
        self._lock = asyncio.Lock()
        self.load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def items(self) -> list[QueueItem]:
        return list(self._items)

    @property
    def size(self) -> int:
        return len(self._items)

    @property
    def pending(self) -> int:
        return sum(1 for i in self._items if i.status == QueueItemStatus.PENDING)

    @property
    def is_paused(self) -> bool:
        return self._paused

    def add(self, url: str, chapters: list[float] | None = None) -> QueueItem:
        """Add a manga URL to the queue."""
        item = QueueItem(url=url, chapters=chapters or [])
        self._items.append(item)
        self.save()
        return item

    def remove(self, index: int) -> QueueItem | None:
        """Remove item at index."""
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.save()
            return item
        return None

    def cancel(self, index: int) -> bool:
        """Cancel a pending or paused item."""
        if 0 <= index < len(self._items):
            item = self._items[index]
            if item.status in (QueueItemStatus.PENDING, QueueItemStatus.PAUSED):
                item.status = QueueItemStatus.CANCELLED
                item.completed_at = time.time()
                self.save()
                return True
        return False

    def pause(self) -> None:
        """Pause queue execution."""
        self._paused = True
        self.save()

    def resume(self) -> None:
        """Resume queue execution."""
        self._paused = False
        self.save()

    def mark_completed(self, index: int, result: str = "") -> None:
        if 0 <= index < len(self._items):
            self._items[index].status = QueueItemStatus.COMPLETED
            self._items[index].completed_at = time.time()
            self._items[index].result = result
            self.save()

    def mark_failed(self, index: int, error: str = "") -> None:
        if 0 <= index < len(self._items):
            self._items[index].status = QueueItemStatus.FAILED
            self._items[index].completed_at = time.time()
            self._items[index].result = error
            self.save()

    def set_title(self, index: int, title: str) -> None:
        if 0 <= index < len(self._items):
            self._items[index].manga_title = title
            self.save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "items": [i.to_dict() for i in self._items],
            "paused": self._paused,
        }
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
            self._items = [QueueItem.from_dict(i) for i in data.get("items", [])]
            self._paused = data.get("paused", False)
            return True
        except (json.JSONDecodeError, OSError):
            return False

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def format_summary(self) -> str:
        lines = ["Download Queue:"]
        for i, item in enumerate(self._items):
            marker = ">" if i == self._active_index else " "
            status = item.status.value.upper()
            title = item.manga_title or item.url.rsplit("/", 1)[-1] if "/" in item.url else item.url
            ch_count = f"({len(item.chapters)} ch)" if item.chapters else ""
            lines.append(f"  [{marker}] {status:<12} {title[:40]:<42} {ch_count}")
        if not self._items:
            lines.append("  (empty)")
        if self._paused:
            lines.append("  [PAUSED]")
        return "\n".join(lines)
