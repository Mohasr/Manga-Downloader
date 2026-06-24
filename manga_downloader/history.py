"""Download history — SQLite-based persistent record of all downloads."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

DB_PATH = "manga_downloader/cache/history.db"


@dataclass
class HistoryEntry:
    id: int
    manga_title: str
    manga_url: str
    chapter: str
    chapter_number: float
    status: str  # completed, failed
    image_count: int
    file_path: str
    file_size: int
    duration_s: float
    timestamp: float

    def to_dict(self) -> dict:
        return {
            "id": self.id, "manga_title": self.manga_title,
            "manga_url": self.manga_url, "chapter": self.chapter,
            "chapter_number": self.chapter_number, "status": self.status,
            "image_count": self.image_count, "file_path": self.file_path,
            "file_size": self.file_size, "duration_s": self.duration_s,
            "timestamp": self.timestamp,
        }


class HistoryManager:
    """Persistent download history using SQLite."""

    def __init__(self, db_path: str | Path = DB_PATH):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS downloads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manga_title TEXT NOT NULL,
                manga_url TEXT NOT NULL,
                chapter TEXT NOT NULL,
                chapter_number REAL DEFAULT 0,
                status TEXT DEFAULT 'completed',
                image_count INTEGER DEFAULT 0,
                file_path TEXT DEFAULT '',
                file_size INTEGER DEFAULT 0,
                duration_s REAL DEFAULT 0,
                timestamp REAL NOT NULL
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_manga ON downloads(manga_title)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON downloads(timestamp)")
        self._conn.commit()

    def record(self, manga_title: str, manga_url: str, chapter: str,
               chapter_number: float, status: str, image_count: int,
               file_path: str, file_size: int, duration_s: float) -> int:
        cursor = self._conn.execute(
            """INSERT INTO downloads (manga_title, manga_url, chapter, chapter_number,
               status, image_count, file_path, file_size, duration_s, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (manga_title, manga_url, chapter, chapter_number, status,
             image_count, file_path, file_size, duration_s, time.time()),
        )
        self._conn.commit()
        return cursor.lastrowid

    def query(self, manga_title: str | None = None, limit: int = 50,
              status: str | None = None) -> list[HistoryEntry]:
        sql = "SELECT * FROM downloads WHERE 1=1"
        params: list = []
        if manga_title:
            sql += " AND manga_title LIKE ?"
            params.append(f"%{manga_title}%")
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [HistoryEntry(
            id=r["id"], manga_title=r["manga_title"], manga_url=r["manga_url"],
            chapter=r["chapter"], chapter_number=r["chapter_number"], status=r["status"],
            image_count=r["image_count"], file_path=r["file_path"],
            file_size=r["file_size"], duration_s=r["duration_s"], timestamp=r["timestamp"],
        ) for r in rows]

    def stats(self) -> dict:
        rows = self._conn.execute("""
            SELECT COUNT(*) as total, SUM(image_count) as images,
                   SUM(file_size) as bytes, SUM(duration_s) as seconds,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as completed,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed
            FROM downloads
        """).fetchone()
        return {
            "total_downloads": rows["total"] or 0,
            "total_images": rows["images"] or 0,
            "total_bytes": rows["bytes"] or 0,
            "total_seconds": rows["seconds"] or 0,
            "completed": rows["completed"] or 0,
            "failed": rows["failed"] or 0,
        }

    def close(self) -> None:
        self._conn.close()
