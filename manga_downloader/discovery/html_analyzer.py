"""HTML structure analyzer for dynamic website discovery.

Analyzes manga site HTML to discover chapter links and image URLs
without hardcoded selectors. Uses multiple strategies to adapt to
website structure changes.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag


class HTMLAnalyzer:
    """Analyzes HTML content to discover site structure dynamically."""

    def __init__(self, html: str, base_url: str) -> None:
        """Initialize the analyzer.

        Args:
            html: Raw HTML content of the page.
            base_url: Base URL of the page for resolving relative URLs.
        """
        self.html = html
        self.base_url = base_url
        self.soup = BeautifulSoup(html, "lxml")
        self._parsed_base = urlparse(base_url)
        self.site_host = f"{self._parsed_base.scheme}://{self._parsed_base.netloc}"

    def find_chapter_links(self) -> list[dict[str, Any]]:
        """Discover chapter links from the page HTML.

        Uses multiple strategies to find chapter links:
            1. Look for links matching chapter patterns in href/text.
            2. Look for common container elements (ul, div with list-like classes).
            3. Look for structured data (JSON-LD, embedded data).
            4. Look for links sorted by their position in the DOM.

        Returns:
            List of dicts with keys: number, title, url, slug.
        """
        chapters: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        strategies = [
            self._strategy_chapter_link_patterns,
            self._strategy_common_containers,
            self._strategy_json_ld,
            self._strategy_embedded_data,
            self._strategy_all_links_fallback,
        ]

        for strategy in strategies:
            try:
                result = strategy()
                if result:
                    for ch in result:
                        url = ch.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            chapters.append(ch)
            except Exception:
                continue

        chapters = self._deduplicate_chapters(chapters)
        chapters.sort(key=lambda c: c.get("number", 0))

        return chapters

    def _resolve_url(self, url: str) -> str:
        """Resolve a potentially relative URL to absolute."""
        return urljoin(self.base_url, url)

    def _extract_number(self, text: str) -> float | None:
        """Extract a chapter number from text.

        Handles formats like: 'Chapter 1', 'Ch. 1', '1', '001', '1.5'.
        """
        if not text:
            return None

        text = text.strip()

        patterns = [
            r"[Cc]h(?:apter)?\.?\s*(\d+(?:\.\d+)?)",
            r"[Cc]h(?:apitre)?\.?\s*(\d+(?:\.\d+)?)",
            r"[Ee]p(?:isode)?\.?\s*(\d+(?:\.\d+)?)",
            r"#?\s*(\d+(?:\.\d+)?)",
            r"^(\d+(?:\.\d+)?)$",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    num_str = match.group(1)
                    return float(num_str)
                except ValueError:
                    continue

        return None

    def _extract_title(self, text: str, number: float | None) -> str:
        """Extract chapter title from text."""
        text = text.strip()
        if number is not None:
            prefix_patterns = [
                rf"[Cc]h(?:apter)?\.?\s*{re.escape(str(number))}\s*[:-]?\s*",
                rf"[Cc]h(?:apitre)?\.?\s*{re.escape(str(number))}\s*[:-]?\s*",
                rf"{re.escape(str(number))}\s*[:-]?\s*",
            ]
            for pat in prefix_patterns:
                text = re.sub(pat, "", text).strip()
        return text or ""

    def _strategy_chapter_link_patterns(self) -> list[dict[str, Any]]:
        """Strategy 1: Find links matching chapter URL patterns."""
        chapters: list[dict[str, Any]] = []
        links = self.soup.find_all("a", href=True)

        for link in links:
            href = str(link.get("href", ""))
            text = link.get_text(strip=True)

            number = self._extract_number(text) or self._extract_number(href)

            if number is not None:
                full_url = self._resolve_url(href)
                title = self._extract_title(text, number)
                slug = self._extract_slug(href)
                chapters.append({
                    "number": number,
                    "title": title,
                    "url": full_url,
                    "slug": slug,
                })

        return chapters

    def _strategy_common_containers(self) -> list[dict[str, Any]]:
        """Strategy 2: Look for common chapter list containers."""
        chapters: list[dict[str, Any]] = []
        list_items = self.soup.find_all("li")

        for li in list_items:
            link = li.find("a", href=True)
            if not link:
                continue
            href = str(link.get("href", ""))
            text = link.get_text(strip=True)
            if not text:
                text = li.get_text(strip=True)

            number = self._extract_number(text) or self._extract_number(href)
            if number is not None:
                full_url = self._resolve_url(href)
                title = self._extract_title(text, number)
                slug = self._extract_slug(href)
                chapters.append({
                    "number": number,
                    "title": title,
                    "url": full_url,
                    "slug": slug,
                })

        return chapters

    def _strategy_json_ld(self) -> list[dict[str, Any]]:
        """Strategy 3: Extract from JSON-LD structured data."""
        chapters: list[dict[str, Any]] = []
        scripts = self.soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                import json
                data = json.loads(script.string or "{}")
                if isinstance(data, dict):
                    self._extract_chapters_from_json(data, chapters)
                elif isinstance(data, list):
                    for item in data:
                        self._extract_chapters_from_json(item, chapters)
            except (json.JSONDecodeError, TypeError):
                continue
        return chapters

    def _extract_chapters_from_json(self, data: dict[str, Any], chapters: list[dict[str, Any]]) -> None:
        """Recursively search JSON-LD for chapter-like entries."""
        if "hasPart" in data:
            parts = data["hasPart"]
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict):
                        url = part.get("url", "")
                        name = part.get("name", "")
                        number = self._extract_number(name)
                        if url and number is not None:
                            chapters.append({
                                "number": number,
                                "title": self._extract_title(name, number),
                                "url": self._resolve_url(url),
                                "slug": self._extract_slug(url),
                            })
        for value in data.values():
            if isinstance(value, dict):
                self._extract_chapters_from_json(value, chapters)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._extract_chapters_from_json(item, chapters)

    def _strategy_embedded_data(self) -> list[dict[str, Any]]:
        """Strategy 4: Look for embedded JavaScript data (__NEXT_DATA__, window.__data, etc.)."""
        chapters: list[dict[str, Any]] = []
        scripts = self.soup.find_all("script")
        data_keys = [
            "__NEXT_DATA__",
            "__NUXT__",
            "window.__INITIAL_STATE__",
            "window.__DATA__",
            "window.__PRELOADED_STATE__",
            "window.chapters",
            "window.manga",
        ]

        for script in scripts:
            if not script.string:
                continue
            for key in data_keys:
                if key in script.string:
                    chapters.extend(self._extract_chapters_from_js(script.string, key))
                    break

        return chapters

    def _extract_chapters_from_js(self, js_text: str, key: str) -> list[dict[str, Any]]:
        """Attempt to extract chapter data from JavaScript embedded data."""
        chapters: list[dict[str, Any]] = []
        try:
            idx = js_text.index(key)
            after = js_text[idx + len(key):]
            json_str = after.split("</script>")[0]
            json_str = json_str.strip().lstrip("=").strip().rstrip(";")

            if json_str.startswith("{"):
                import json
                data = json.loads(json_str)
                self._extract_chapters_from_json(data, chapters)
        except Exception:
            pass
        return chapters

    def _strategy_all_links_fallback(self) -> list[dict[str, Any]]:
        """Strategy 5: Extract all links and try to identify chapters."""
        chapters: list[dict[str, Any]] = []
        links = self.soup.find_all("a", href=True)

        for link in links:
            href = str(link.get("href", ""))
            text = link.get_text(strip=True)

            number = self._extract_number(text) or self._extract_number(href)
            if number is not None:
                full_url = self._resolve_url(href)
                title = self._extract_title(text, number)
                slug = self._extract_slug(href)
                chapters.append({
                    "number": number,
                    "title": title,
                    "url": full_url,
                    "slug": slug,
                })

        return chapters

    def _extract_slug(self, url: str) -> str:
        """Extract a slug from a URL."""
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if path_parts:
            return path_parts[-1]
        return ""

    def _deduplicate_chapters(self, chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove duplicate chapters, keeping the one with more info."""
        by_number: dict[float, dict[str, Any]] = {}
        for ch in chapters:
            num = ch["number"]
            if num not in by_number:
                by_number[num] = ch
            else:
                existing = by_number[num]
                if len(ch.get("title", "")) > len(existing.get("title", "")):
                    by_number[num] = ch
        return list(by_number.values())

    def find_image_urls(self) -> list[dict[str, Any]]:
        """Discover image URLs from a chapter page.

        Uses multiple strategies:
            1. Look for img tags inside reading/viewer containers.
            2. Look for images loaded via JavaScript (data-src, etc.).
            3. Look for embedded JSON with image arrays.
            4. Look for all images as fallback.

        Returns:
            List of dicts with keys: url, order, width, height.
        """
        images: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        strategies = [
            self._strategy_reader_container_images,
            self._strategy_lazy_loaded_images,
            self._strategy_json_image_arrays,
            self._strategy_all_images_fallback,
        ]

        for strategy in strategies:
            try:
                result = strategy()
                for img in result:
                    url = img.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        images.append(img)
            except Exception:
                continue

        images = self._filter_valid_images(images)
        images.sort(key=lambda i: i.get("order", 0))

        return images

    def _is_valid_image_url(self, url: str) -> bool:
        """Check if a URL looks like a valid image URL."""
        if not url:
            return False
        url_lower = url.lower()
        image_extensions = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")
        image_keywords = ("/uploads/", "/manga/", "/chapters/", "/images/", "/img/", "/media/")

        if any(url_lower.endswith(ext) for ext in image_extensions):
            return True
        if any(kw in url_lower for kw in image_keywords):
            return True

        parsed = urlparse(url)
        path = parsed.path.lower()
        if any(path.endswith(ext) for ext in image_extensions):
            return True
        if any(kw in path for kw in image_keywords):
            return True

        return False

    def _strategy_reader_container_images(self) -> list[dict[str, Any]]:
        """Strategy 1: Find images inside common reader/viewer containers."""
        images: list[dict[str, Any]] = []
        reader_containers = self.soup.select(
            "#reader, #viewer, .reader, .viewer, .reading-content, "
            ".manga-reader, .chapter-content, .chapter-images, "
            ".page-break, .entry-content, .post-content"
        )

        order = 0
        if reader_containers:
            for container in reader_containers:
                for img in container.find_all("img"):
                    src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
                    if src and self._is_valid_image_url(src):
                        images.append({
                            "url": self._resolve_url(src),
                            "order": order,
                            "width": img.get("width"),
                            "height": img.get("height"),
                        })
                        order += 1

        return images

    def _strategy_lazy_loaded_images(self) -> list[dict[str, Any]]:
        """Strategy 2: Find images with lazy loading attributes."""
        images: list[dict[str, Any]] = []
        lazy_attrs = ["data-src", "data-lazy-src", "data-original", "data-srcset", "data-image"]

        order = 0
        for img in self.soup.find_all("img"):
            found = False
            for attr in lazy_attrs:
                src = img.get(attr)
                if src:
                    if isinstance(src, str):
                        src = src.split()[0]
                    if self._is_valid_image_url(src):
                        images.append({
                            "url": self._resolve_url(src),
                            "order": order,
                            "width": img.get("width"),
                            "height": img.get("height"),
                        })
                        order += 1
                        found = True
                        break
            if not found:
                src = img.get("src", "")
                if src and self._is_valid_image_url(src):
                    images.append({
                        "url": self._resolve_url(src),
                        "order": order,
                        "width": img.get("width"),
                        "height": img.get("height"),
                    })
                    order += 1

        return images

    def _strategy_json_image_arrays(self) -> list[dict[str, Any]]:
        """Strategy 3: Find embedded JSON with image arrays."""
        images: list[dict[str, Any]] = []
        scripts = self.soup.find_all("script")

        image_keys = [
            "images", "pages", "chapter_images", "img_urls",
            "pageImages", "media", "slides", "chapter_data",
        ]

        for script in scripts:
            if not script.string:
                continue
            try:
                import json

                json_matches = re.findall(r'\{[^{}]*\}', script.string)
                for match in json_matches:
                    try:
                        data = json.loads(match)
                        if isinstance(data, dict):
                            self._extract_images_from_json(data, images, image_keys)
                    except json.JSONDecodeError:
                        continue
            except Exception:
                continue

        return images

    def _extract_images_from_json(
        self, data: dict[str, Any], images: list[dict[str, Any]], image_keys: list[str]
    ) -> None:
        """Recursively search JSON for image arrays."""
        for key, value in data.items():
            if any(ik in key.lower() for ik in image_keys):
                if isinstance(value, list):
                    for i, item in enumerate(value):
                        if isinstance(item, str) and self._is_valid_image_url(item):
                            images.append({
                                "url": self._resolve_url(item),
                                "order": i,
                            })
                        elif isinstance(item, dict):
                            img_url = item.get("url") or item.get("src") or item.get("image") or ""
                            if img_url and self._is_valid_image_url(str(img_url)):
                                images.append({
                                    "url": self._resolve_url(str(img_url)),
                                    "order": i,
                                })
            if isinstance(value, dict):
                self._extract_images_from_json(value, images, image_keys)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._extract_images_from_json(item, images, image_keys)

    def _strategy_all_images_fallback(self) -> list[dict[str, Any]]:
        """Strategy 4: Extract all images from the page as a fallback."""
        images: list[dict[str, Any]] = []
        order = 0

        for img in self.soup.find_all("img"):
            src = img.get("src") or ""
            if src:
                resolved = self._resolve_url(src)
                if self._is_valid_image_url(resolved):
                    images.append({
                        "url": resolved,
                        "order": order,
                        "width": img.get("width"),
                        "height": img.get("height"),
                    })
                    order += 1

        return images

    def _filter_valid_images(self, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter out non-manga images (icons, logos, avatars, etc.).

        Uses whole-segment matching on the URL path to avoid false
        positives like 'ad' matching 'uploads'.
        """
        exclude_keywords = [
            "icon", "logo", "avatar", "banner", "thumbnail", "thumb",
            "favicon", "social", "share", "button", "bg", "background",
            "header", "footer", "advertisement",
        ]

        def _has_exclude_kw(segment: str) -> bool:
            """Check if a path segment contains an excluded keyword as a
            whole word (delimited by separators or boundaries)."""
            segment_lower = segment.lower()
            for kw in exclude_keywords:
                if kw in segment_lower:
                    idx = segment_lower.index(kw)
                    before_ok = idx == 0 or not segment_lower[idx - 1].isalnum()
                    after_ok = (idx + len(kw) == len(segment_lower) or
                                not segment_lower[idx + len(kw)].isalnum())
                    if before_ok and after_ok:
                        return True
            return False

        filtered: list[dict[str, Any]] = []
        for img in images:
            url = img.get("url", "")
            path = urlparse(url).path.lower()
            segments = [s for s in path.split("/") if s]
            basename = segments[-1] if segments else ""

            excluded = False
            for seg in segments:
                if _has_exclude_kw(seg):
                    excluded = True
                    break

            if not excluded:
                filtered.append(img)

        return filtered

    def find_manga_title(self) -> str:
        """Discover the manga title from the page.

        Tries: og:title, title tag, h1, canonical URL path.
        """
        og_title = self.soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = str(og_title["content"]).strip()
            title = re.sub(r"\s*[-–|]\s*Manga\s*Starz.*", "", title, flags=re.I).strip()
            title = re.sub(r"\s*[-–|]\s*مانجا\s*ستارز.*", "", title, flags=re.I).strip()
            if title:
                return title

        title_tag = self.soup.find("title")
        if title_tag and title_tag.string:
            title = title_tag.string.strip()
            title = re.sub(r"\s*[-–|]\s*Manga\s*Starz.*", "", title, flags=re.I).strip()
            if title:
                return title

        h1 = self.soup.find("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(strip=True)

        parsed = urlparse(self.base_url)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(path_parts) >= 2 and path_parts[0].lower() == "manga":
            return path_parts[1].replace("-", " ").title()

        if path_parts:
            return path_parts[-1].replace("-", " ").title()

        return "Unknown Manga"

    def find_manga_slug(self) -> str:
        """Discover the manga slug from the URL."""
        parsed = urlparse(self.base_url)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if path_parts:
            return path_parts[-1]
        return ""

    @staticmethod
    def get_selector_report() -> dict[str, str]:
        """Return a report of what strategies are available."""
        return {
            "chapter_strategies": [
                "1. Pattern-based link extraction",
                "2. Common container element search (li, ul, div)",
                "3. JSON-LD structured data",
                "4. Embedded JavaScript data",
                "5. All-links fallback",
            ],
            "image_strategies": [
                "1. Reader/viewer container images",
                "2. Lazy-loaded image attributes",
                "3. JSON embedded image arrays",
                "4. All-images fallback",
            ],
        }
