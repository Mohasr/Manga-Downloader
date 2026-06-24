"""Network request monitoring and analysis for dynamic discovery.

Monitors Playwright page network traffic to discover:
- Image CDN URLs
- API endpoints
- Required headers
- Referer patterns
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapturedRequest:
    """Represents a captured network request."""

    url: str
    method: str
    resource_type: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    response_headers: dict[str, str] = field(default_factory=dict)
    body: str = ""


class NetworkAnalyzer:
    """Captures and analyzes network traffic for discovery purposes."""

    IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
    IMAGE_MIME_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp", "image/avif")
    API_PATH_PATTERNS = ("/api/", "/ajax/", "/graphql", "/wp-json/", "/v1/", "/v2/", "/rest/")

    def __init__(self) -> None:
        self.requests: list[CapturedRequest] = []
        self.image_urls: list[str] = []
        self.api_urls: list[str] = []
        self.xhr_requests: list[CapturedRequest] = []
        self.fetch_requests: list[CapturedRequest] = []

    def reset(self) -> None:
        """Clear all captured data."""
        self.requests.clear()
        self.image_urls.clear()
        self.api_urls.clear()
        self.xhr_requests.clear()
        self.fetch_requests.clear()

    def on_request(self, request: Any) -> None:
        """Handle a network request event from Playwright.

        Args:
            request: Playwright Request object.
        """
        try:
            url = request.url
            resource_type = request.resource_type or ""
            method = request.method
            headers = dict(request.headers)
        except Exception:
            return

        captured = CapturedRequest(
            url=url,
            method=method,
            resource_type=resource_type,
            status=0,
            headers=headers,
        )
        self.requests.append(captured)

        if resource_type in ("image", "media") or self._is_image_url(url):
            if url not in self.image_urls:
                self.image_urls.append(url)

        if resource_type == "xhr":
            self.xhr_requests.append(captured)

        if resource_type == "fetch":
            self.fetch_requests.append(captured)

        if self._is_api_url(url):
            if url not in self.api_urls:
                self.api_urls.append(url)

    def on_response(self, response: Any) -> None:
        """Handle a network response event from Playwright.

        Args:
            response: Playwright Response object.
        """
        try:
            url = response.url
            status = response.status
            resp_headers = dict(response.headers)
        except Exception:
            return

        for req in self.requests:
            if req.url == url:
                req.status = status
                req.response_headers = resp_headers
                break

    def analyze_image_patterns(self) -> dict[str, Any]:
        """Analyze captured image URLs to discover patterns.

        Returns:
            Dict containing:
                - cdn_hosts: List of identified CDN hostnames.
                - url_patterns: List of common URL patterns.
                - file_extensions: Set of image file extensions used.
                - numeric_patterns: Detected numbering patterns.
        """
        if not self.image_urls:
            return {
                "cdn_hosts": [],
                "url_patterns": [],
                "file_extensions": set(),
                "numeric_patterns": [],
            }

        from urllib.parse import urlparse

        cdn_hosts: set[str] = set()
        for url in self.image_urls:
            parsed = urlparse(url)
            cdn_hosts.add(parsed.netloc)

        extensions: set[str] = set()
        for url in self.image_urls:
            for ext in self.IMAGE_EXTENSIONS:
                if url.lower().endswith(ext):
                    extensions.add(ext)
                    break

        numeric_patterns: list[dict[str, Any]] = []
        for url in self.image_urls[:10]:
            numbers = re.findall(r"(\d+)", url)
            if numbers:
                numeric_patterns.append({
                    "url": url,
                    "extracted_numbers": numbers,
                })

        url_patterns = self._derive_url_patterns(self.image_urls)

        return {
            "cdn_hosts": sorted(cdn_hosts),
            "url_patterns": url_patterns,
            "file_extensions": extensions,
            "numeric_patterns": numeric_patterns,
        }

    def analyze_api_patterns(self) -> dict[str, Any]:
        """Analyze captured API requests to discover endpoints.

        Returns:
            Dict with API endpoint analysis.
        """
        api_requests = self.xhr_requests + self.fetch_requests + [
            r for r in self.requests if self._is_api_url(r.url)
        ]

        endpoints: list[dict[str, Any]] = []
        for req in api_requests:
            endpoints.append({
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "status": req.status,
            })

        return {
            "api_endpoints": endpoints,
            "count": len(endpoints),
        }

    def analyze_headers(self) -> dict[str, Any]:
        """Analyze request/response headers for patterns.

        Returns:
            Dict with header analysis.
        """
        common_request_headers: dict[str, set[str]] = {}
        common_response_headers: dict[str, set[str]] = {}

        for req in self.requests:
            for key, value in req.headers.items():
                if key not in common_request_headers:
                    common_request_headers[key] = set()
                common_request_headers[key].add(value)

        for req in self.requests:
            for key, value in req.response_headers.items():
                if key not in common_response_headers:
                    common_response_headers[key] = set()
                common_response_headers[key].add(value)

        req_headers: dict[str, str] = {}
        for key, values in common_request_headers.items():
            if len(values) == 1:
                req_headers[key] = next(iter(values))
            else:
                req_headers[key] = list(values)  # type: ignore[assignment]

        return {
            "request_headers": req_headers,
            "response_headers": {k: list(v) for k, v in common_response_headers.items()},
        }

    def get_referer_requirements(self) -> dict[str, str]:
        """Determine referer requirements for image requests."""
        image_referers: dict[str, set[str]] = {}
        for req in self.requests:
            if req.resource_type == "image" or self._is_image_url(req.url):
                referer = req.headers.get("Referer", req.headers.get("referer", ""))
                if referer:
                    cdn = self._get_host(req.url)
                    if cdn not in image_referers:
                        image_referers[cdn] = set()
                    image_referers[cdn].add(referer)

        result: dict[str, str] = {}
        for cdn, referers in image_referers.items():
            if len(referers) == 1:
                result[cdn] = next(iter(referers))
            else:
                result[cdn] = "required"

        return result

    def get_discovery_report(self) -> dict[str, Any]:
        """Generate a comprehensive discovery report."""
        return {
            "total_requests": len(self.requests),
            "image_urls_count": len(self.image_urls),
            "api_urls_count": len(self.api_urls),
            "image_analysis": self.analyze_image_patterns(),
            "api_analysis": self.analyze_api_patterns(),
            "header_analysis": self.analyze_headers(),
            "referer_requirements": self.get_referer_requirements(),
        }

    @staticmethod
    def _is_image_url(url: str) -> bool:
        """Check if a URL points to an image based on extension or path."""
        url_lower = url.lower()
        for ext in NetworkAnalyzer.IMAGE_EXTENSIONS:
            if url_lower.endswith(ext):
                return True
        image_paths = ("/uploads/", "/manga/", "/chapters/", "/images/", "/img/")
        for path in image_paths:
            if path in url_lower:
                return True
        return False

    @staticmethod
    def _is_api_url(url: str) -> bool:
        """Check if a URL is likely an API endpoint."""
        url_lower = url.lower()
        for pattern in NetworkAnalyzer.API_PATH_PATTERNS:
            if pattern in url_lower:
                return True
        if re.search(r"\.(json|xml|graphql)(\?|$)", url_lower):
            return True
        return False

    @staticmethod
    def _get_host(url: str) -> str:
        """Extract hostname from URL."""
        from urllib.parse import urlparse
        return urlparse(url).netloc

    @staticmethod
    def _derive_url_patterns(urls: list[str]) -> list[str]:
        """Derive common URL patterns from a list of URLs."""
        if not urls:
            return []

        from urllib.parse import urlparse

        parsed_urls = [urlparse(u) for u in urls]
        schemes = {p.scheme for p in parsed_urls}

        patterns: list[str] = []
        for pu in parsed_urls[:5]:
            path = pu.path
            digitized = re.sub(r"\d+", "{n}", path)
            if digitized not in patterns:
                patterns.append(digitized)

        return patterns

    async def attach_to_page(self, page: Any) -> None:
        """Attach network listeners to a Playwright page.

        Args:
            page: Playwright Page object.
        """
        self.reset()

        def _on_request(request: Any) -> None:
            self.on_request(request)

        def _on_response(response: Any) -> None:
            self.on_response(response)

        page.on("request", _on_request)
        page.on("response", _on_response)
