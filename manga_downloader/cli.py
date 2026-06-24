"""Command-line interface for Manga Downloader.

Features:
- Auto-detects provider from URL domain
- Smart URL detection (manga page vs direct chapter)
- Interactive chapter selection + format selection
- Live progress tracking + download summary
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import __version__
from .config import AppConfig
from .models import Chapter, OutputFormat
from .providers.base_provider import BaseMangaProvider
from .providers.browser_madara import BrowserMadaraProvider
from .providers.madara_provider import MadaraProvider
from .providers.mangastarz_provider import MangaStarzProvider
from .providers.lekmanga_provider import LekMangaProvider
from .scraper.cloudflare import CloudflareDetectedError, DiscoveryValidationError
from .utils.logger import (
    chapter_info,
    console,
    display_chapter_list,
    download_summary,
    error,
    format_menu,
    info,
    manga_info,
    selection_menu,
    startup_banner,
    success,
    warning,
)

_PROVIDER_REGISTRY: list[type[BaseMangaProvider]] = [
    BrowserMadaraProvider,  # Playwright + CDP (production)
    MadaraProvider,         # requests-only (legacy, non-CF sites)
    MangaStarzProvider,     # Playwright-based (legacy)
    LekMangaProvider,       # Playwright-based (legacy)
]


class MangaDownloaderCLI:
    """Interactive CLI for the Manga Downloader."""

    def __init__(self, config: AppConfig | None = None, debug: bool = False) -> None:
        self.config: AppConfig = config or AppConfig.get_instance()
        self.provider: BaseMangaProvider | None = None
        self._start_time: float = 0.0
        self.debug = debug

    async def run(self, url: str | None = None) -> None:
        from .menu import MenuSystem
        menu = MenuSystem(__version__)

        # If URL provided directly, skip menu
        if url:
            await self._download_flow(url)
            return

        # Main menu loop
        while True:
            choice = menu.main_menu()
            if choice is None or choice == "exit":
                console.print()
                info("Goodbye!")
                return

            if choice == "download":
                user_input, input_type = menu.download_screen()
                if user_input:
                    if input_type == "input":
                        # Text query — use paginated search, then download
                        result, _ = menu.search_screen_with_query(user_input)
                        if result:
                            await self._download_flow(result)
                    else:
                        await self._download_flow(user_input)

            elif choice == "search":
                result, _ = menu.search_screen()
                if result:
                    await self._download_flow(result)

            elif choice == "followed":
                result = menu.followed_screen()
                if result and result not in ("back", "check_updates"):
                    await self._download_flow(result)
                elif result == "check_updates":
                    await menu.update_checker_screen()

            elif choice == "updates":
                await menu.update_checker_screen()

            elif choice == "history":
                menu.history_screen()

            elif choice == "queue":
                menu.queue_screen()

            elif choice == "settings":
                menu.settings_screen(self.config)

    async def _download_flow(self, raw_input: str) -> None:
        """Handle a URL or search term — the existing download workflow."""
        provider_cls = self._find_provider(raw_input)
        if provider_cls is None:
            searched_url = await self._search_and_select(raw_input)
            if searched_url is None:
                return
            provider_cls = self._find_provider(searched_url)
            if provider_cls is None:
                warning("Could not determine which site this URL belongs to.")
                return
            raw_input = searched_url

        url_type = self._detect_url_type(raw_input)

        try:
            self._start_time = time.time()
            await self._run_workflow(raw_input, url_type, provider_cls)
        except CloudflareDetectedError as e:
            console.print()
            error(f"Cloudflare challenge not bypassed: {e}")
        except DiscoveryValidationError as e:
            console.print()
            error(f"Discovery validation failed: {e}")
        except KeyboardInterrupt:
            console.print()
            warning("Interrupted by user")
        except Exception as e:
            console.print()
            error(f"Fatal error: {e}")
            if self.debug:
                import traceback
                console.print(traceback.format_exc())
        finally:
            if self.provider:
                await self.provider.cleanup()
                self._show_summary()
                self._record_download_history()

    async def profile_info(self) -> None:
        """Display comprehensive browser profile diagnostics."""
        from .browser.playwright_manager import PlaywrightManager
        from .scraper.cloudflare import CloudflareHandler

        startup_banner(__version__)
        console.print("  [bold]Browser Profile Diagnostics[/bold]")
        console.print()

        profile_path = self.config.profile_path
        info(f"Profile dir:    {profile_path}")

        if not profile_path.exists():
            warning("Profile directory does not exist yet.")
            info("It will be created on first launch.")
            return

        def _fmt(n: int) -> str:
            for u in ("B", "KB", "MB", "GB"):
                if n < 1000:
                    return f"{n:.1f} {u}"
                n /= 1000
            return f"{n:.1f} TB"

        browser = PlaywrightManager(profile_dir=str(profile_path), debug=False)
        info(f"Browser:     {browser.provider_name()}")
        ctx, page = await browser.start(headless=False, mode="profile-info")

        cf = CloudflareHandler(browser, debug=False)
        cf._context = ctx
        cf._page = page

        cf = CloudflareHandler(browser, debug=False)
        cf._context = ctx
        cf._page = page
        stats = await cf.collect_profile_stats()

        console.print()
        info(f"Profile size:   {_fmt(stats['profile_size_bytes'])}")
        info(f"Files:          {stats['file_count']}")
        info(f"Cookies:        {stats['cookie_count']}")
        info(f"Domains:        {len(stats['domains'])}")
        if stats["domains"]:
            for d in stats["domains"]:
                info(f"  [dim]{d}[/dim]")

        history_path = profile_path / "History"
        info(f"History DB:     {'[success]YES[/success]' if stats['history_exists'] else '[dim]not yet[/dim]'}")

        try:
            ls_count = len(await page.evaluate("() => { try { return Object.keys(localStorage); } catch(e) { return []; } }"))
            info(f"LocalStorage:   {ls_count} keys")
        except Exception:
            info(f"LocalStorage:   N/A")

        console.print()
        mtimes = [f.stat().st_mtime for f in profile_path.rglob("*") if f.is_file()]
        if mtimes:
            last = datetime.fromtimestamp(max(mtimes))
            info(f"Last modified:  {last.strftime('%Y-%m-%d %H:%M:%S')}")

        cf_found = await cf.check_cf_persistence()
        if not cf_found:
            warning("No cf_clearance cookie in profile")

        warmup = self._calc_warmup_score(stats)
        if warmup > 0:
            console.print()
            info(f"Warmup Score:   [highlight]{warmup}/100[/highlight]")
            bar = "#" * (warmup // 5) + "-" * (20 - warmup // 5)
            style = "highlight" if warmup > 30 else "dim"
            console.print(f"  [{style}]{bar}[/{style}]")

        self._track_profile_growth(stats)
        await cf.close()

    async def warmup(self) -> None:
        """Warmup mode: launch browser for manual browsing to build profile trust.

        No scraping. No downloading. User browses normally to accumulate
        cookies, cache, history, and Cloudflare trust.
        """
        from .scraper.cloudflare import CloudflareHandler

        startup_banner(__version__)
        console.print("  [bold yellow]Warmup Mode[/bold yellow]")
        console.print()
        console.print(
            "  Please use the browser normally.\n"
            "  Recommended:\n"
            "    - Sign into Google\n"
            "    - Browse a few websites\n"
            "    - Visit Manga Starz\n"
            "    - Visit Lek Manga\n"
            "    - Solve Cloudflare challenges\n"
            "    - Read a few chapters manually\n"
            "    - Spend at least 5-10 minutes browsing\n"
        )
        console.print(
            "  [bold]No scraping or downloading occurs during warmup.[/bold]"
        )
        console.print()

        browser = PlaywrightManager(profile_dir=str(self.config.profile_path), debug=False)
        ctx, page = await browser.start(headless=False, mode="warmup")

        await page.goto("https://www.google.com")
        await asyncio.sleep(1)

        await ctx.new_page()
        await asyncio.sleep(0.5)
        pages = ctx.pages
        if len(pages) >= 2:
            await pages[1].goto("https://manga-starz.net/")
        await asyncio.sleep(1)

        await ctx.new_page()
        await asyncio.sleep(0.5)
        pages = ctx.pages
        if len(pages) >= 3:
            await pages[2].goto("https://lek-manga.net/")
        await asyncio.sleep(1)

        console.print()
        console.print("  [highlight]Browser is ready. Browse freely.[/highlight]")
        console.print("  [bold]Press ENTER when finished...[/bold]")
        await asyncio.to_thread(sys.stdin.readline)
        console.print()

        def _f(n: int) -> str:
            for u in ("B", "KB", "MB", "GB"):
                if n < 1000:
                    return f"{n:.1f} {u}"
                n /= 1000
            return f"{n:.1f} TB"

        info(f"Cookies:        {stats['cookie_count']}")
        info(f"Domains:        {len(stats['domains'])}")
        info(f"Profile size:   {_f(stats['profile_size_bytes'])}")

        cf_found = await cf.check_cf_persistence()
        if cf_found:
            success("Cloudflare clearance cookie acquired")
        else:
            warning("No cf_clearance cookie yet — solve a Cloudflare challenge manually")

        warmup_score = self._calc_warmup_score(stats)
        console.print()
        info(f"Warmup Score:   [highlight]{warmup_score}/100[/highlight]")

        self._track_profile_growth(stats)
        await cf.close()
        console.print()
        success("Warmup complete — profile persisted")

    @staticmethod
    def _calc_warmup_score(stats: dict[str, Any]) -> int:
        score = 0
        c = stats.get("cookie_count", 0)
        if c >= 100:
            score += 40
        elif c >= 50:
            score += 30
        elif c >= 10:
            score += 20
        elif c > 2:
            score += 10
        domains = len(stats.get("domains", []))
        if domains >= 20:
            score += 30
        elif domains >= 10:
            score += 20
        elif domains >= 5:
            score += 10
        if stats.get("history_exists"):
            score += 20
        if stats.get("profile_size_bytes", 0) > 50_000_000:
            score += 10
        return min(score, 100)

    def _track_profile_growth(self, stats: dict[str, Any]) -> None:
        import json
        cache_dir = self.config.cache_path
        cache_dir.mkdir(parents=True, exist_ok=True)
        stats_path = cache_dir / "profile_stats.json"
        previous: dict[str, Any] = {}
        if stats_path.exists():
            try:
                previous = json.loads(stats_path.read_text())
            except Exception:
                pass
        entry = {
            "cookie_count": stats.get("cookie_count", 0),
            "profile_size": stats.get("profile_size_bytes", 0),
            "file_count": stats.get("file_count", 0),
            "timestamp": datetime.now().isoformat(),
        }
        stats_path.write_text(json.dumps(entry, indent=2))
        prev_cookies = previous.get("cookie_count", 0)
        curr_cookies = entry["cookie_count"]
        if prev_cookies > 0 and curr_cookies != prev_cookies:
            console.print()
            info(f"Growth: cookies {prev_cookies} -> [highlight]{curr_cookies}[/highlight] "
                 f"({'[success]+' + str(curr_cookies - prev_cookies) + '[/success]' if curr_cookies > prev_cookies else '[warning]' + str(curr_cookies - prev_cookies) + '[/warning]'})")
        prev_size = previous.get("profile_size", 0)
        curr_size = entry["profile_size"]
        if prev_size > 0 and abs(curr_size - prev_size) > 100_000:
            def _fmt_sz(n: int) -> str:
                for u in ("B", "KB", "MB", "GB"):
                    if n < 1000:
                        return f"{n:.1f} {u}"
                    n /= 1000
                return f"{n:.1f} TB"
            console.print()
            info(f"Growth: size {_fmt_sz(prev_size)} -> [highlight]{_fmt_sz(curr_size)}[/highlight] "
                 f"({'[success]+' + _fmt_sz(curr_size - prev_size) + '[/success]' if curr_size > prev_size else ''})")

    @staticmethod
    def _find_provider(url: str) -> type[BaseMangaProvider] | None:
        for cls in _PROVIDER_REGISTRY:
            if cls.accepts_url(url):
                return cls
        return None

    async def _search_and_select(self, query: str) -> str | None:
        """Search for manga by text query and let user select a result."""
        from .search import SearchManager

        search = SearchManager()
        results = search.search(query)

        if not results:
            warning(f"No manga found for '{query}'.")
            return None

        console.print()
        info(f"Search results for '[highlight]{query}[/highlight]':")
        console.print()

        max_display = min(len(results), 15)
        for i, r in enumerate(results[:max_display], 1):
            site_tag = f"[dim]({r.site})[/dim]"
            console.print(f"  [bold cyan]{i:2d}.[/bold cyan] {r.title[:60]} {site_tag}")

        if len(results) > max_display:
            console.print(f"  [dim]... and {len(results) - max_display} more[/dim]")

        console.print()
        choice = console.input(f"  Select result [1-{max_display}] or 0 to cancel: ").strip()

        try:
            idx = int(choice)
            if idx == 0:
                return None
            if 1 <= idx <= max_display:
                selected_url = results[idx - 1].url
                if not selected_url or not selected_url.startswith("http"):
                    warning(f"Invalid URL in search result: '{selected_url}'")
                    return None
                return selected_url
        except ValueError:
            pass

        warning(f"Invalid selection: '{choice}'")
        return None

    def _detect_url_type(self, url: str) -> str:
        try:
            parsed = urlparse(url)
        except Exception:
            return "unknown"
        segments = [s for s in parsed.path.strip("/").split("/") if s]
        if len(segments) < 2:
            return "unknown"
        if segments[0].lower() == "manga":
            if len(segments) == 2:
                return "manga"
            if len(segments) >= 3 and re.match(r"^\d+(\.\d+)?$", segments[-1]):
                return "chapter"
            return "manga"
        return "unknown"

    def _parse_chapter_number(self, url: str) -> float | None:
        segments = [s for s in urlparse(url).path.strip("/").split("/") if s]
        if segments:
            m = re.match(r"^(\d+(?:\.\d+)?)$", segments[-1])
            if m:
                return float(m.group(1))
        return None

    def _extract_manga_slug(self, url: str) -> str:
        segments = [s for s in urlparse(url).path.strip("/").split("/") if s]
        if len(segments) >= 2 and segments[0].lower() == "manga":
            return segments[1]
        return ""

    async def _run_workflow(self, url: str, url_type: str, provider_cls: type[BaseMangaProvider]) -> None:
        provider_name = provider_cls.SITE_NAME
        console.print()
        info(f"Provider: [highlight]{provider_name}[/highlight]")
        if url_type == "chapter":
            await self._handle_direct_chapter(url, provider_cls)
        else:
            await self._handle_manga_page(url, provider_cls)

    async def _handle_direct_chapter(self, url: str, provider_cls: type[BaseMangaProvider]) -> None:
        chapter_num = self._parse_chapter_number(url)
        manga_slug_val = self._extract_manga_slug(url)

        console.print()
        info("Detected direct chapter URL")

        self.provider = provider_cls(self.config, debug=self.debug)
        await self.provider.initialize()

        try:
            console.print()
            derived_title, detected_slug, image_urls = \
                await self.provider.discover_chapter_page(url, manga_slug_val)
        except (CloudflareDetectedError, DiscoveryValidationError):
            raise
        except Exception as e:
            error(f"Failed to analyze chapter page: {e}")
            return

        manga_title = derived_title
        slug = detected_slug or manga_slug_val

        if not self.provider.manga:
            from .models import Manga
            self.provider.manga = Manga(title=manga_title, slug=slug, url=url)
            self.provider.stats.manga_title = manga_title
            self.provider.stats.manga_slug = slug

        ch_num = chapter_num if chapter_num is not None else 1.0
        chapter = Chapter(number=ch_num, title="", url=url, slug=str(ch_num), image_urls=image_urls)

        manga_info(manga_title)
        chapter_info(chapter.display_number, f"{len(image_urls)} images discovered")
        console.print()

        confirm = console.input("  Continue with download? [Y/n]: ").strip().lower()
        if confirm and confirm != "y":
            return

        output_format = await self._select_format()
        console.print()

        result = await self.provider.download_chapter(chapter, output_format)
        if result:
            self.provider.stats.total_chapters = 1
            self.provider.stats.completed_chapters = 1

    async def _handle_manga_page(self, url: str, provider_cls: type[BaseMangaProvider]) -> None:
        self.provider = provider_cls(self.config, debug=self.debug)
        await self.provider.initialize()

        info("Discovering manga info and chapters...")
        discovery = await self.provider.discover_manga(url)
        manga = discovery.manga

        manga_info(manga.title)

        chapters = manga.sorted_chapters
        if not chapters:
            error("No chapters found. The site structure may have changed.")
            return

        chapter_list = [(ch.display_number, ch.title) for ch in chapters]
        display_chapter_list(chapter_list, len(chapters))

        selected = await self._select_chapters(chapters)
        if not selected:
            warning("No chapters selected. Exiting.")
            return

        output_format = await self._select_format()

        console.print()
        console.print(f"  Selected: [highlight]{len(selected)} chapters[/highlight]")
        console.print(f"  Format:   [highlight]{output_format.value.upper()}[/highlight]")
        console.print()

        confirm = console.input("  Proceed with download? [Y/n]: ").strip().lower()
        if confirm and confirm != "y":
            return

        console.print()
        await self.provider.download_chapters(selected, output_format)

    async def _select_chapters(self, chapters: list[Chapter]) -> list[Chapter]:
        selection_menu(
            title="Chapter Selection",
            options=[
                ("1", "Download Single Chapter", "Enter chapter number or index"),
                ("2", "Download Chapter Range", "e.g. 1-50 or 10.0-20.0"),
                ("3", "Download All Chapters", f"All {len(chapters)} chapters"),
                ("4", "Exit", "Cancel and quit"),
            ],
        )
        choice = console.input("  Select option [1-4]: ").strip()
        if choice == "1":
            return await self._select_single(chapters)
        elif choice == "2":
            return await self._select_range(chapters)
        elif choice == "3" or choice.lower() == "all":
            return chapters
        elif choice == "4":
            return []
        else:
            warning(f"Invalid choice: '{choice}'. Please select 1-4.")
            return []

    async def _select_single(self, chapters: list[Chapter]) -> list[Chapter]:
        while True:
            console.print()
            inp = console.input("  Enter chapter number or index (or 'b' to go back): ").strip()
            if not inp:
                return []
            if inp.lower() == "b":
                return await self._select_chapters(chapters)
            found = self._find_chapters(chapters, inp)
            if found:
                ch = found[0]
                console.print()
                chapter_info(ch.display_number, ch.title or "")
                return [ch]
            warning(f"No chapter matched '{inp}'. Try again.")

    async def _select_range(self, chapters: list[Chapter]) -> list[Chapter]:
        while True:
            console.print()
            console.print("  [dim]Examples: 1-50, 10.0-20.5, 100-364[/dim]")
            inp = console.input("  Enter range (or 'b' to go back): ").strip()
            if not inp:
                return []
            if inp.lower() == "b":
                return await self._select_chapters(chapters)
            m = re.match(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)", inp)
            if not m:
                warning("Invalid range format. Use: start-end (e.g. 1-50)")
                continue
            try:
                start, end = float(m.group(1)), float(m.group(2))
            except ValueError:
                warning("Invalid numbers in range.")
                continue
            if start > end:
                start, end = end, start
            selected = [ch for ch in chapters if start <= ch.number <= end]
            if not selected:
                warning(f"No chapters found in range {start}-{end}. Try again.")
                continue
            console.print()
            info(f"Selected {len(selected)} chapters ({start} to {end})")
            return selected

    @staticmethod
    def _find_chapters(chapters: list[Chapter], inp: str) -> list[Chapter]:
        try:
            num = float(inp)
            matches = [ch for ch in chapters if ch.number == num]
            if matches:
                return matches
        except ValueError:
            pass
        try:
            idx = int(inp)
            if 0 < idx <= len(chapters):
                return [chapters[idx - 1]]
        except ValueError:
            pass
        inp_lower = inp.lower()
        for ch in chapters:
            if inp_lower in ch.title.lower() or inp_lower in str(ch.number):
                return [ch]
        return []

    async def _select_format(self) -> OutputFormat:
        format_menu(
            title="Output Format:",
            options=["CBZ only (recommended)", "PDF only", "Both CBZ and PDF"],
            default="1",
        )
        choice = console.input("\n  Select format [1-3] (default 1): ").strip() or "1"
        return {"1": OutputFormat.CBZ, "2": OutputFormat.PDF, "3": OutputFormat.BOTH}.get(choice, OutputFormat.CBZ)

    async def manual_navigation_test(self) -> None:
        """Diagnostic: user navigates manually vs page.goto() automation.

        Launches browser normally, waits for user to manually navigate
        to the target URL, then diagnoses whether Cloudflare challenges
        manual navigation the same way it challenges page.goto().
        """
        from .scraper.cloudflare import CloudflareHandler

        startup_banner(__version__)
        console.print("  [bold yellow]Manual Navigation Test[/bold yellow]")
        console.print()

        browser = PlaywrightManager(profile_dir=str(self.config.profile_path), debug=False)
        ctx, page = await browser.start(headless=False, mode="manual-test")
        cf = CloudflareHandler(browser, debug=False)
        cf._context = ctx
        cf._page = page

        # Get pre-navigation cookies
        cookies = await cf._context.cookies()
        cf_clearance = [c for c in cookies if c.get("name") == "cf_clearance"]
        info(f"Pre-nav cookies:   {len(cookies)}")
        info(f"cf_clearance:      {'YES' if cf_clearance else 'NO'}")

        # Open blank page — NO page.goto() to target
        await cf._page.goto("about:blank")
        await asyncio.sleep(0.5)

        console.print()
        console.print("  [highlight]Please manually navigate to:[/highlight]")
        console.print("  [bold]https://manga-starz.net/manga/berserk[/bold]")
        console.print()
        console.print("  [dim]Press ENTER after the page finishes loading.[/dim]")
        await asyncio.to_thread(sys.stdin.readline)

        # Diagnose the page the user landed on
        await asyncio.sleep(2)
        title = await cf._page.evaluate("() => document.title")
        url = cf._page.url

        is_cf = any(t in title.lower() for t in [
            "just a moment", "please wait", "checking your browser",
            "attention required", "verifying", "cloudflare",
        ])
        has_cf_url = any(p in url for p in ["/cdn-cgi/", "challenge-platform"])

        console.print()
        info(f"Current URL:    {url}")
        info(f"Current Title:  {title}")
        if is_cf or has_cf_url:
            warning("Cloudflare:  ACTIVE (manual navigation also challenged)")
        else:
            success("Cloudflare:  CLEAR (manual navigation NOT challenged)")

        # Save HTML snapshot
        html = await cf._page.content()
        snapshot_path = self.config.cache_path / "manual_nav_snapshot.html"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(html, encoding="utf-8")
        info(f"HTML snapshot:   {snapshot_path}")

        console.print()
        if is_cf or has_cf_url:
            error("Both manual navigation AND page.goto() trigger Cloudflare.")
            error("The issue is session trust itself — not automation behavior.")
        else:
            console.print("  [highlight]Manual navigation SUCCEEDED.[/highlight]")
            console.print("  [highlight]This proves page.goto() triggers Cloudflare where manual browsing does not.[/highlight]")

        await cf.close()

    async def kameleo_test(self) -> None:
        """Diagnostic: verify KameleoManager is actually used at runtime.

        Connects via Kameleo, prints profile info, fingerprint, cookies.
        No scraping. No fallback.
        """
        from .browser.kameleo_manager import KameleoManager

        startup_banner(__version__)
        console.print("  [bold yellow]Kameleo Diagnostic Test[/bold yellow]")
        console.print()

        self.config.browser.browser_backend = "kameleo"
        port = self.config.browser.kameleo_port
        profile_path = str(self.config.profile_path)

        browser = KameleoManager(profile_dir=profile_path, debug=True, kameleo_port=port)
        info(f"API URL:      {browser.api_url}")
        info(f"Profile dir:  {profile_path}")

        # Runtime assertion — print actual class
        actual_class = type(browser).__name__
        info(f"Class:        [highlight]{actual_class}[/highlight]")
        assert actual_class == "KameleoManager", f"Expected KameleoManager, got {actual_class}"
        success("Runtime assertion: KameleoManager confirmed")

        if not browser.check_api_reachable():
            error(f"Kameleo API not reachable at {browser.api_url}")
            error("Ensure Kameleo is running.")
            return

        success("Kameleo API reachable")

        console.print()
        info("Starting Kameleo profile...")
        ctx, page = await browser.start(headless=False, mode="kameleo-test")

        console.print()
        info(f"Profile ID:   {browser.profile_id}")
        info(f"CDP Endpoint: {browser.cdp_endpoint[:80]}")

        cookies = await ctx.cookies()
        info(f"Cookie count: {len(cookies)}")

        # Collect fingerprint
        fp = await page.evaluate("""() => {
            return {
                userAgent: navigator.userAgent,
                webdriver: navigator.webdriver,
                languages: navigator.languages,
                platform: navigator.platform,
                hardwareConcurrency: navigator.hardwareConcurrency,
                deviceMemory: navigator.deviceMemory,
                vendor: navigator.vendor,
                plugins_length: navigator.plugins ? navigator.plugins.length : 0,
                chrome_exists: typeof chrome !== 'undefined',
            };
        }""")

        console.print()
        info("Fingerprint:")
        for k, v in fp.items():
            info(f"  {k}: {v}")

        # Save report
        import json
        report = {
            "backend": type(browser).__name__,
            "profile_id": browser.profile_id,
            "cdp_endpoint": browser.cdp_endpoint,
            "cookies": len(cookies),
            "fingerprint": fp,
        }
        cache_dir = self.config.cache_path
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "kameleo_fingerprint.json").write_text(json.dumps(report, indent=2))
        info(f"Report saved: cache/kameleo_fingerprint.json")

        await browser.close()
        console.print()
        success("Kameleo test complete")

    async def kameleo_navigation_test(self) -> None:
        """Kameleo navigation test: open Manga Starz, report Cloudflare status.

        Forces KameleoManager, navigates to berserk manga page,
        detects Cloudflare, saves HTML snapshot.
        """
        from .browser.kameleo_manager import KameleoManager
        from .scraper.cloudflare import CloudflareHandler

        startup_banner(__version__)
        console.print("  [bold yellow]Kameleo Navigation Test[/bold yellow]")
        console.print()

        self.config.browser.browser_backend = "kameleo"
        port = self.config.browser.kameleo_port
        profile_path = str(self.config.profile_path)

        browser = KameleoManager(profile_dir=profile_path, debug=True, kameleo_port=port)

        actual_class = type(browser).__name__
        info(f"Manager class: [highlight]{actual_class}[/highlight]")
        assert actual_class == "KameleoManager", f"Expected KameleoManager, got {actual_class}"
        success("Runtime assertion: KameleoManager confirmed")

        if not browser.check_api_reachable():
            error(f"Kameleo API not reachable at {browser.api_url}")
            return

        ctx, page = await browser.start(headless=False, mode="kameleo-nav")

        cf = CloudflareHandler(browser, debug=False)
        cf._context = ctx
        cf._page = page

        info("Navigating to: https://manga-starz.net/manga/berserk")
        try:
            await page.goto("https://manga-starz.net/manga/berserk", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            warning(f"Navigation error: {e}")
            await asyncio.sleep(2)

        title = await page.evaluate("() => document.title")
        cur_url = page.url
        is_cf = any(t in title.lower() for t in [
            "just a moment", "please wait", "checking your browser",
            "attention required", "verifying", "cloudflare",
        ])
        has_cf_url = any(p in cur_url for p in ["/cdn-cgi/", "challenge-platform"])

        console.print()
        info(f"Current URL:    {cur_url}")
        info(f"Current Title:  {title}")
        if is_cf or has_cf_url:
            warning("Cloudflare:  ACTIVE")
        else:
            success("Cloudflare:  CLEAR")

        html = await page.content()
        snapshot_path = self.config.cache_path / "kameleo_nav_snapshot.html"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(html, encoding="utf-8")
        info(f"HTML snapshot:  cache/kameleo_nav_snapshot.html")

        await cf.close()
        console.print()
        success("Kameleo navigation test complete")

    async def kameleo_debug(self) -> None:
        """Verify Kameleo API connectivity and discover endpoints."""
        import urllib.request, json as _json

        startup_banner(__version__)
        console.print("  [bold yellow]Kameleo API Debug[/bold yellow]")
        console.print()

        port = self.config.browser.kameleo_port
        api_url = f"http://127.0.0.1:{port}"

        info(f"API URL:      {api_url}")

        try:
            r = urllib.request.urlopen(f"{api_url}/general/healthcheck", timeout=5)
            info(f"Health status: {r.status}")
            body = r.read().decode()[:200]
            info(f"Body:          {body}")
        except Exception as e:
            error(f"Health check FAILED: {e}")
            return

        console.print()
        try:
            r = urllib.request.urlopen(f"{api_url}/swagger/v1/swagger.json", timeout=5)
            d = _json.loads(r.read())
            info(f"API Title:     {d.get('info', {}).get('title', '?')}")
            info(f"API Version:   {d.get('info', {}).get('version', '?')}")
            paths = sorted(d.get("paths", {}).keys())
            info(f"Endpoints:     {len(paths)}")
            for p in paths:
                info(f"  [dim]{p}[/dim]")
        except Exception as e:
            warning(f"Swagger fetch failed: {e}")

        console.print()
        try:
            r = urllib.request.urlopen(f"{api_url}/fingerprints", timeout=5)
            fps = _json.loads(r.read())
            info(f"Fingerprints:  {len(fps) if isinstance(fps, list) else '?'}")
            if isinstance(fps, list) and fps:
                for fp in fps[:5]:
                    fid = fp.get("id", fp.get("name", "?"))
                    info(f"  [dim]ID: {fid}[/dim]")
        except Exception as e:
            warning(f"Fingerprint list failed: {e}")

        console.print()
        try:
            r = urllib.request.urlopen(f"{api_url}/profiles", timeout=5)
            profiles = _json.loads(r.read())
            count = len(profiles) if isinstance(profiles, list) else 0
            info(f"Profiles:      {count}")
            for p in (profiles if isinstance(profiles, list) else [])[:5]:
                info(f"  [dim]ID: {p.get('id', '?')}  name: {p.get('name', '?')}[/dim]")
        except Exception as e:
            warning(f"Profile list failed: {e}")

        console.print()
        success("Kameleo API debug complete")

    async def kameleo_sdk_debug(self) -> None:
        """Print installed Kameleo SDK signatures."""
        import inspect

        startup_banner(__version__)
        console.print("  [bold yellow]Kameleo SDK Diagnostics[/bold yellow]")
        console.print()

        try:
            from kameleo.local_api_client import KameleoLocalApiClient
            c = KameleoLocalApiClient(endpoint="http://127.0.0.1:5050")
        except ImportError as e:
            error(f"Kameleo SDK not installed: {e}")
            return

        try:
            import kameleo
            info(f"SDK version:   {kameleo.__version__ if hasattr(kameleo, '__version__') else 'unknown'}")
        except Exception:
            pass

        sigs = {
            "create_profile": c.profile.create_profile,
            "start_profile": c.profile.start_profile,
            "get_profile_status": c.profile.get_profile_status,
            "search_fingerprints": c.fingerprint.search_fingerprints,
            "stop_profile": c.profile.stop_profile,
        }

        for name, method in sigs.items():
            sig = inspect.signature(method)
            params = list(sig.parameters.keys())
            required = [p for p in params if sig.parameters[p].default is inspect.Parameter.empty]
            info(f"{name}({', '.join(params[:4])}{'...' if len(params) > 4 else ''})")
            if required:
                info(f"  [dim]required: {required}[/dim]")

        # SDK model inspection
        console.print()
        from kameleo.local_api_client.models import CreateProfileRequest, StatusResponse, BrowserSettings
        info(f"CreateProfileRequest fields: {list(CreateProfileRequest.model_fields.keys())[:8]}")
        info(f"StatusResponse fields:       {list(StatusResponse.model_fields.keys())}")
        info(f"BrowserSettings fields:      {list(BrowserSettings.model_fields.keys())}")

        console.print()
        success("SDK diagnostics complete")

    async def kameleo_fingerprint_list(self) -> None:
        """List all available Kameleo fingerprints with filtering info."""
        try:
            from kameleo.local_api_client import KameleoLocalApiClient
        except ImportError:
            error("Kameleo SDK not installed")
            return

        startup_banner(__version__)
        console.print("  [bold yellow]Kameleo Fingerprints[/bold yellow]")
        console.print()

        client = KameleoLocalApiClient(
            endpoint=f"http://127.0.0.1:{self.config.browser.kameleo_port}"
        )

        info("Fetching fingerprints...")
        try:
            raw = client.fingerprint.search_fingerprints(
                device_type="desktop", browser_product="chrome"
            )
        except Exception as e:
            error(f"Failed: {e}")
            return

        info(f"Total: {len(raw)} desktop Chrome fingerprints")
        console.print()

        from .browser.kameleo_manager import KameleoManager
        filtered = KameleoManager._filter_fingerprints(raw)

        info(f"Matched: {len(filtered)} (desktop + Windows + Chrome + no proxy)")
        console.print()

        for i, f in enumerate(filtered[:20]):
            proxy_mark = "[warning]PROXY[/warning]" if f["proxy"] else "[success]none[/success]"
            console.print(f"  [highlight]{i:2d}[/highlight]  {f['id'][:20]}...  {f['browser']} v{f['version']}  proxy={proxy_mark}")

        if len(filtered) > 20:
            info(f"  ... and {len(filtered) - 20} more")

        console.print()
        success("Fingerprint list complete")

    async def kameleo_launch_test(self) -> None:
        """Launch a real Kameleo browser window. No CDP required."""
        try:
            from kameleo.local_api_client import KameleoLocalApiClient
            from kameleo.local_api_client.models import CreateProfileRequest as CPR
        except ImportError:
            error("Kameleo SDK not installed")
            return

        startup_banner(__version__)
        console.print("  [bold yellow]Kameleo Launch Test[/bold yellow]")
        console.print()

        port = self.config.browser.kameleo_port
        client = KameleoLocalApiClient(endpoint=f"http://127.0.0.1:{port}")

        # 1. Get fingerprints
        info("Fetching fingerprints (desktop/Chrome)...")
        try:
            raw = client.fingerprint.search_fingerprints(
                device_type="desktop", browser_product="chrome"
            )
        except Exception as e:
            error(f"Fingerprint fetch FAILED: {e}")
            return
        success(f"Got {len(raw)} fingerprints")

        # 2. Filter
        from .browser.kameleo_manager import KameleoManager
        filtered = KameleoManager._filter_fingerprints(raw)
        if not filtered:
            error("No fingerprints match: desktop + Windows + Chrome + no proxy")
            return
        best = filtered[0]
        success(f"Selected: {best['browser']} v{best['version']}")

        # 3. Create profile
        info("Creating profile...")
        try:
            body = CPR(fingerprint_id=best["id"])
            result = client.profile.create_profile(create_profile_request=body)
            pid = result.id
        except Exception as e:
            error(f"Create FAILED: {e}")
            return
        success(f"Profile: {pid}")

        # 4. Start profile
        info("Starting profile (browser should open now)...")
        try:
            status = client.profile.start_profile(pid)
        except Exception as e:
            error(f"Start FAILED: {e}")
            try:
                import requests
                r = requests.post(f"http://127.0.0.1:{port}/profiles/{pid}/start", timeout=10)
                error(f"Raw HTTP {r.status_code}: {r.text[:500]}")
            except Exception:
                pass
            return

        success(f"Profile started: {status.lifetime_state}")
        console.print()
        success("Kameleo browser window should now be visible")

        info("Waiting 10 seconds... (do NOT close the browser)")
        await asyncio.sleep(10)

        info("Stopping profile...")
        try:
            client.profile.stop_profile(pid)
            success("Profile stopped")
        except Exception as e:
            info(f"Stop result: {e}")

        console.print()
        success("Kameleo launch test complete")

    async def kameleo_mangastarz_test(self) -> None:
        """Full Kameleo test: launch, navigate to Manga Starz, detect CF, keep alive."""
        from .browser.kameleo_manager import KameleoManager
        from .scraper.cloudflare import CloudflareHandler

        startup_banner(__version__)
        console.print("  [bold yellow]Kameleo -> Manga Starz Test[/bold yellow]")
        console.print()

        self.config.browser.browser_backend = "kameleo"
        browser = KameleoManager(
            profile_dir=str(self.config.profile_path),
            debug=True,
            kameleo_port=self.config.browser.kameleo_port,
        )
        assert type(browser).__name__ == "KameleoManager"
        info(f"Manager: KameleoManager (confirmed)")

        if not browser.check_api_reachable():
            error(f"Kameleo API not reachable at {browser.api_url}")
            return

        info("Starting Kameleo profile...")
        try:
            ctx, page = await browser.start(headless=False, mode="mangastarz-test")
        except RuntimeError as e:
            error(f"Start failed: {e}")
            await browser.close()
            return

        if page is None:
            warning("No CDP available — Kameleo browser is running but Playwright cannot control it")
            info("Browser will stay open for 30 seconds for manual inspection")
            await asyncio.sleep(30)
            await browser.close()
            return

        info("Kameleo browser running with Playwright CDP control")
        info("Profile ID: " + browser.profile_id)

        # Navigate to Manga Starz
        console.print()
        info("Navigating to https://manga-starz.net/manga/berserk ...")
        try:
            await page.goto(
                "https://manga-starz.net/manga/berserk",
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception as e:
            warning(f"Navigation error: {e}")
            await asyncio.sleep(3)

        # Wait for page to settle
        await asyncio.sleep(2)
        title = await page.evaluate("() => document.title")
        cur_url = page.url
        cookies = await ctx.cookies() if ctx else []

        console.print()
        info(f"Current URL:  {cur_url}")
        info(f"Current Title: {title}")
        info(f"Cookies:       {len(cookies)}")

        is_cf = any(t in title.lower() for t in [
            "just a moment", "please wait", "checking your browser",
            "attention required", "verifying", "cloudflare",
        ])
        has_cf_url = any(p in cur_url for p in ["/cdn-cgi/", "challenge-platform"])

        if is_cf or has_cf_url:
            warning("Cloudflare:  ACTIVE")
        else:
            success("Cloudflare:  CLEAR")

        # Save HTML snapshot
        html = await page.content()
        snapshot_path = self.config.cache_path / "kameleo_mangastarz.html"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(html, encoding="utf-8")
        info(f"HTML snapshot: cache/kameleo_mangastarz.html")

        # Keep browser alive for inspection
        console.print()
        info("Keeping browser alive for 60 seconds for manual inspection...")
        for remaining in range(60, 0, -5):
            console.print(f"  [dim]{remaining}s remaining...[/dim]")
            await asyncio.sleep(5)

        info("Closing browser...")
        await browser.close()
        console.print()
        success("Kameleo Manga Starz test complete — shutdown clean")
        """Kameleo end-to-end: create profile, launch, navigate to Google."""
        from .browser.kameleo_manager import KameleoManager

        startup_banner(__version__)
        console.print("  [bold yellow]Kameleo -> Google[/bold yellow]")
        console.print()

        self.config.browser.browser_backend = "kameleo"
        browser = KameleoManager(
            profile_dir=str(self.config.profile_path),
            debug=True,
            kameleo_port=self.config.browser.kameleo_port,
        )
        assert type(browser).__name__ == "KameleoManager"

        if not browser.check_api_reachable():
            error(f"Kameleo API not reachable at {browser.api_url}")
            return

        info("Creating/reusing Kameleo profile...")
        try:
            ctx, page = await browser.start(headless=False, mode="google-test")
        except RuntimeError as e:
            error(f"Start failed: {e}")
            return

        console.print()
        info("Navigating to https://google.com ...")
        await page.goto("https://google.com", wait_until="domcontentloaded", timeout=30000)
        title = await page.evaluate("() => document.title")
        url = page.url

        console.print()
        info(f"Title:         {title}")
        info(f"URL:           {url}")
        if "Google" in title:
            success("Google navigation SUCCESS")
        else:
            warning(f"Unexpected title: {title}")

        await browser.close()
        console.print()
        success("Kameleo Google test complete")

    async def kameleo_open_mangastarz(self) -> None:
        """Kameleo end-to-end: navigate to Manga Starz, detect Cloudflare."""
        from .browser.kameleo_manager import KameleoManager
        from .scraper.cloudflare import CloudflareHandler

        startup_banner(__version__)
        console.print("  [bold yellow]Kameleo -> Manga Starz[/bold yellow]")
        console.print()

        self.config.browser.browser_backend = "kameleo"
        browser = KameleoManager(
            profile_dir=str(self.config.profile_path),
            debug=True,
            kameleo_port=self.config.browser.kameleo_port,
        )
        assert type(browser).__name__ == "KameleoManager"

        if not browser.check_api_reachable():
            error(f"Kameleo API not reachable at {browser.api_url}")
            return

        info("Creating/reusing Kameleo profile...")
        try:
            ctx, page = await browser.start(headless=False, mode="mangastarz-test")
        except RuntimeError as e:
            error(f"Start failed: {e}")
            return

        cf = CloudflareHandler(browser, debug=False)
        cf._context = ctx
        cf._page = page

        console.print()
        info("Navigating to https://manga-starz.net ...")
        await page.goto("https://manga-starz.net", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        title = await page.evaluate("() => document.title")
        cur_url = page.url
        cookies = await ctx.cookies()

        is_cf = any(t in title.lower() for t in [
            "just a moment", "please wait", "checking your browser",
            "attention required", "verifying", "cloudflare",
        ])

        console.print()
        info(f"Title:         {title}")
        info(f"URL:           {cur_url}")
        info(f"Cookies:       {len(cookies)}")
        if is_cf:
            warning("Cloudflare:    ACTIVE")
        else:
            success("Cloudflare:    CLEAR")

        try:
            screenshot_path = self.config.cache_path / "kameleo_mangastarz.png"
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(screenshot_path))
            info(f"Screenshot:    cache/kameleo_mangastarz.png")
        except Exception as e:
            warning(f"Screenshot failed: {e}")

        await cf.close()
        console.print()
        success("Kameleo Manga Starz test complete")

    def _show_summary(self) -> None:
        if self.provider is None:

            return
        stats = self.provider.stats
        if stats.total_chapters == 0:
            return
        stats.elapsed_seconds = time.time() - self._start_time
        stats.manga_title = self.provider.manga.title if self.provider.manga else ""
        stats.manga_slug = self.provider.manga.slug if self.provider.manga else ""
        download_summary(stats)

    def _record_download_history(self) -> None:
        """Record completed downloads to history database."""
        if self.provider is None or self.provider.manga is None:
            return
        stats = self.provider.stats
        if stats.completed_chapters == 0:
            return
        try:
            from .history import HistoryManager
            from pathlib import Path
            hm = HistoryManager()
            manga = self.provider.manga
            safe = manga.title.replace("/", "_").replace("\\", "_")
            # Record based on stats, not individual chapter status
            # (provider tracks completed_chapters count but may not update Chapter.status)
            for ch in manga.sorted_chapters:
                # Check if CBZ file exists as evidence of completion
                cbz_path = Path(self.config.download_dir) / safe / f"{ch.safe_filename}.cbz"
                if cbz_path.exists():
                    file_size = cbz_path.stat().st_size
                    hm.record(
                        manga_title=manga.title, manga_url=manga.url,
                        chapter=ch.display_number, chapter_number=ch.number,
                        status="completed", image_count=ch.page_count,
                        file_path=str(cbz_path), file_size=file_size, duration_s=0,
                    )
            hm.close()
        except Exception as e:
            if self.debug:
                import traceback; console.print(traceback.format_exc())


def main() -> None:
    config = AppConfig.get_instance()
    url: str | None = None
    profile_info_flag: bool = False
    warmup_flag: bool = False
    manual_nav_flag: bool = False
    kameleo_test_flag: bool = False
    kameleo_nav_test_flag: bool = False
    kameleo_debug_flag: bool = False
    kameleo_google_flag: bool = False
    kameleo_mangastarz_flag: bool = False
    kameleo_sdk_debug_flag: bool = False
    kameleo_fp_list_flag: bool = False
    kameleo_launch_test_flag: bool = False
    kameleo_mangastarz_test_flag: bool = False

    args = sys.argv[1:]
    for arg in args:
        if arg in ("--profile-info",):
            profile_info_flag = True
        elif arg in ("--warmup",):
            warmup_flag = True
        elif arg in ("--manual-navigation-test",):
            manual_nav_flag = True
        elif arg in ("--kameleo-test",):
            kameleo_test_flag = True
        elif arg in ("--kameleo-navigation-test",):
            kameleo_nav_test_flag = True
        elif arg in ("--kameleo-debug",):
            kameleo_debug_flag = True
        elif arg in ("--kameleo-open-google",):
            kameleo_google_flag = True
        elif arg in ("--kameleo-open-mangastarz",):
            kameleo_mangastarz_flag = True
        elif arg in ("--kameleo-sdk-debug",):
            kameleo_sdk_debug_flag = True
        elif arg in ("--kameleo-fingerprint-list",):
            kameleo_fp_list_flag = True
        elif arg in ("--kameleo-launch-test",):
            kameleo_launch_test_flag = True
        elif arg in ("--kameleo-mangastarz-test",):
            kameleo_mangastarz_test_flag = True
        elif arg.startswith("--debug"):
            pass
        elif not arg.startswith("-"):
            url = arg

    cli = MangaDownloaderCLI(config=config)
    if kameleo_test_flag:
        asyncio.run(cli.kameleo_test())
    elif kameleo_nav_test_flag:
        asyncio.run(cli.kameleo_navigation_test())
    elif kameleo_sdk_debug_flag:
        asyncio.run(cli.kameleo_sdk_debug())
    elif kameleo_fp_list_flag:
        asyncio.run(cli.kameleo_fingerprint_list())
    elif kameleo_launch_test_flag:
        asyncio.run(cli.kameleo_launch_test())
    elif kameleo_mangastarz_test_flag:
        asyncio.run(cli.kameleo_mangastarz_test())
    elif kameleo_debug_flag:
        asyncio.run(cli.kameleo_debug())
    elif kameleo_google_flag:
        asyncio.run(cli.kameleo_open_google())
    elif kameleo_mangastarz_flag:
        asyncio.run(cli.kameleo_open_mangastarz())
    elif manual_nav_flag:
        asyncio.run(cli.manual_navigation_test())
    elif warmup_flag:
        asyncio.run(cli.warmup())
    elif profile_info_flag:
        asyncio.run(cli.profile_info())
    else:
        asyncio.run(cli.run(url))


if __name__ == "__main__":
    main()
