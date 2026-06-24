"""Session manager for aiohttp HTTP sessions.

Manages connection pooling, cookie persistence, and header management
for efficient image downloading.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from aiohttp import ClientTimeout, CookieJar, TCPConnector


class SessionManager:
    """Manages aiohttp client sessions for image downloading."""

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        timeout: int = 60,
        max_retries: int = 5,
        retry_delay: float = 2.0,
        cookies: dict[str, str] | None = None,
        max_connections: int = 50,
        max_connections_per_host: int = 10,
    ) -> None:
        """Initialize the session manager.

        Args:
            headers: Default headers for all requests.
            timeout: Request timeout in seconds.
            max_retries: Maximum retry attempts.
            retry_delay: Delay between retries in seconds.
            cookies: Cookie dict for the session.
            max_connections: Maximum total connections.
            max_connections_per_host: Maximum connections per host.
        """
        self._headers = headers or {}
        self._timeout = ClientTimeout(total=timeout)
        self._max_retries = max_retries
        self._retry_delay = retry_delay
        self._session: aiohttp.ClientSession | None = None
        self._connector = TCPConnector(
            limit=max_connections,
            limit_per_host=max_connections_per_host,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        self._cookie_jar = CookieJar(unsafe=True)
        if cookies:
            self._cookie_jar.update_cookies(cookies)
        self._referer_overrides: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp ClientSession.

        Returns:
            aiohttp ClientSession.
        """
        if self._session is None or self._session.closed:
            async with self._lock:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession(
                        headers=self._headers,
                        timeout=self._timeout,
                        connector=self._connector,
                        cookie_jar=self._cookie_jar,
                    )
        return self._session

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        referer: str = "",
    ) -> aiohttp.ClientResponse:
        """Perform a GET request with retry support.

        Args:
            url: Request URL.
            headers: Additional headers.
            referer: Referer URL.

        Returns:
            aiohttp ClientResponse.
        """
        session = await self.get_session()
        merged_headers = {**self._headers}
        if headers:
            merged_headers.update(headers)
        if referer:
            merged_headers["Referer"] = referer

        if not merged_headers.get("Referer"):
            for host, ref in self._referer_overrides.items():
                from urllib.parse import urlparse
                if urlparse(url).netloc == host:
                    merged_headers["Referer"] = ref
                    break

        last_exception: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await session.get(url, headers=merged_headers)
                if response.status < 500:
                    return response
                response.close()
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_delay * (2 ** attempt))
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exception = e
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_delay * (2 ** attempt))

        if last_exception:
            raise last_exception
        raise RuntimeError(f"Failed to fetch {url} after {self._max_retries} retries")

    async def download_to_file(
        self,
        url: str,
        filepath: str,
        headers: dict[str, str] | None = None,
        referer: str = "",
        chunk_size: int = 8192,
    ) -> bool:
        """Download a file to disk.

        Args:
            url: File URL.
            filepath: Destination path.
            headers: Additional headers.
            referer: Referer URL.
            chunk_size: Download chunk size.

        Returns:
            True if download succeeded, False otherwise.
        """
        try:
            response = await self.get(url, headers=headers, referer=referer)
            if response.status != 200:
                response.close()
                return False

            from pathlib import Path
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)

            with open(filepath, "wb") as f:
                async for chunk in response.content.iter_chunked(chunk_size):
                    f.write(chunk)

            response.close()
            return True

        except Exception:
            if 'response' in locals():
                try:
                    response.close()
                except Exception:
                    pass
            return False

    async def read_bytes(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        referer: str = "",
    ) -> bytes | None:
        """Download file contents as bytes.

        Args:
            url: File URL.
            headers: Additional headers.
            referer: Referer URL.

        Returns:
            File contents as bytes, or None on failure.
        """
        try:
            response = await self.get(url, headers=headers, referer=referer)
            if response.status != 200:
                response.close()
                return None
            data = await response.read()
            response.close()
            return data
        except Exception:
            return None

    def set_referer_overrides(self, overrides: dict[str, str]) -> None:
        """Set referer overrides for specific hosts.

        Args:
            overrides: Dict mapping hostnames to referer URLs.
        """
        self._referer_overrides.update(overrides)

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
        await self._connector.close()
