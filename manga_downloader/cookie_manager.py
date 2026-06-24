"""Cookie manager for requests-centric downloader.

Loads cookies.json exported by setup_cookies.py (one-time Playwright session).
Creates a requests.Session with browser-matching headers and CF clearance cookies.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

DEFAULT_COOKIES_PATH = "manga_downloader/cache/cookies.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class CookieManager:
    """Manages CF clearance cookies for requests.Session."""

    def __init__(
        self,
        cookies_path: str | Path = DEFAULT_COOKIES_PATH,
        user_agent: str | None = None,
    ) -> None:
        self._cookies_path = Path(cookies_path)
        self._user_agent = user_agent or UA
        self._data: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._cookies_path

    @property
    def loaded(self) -> bool:
        return bool(self._data)

    @property
    def captured_at(self) -> float | None:
        return self._data.get("captured_at")

    @property
    def captured_at_display(self) -> str:
        ts = self.captured_at
        if ts is None:
            return "unknown"
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def sites(self) -> list[str]:
        return list(self._data.get("sites", {}).keys())

    def load(self) -> bool:
        """Load cookies from disk.  Returns True on success."""
        path = self._cookies_path
        if not path.exists():
            return False
        try:
            with open(path, encoding="utf-8") as f:
                self._data = json.load(f)
            return True
        except (json.JSONDecodeError, OSError):
            self._data = {}
            return False

    def save(self) -> None:
        """Persist current cookie data to disk."""
        path = self._cookies_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def list_cookies(self, site_name: str) -> list[dict]:
        """Return raw cookie list for a site."""
        return self._data.get("sites", {}).get(site_name, [])

    def has_valid_cookies(self, site_name: str) -> bool:
        """Check if site has cf_clearance cookie."""
        for c in self.list_cookies(site_name):
            if c.get("name") == "cf_clearance":
                expires = c.get("expires", -1)
                if expires == -1 or expires > time.time():
                    return True
        return False

    def create_session(self, site_name: str) -> requests.Session:
        """Create a requests.Session with browser headers and site cookies."""
        session = self._create_base_session()

        for c in self.list_cookies(site_name):
            self._add_cookie(session, c)

        return session

    def create_session_all_sites(self) -> requests.Session:
        """Create a requests.Session with cookies from ALL known sites."""
        session = self._create_base_session()
        for site_name in self._data.get("sites", {}):
            for c in self._data["sites"][site_name]:
                self._add_cookie(session, c)
        return session

    def _create_base_session(self) -> requests.Session:
        session = requests.Session()
        ua = self._data.get("user_agent", self._user_agent)
        session.headers.update({
            "User-Agent": ua,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })
        return session

    @staticmethod
    def _add_cookie(session: requests.Session, c: dict) -> None:
        try:
            expires = c.get("expires")
            if expires == -1:
                expires = None
            cookie_obj = requests.cookies.create_cookie(
                name=c["name"],
                value=c.get("value", c.get("value_full", "")),
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
                expires=expires,
                secure=c.get("secure", False),
            )
            session.cookies.set_cookie(cookie_obj)
        except Exception:
            pass

    def add_site_cookies(self, site_name: str, cookies: list[dict]) -> None:
        """Add or replace cookies for a site."""
        if "sites" not in self._data:
            self._data["sites"] = {}
        self._data["sites"][site_name] = cookies
        self._data["captured_at"] = time.time()
        self._data["captured_at_human"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def cookie_diagnostics(self, site_name: str) -> str:
        """Human-readable cookie summary."""
        lines = []
        for c in self.list_cookies(site_name):
            name = c.get("name", "?")
            expires = c.get("expires", -1)
            if expires == -1 or expires == 0:
                exp_str = "session"
            else:
                remaining = (expires - time.time()) / 3600
                exp_str = f"{remaining:.0f}h ({datetime.fromtimestamp(expires).strftime('%Y-%m-%d')})"
            lines.append(f"  {name:<30} domain={c.get('domain', '?'):<25} expires={exp_str}")
        return "\n".join(lines)
