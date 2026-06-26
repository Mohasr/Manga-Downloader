"""Search system — searches supported Madara sites via Ajax API.

Uses wp-manga-search-manga Ajax action (no browser needed for search).
"""

from __future__ import annotations

import requests
from dataclasses import dataclass
from typing import Any


@dataclass
class SearchResult:
    title: str
    url: str
    site: str = ""
    cover_url: str = ""
    latest_chapter: str = ""


SITES: dict[str, dict[str, Any]] = {
    "manga-starz.net": {
        "name": "Manga Starz",
        "url": "https://manga-starz.net",
    },
    "lek-manga.net": {
        "name": "Lek Manga",
        "url": "https://lek-manga.net",
    },
    "rocksmanga.com": {
        "name": "Rocks Manga",
        "url": "https://rocksmanga.com",
    },
    "3asq.org": {
        "name": "3asq",
        "url": "https://3asq.org",
    },
}

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/149.0.0.0 Safari/537.36"


class SearchManager:
    """Searches Madara sites for manga by title."""

    def __init__(self, sites: dict[str, dict[str, Any]] | None = None):
        self._sites = sites or SITES

    def search(self, query: str, site_override: str | None = None) -> list[SearchResult]:
        """Search for manga across configured sites.

        If query looks like a URL, return it as a single result.
        """
        # URL detection
        if query.startswith("http://") or query.startswith("https://"):
            return [SearchResult(title="(direct URL)", url=query, site="URL")]

        results: list[SearchResult] = []
        sites_to_search = (
            {site_override: self._sites[site_override]}
            if site_override and site_override in self._sites
            else self._sites
        )

        for domain, cfg in sites_to_search.items():
            try:
                resp = requests.post(
                    f"{cfg['url']}/wp-admin/admin-ajax.php",
                    data={"action": "wp-manga-search-manga", "title": query},
                    headers={
                        "User-Agent": UA,
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": cfg["url"] + "/",
                    },
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("data", []):
                        title = (item.get("title") or "").strip()
                        url = (item.get("url") or "").strip()
                        if not title or not url:
                            continue
                        results.append(SearchResult(
                            title=title, url=url, site=cfg["name"],
                        ))
            except Exception:
                continue

        # Fuzzy match: promote exact title matches to top
        query_lower = query.lower().strip()
        results.sort(key=lambda r: (
            0 if r.title.lower() == query_lower else 1,
            r.title.lower(),
        ))

        return results

    def search_all(self, query: str) -> dict[str, list[SearchResult]]:
        """Search all sites, return grouped results."""
        grouped: dict[str, list[SearchResult]] = {}
        for domain, cfg in self._sites.items():
            r = self.search(query, site_override=domain)
            if r:
                grouped[cfg["name"]] = r
        return grouped
