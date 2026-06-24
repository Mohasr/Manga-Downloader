"""Abstract browser manager — defines the interface for browser backends.

Subclasses provide browser launch and lifecycle management.
Supported backends: Playwright Chrome, Kameleo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BrowserManager(ABC):
    """Abstract browser lifecycle manager.

    All browser backends implement this interface. CloudflareHandler
    calls start() to get a context and page, then uses them for
    navigation, scraping, and CF handling.
    """

    def __init__(self, profile_dir: str = "browser_profile", debug: bool = False) -> None:
        self.profile_dir = profile_dir
        self.debug = debug
        self._playwright: Any = None
        self._context: Any = None
        self._page: Any = None
        self._browser_type: str = ""
        self._browser_version: str = ""
        self._browser_ua: str = ""

    @abstractmethod
    async def start(self, headless: bool = False, mode: str = "unknown") -> tuple[Any, Any]:
        """Launch/attach browser and return (context, page).

        Args:
            headless: Whether to run headless.
            mode: Diagnostic label (warmup, download, profile-info, etc.)

        Returns:
            Tuple of (BrowserContext, Page) from Playwright.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close browser and clean up resources."""
        ...

    @abstractmethod
    async def get_headers(self) -> dict[str, str]:
        """Get HTTP headers for aiohttp image downloading."""
        ...

    @property
    def context(self) -> Any:
        return self._context

    @property
    def page(self) -> Any:
        return self._page

    @property
    def browser_type(self) -> str:
        return self._browser_type

    @property
    def browser_version(self) -> str:
        return self._browser_version

    @property
    def browser_ua(self) -> str:
        return self._browser_ua

    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable backend name for diagnostics."""
        ...

    @abstractmethod
    def collect_fingerprint(self) -> dict[str, Any]:
        """Collect browser fingerprint values. Synchronous snapshot."""
        ...
