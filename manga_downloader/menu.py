"""Menu system — number-based UI with pagination and provider filter."""

from __future__ import annotations

from .search import SearchManager
from .utils.logger import console, info, success, warning, error, startup_banner

def _clear():
    import os
    os.system("cls" if os.name == "nt" else "clear")

def _input(prompt: str = "") -> str:
    return console.input(f"  {prompt}").strip()

class MenuSystem:

    def __init__(self, version: str):
        self.version = version

    def _show_banner(self):
        _clear()
        startup_banner(self.version)

    # ==================================================================
    # MAIN MENU
    # ==================================================================
    def main_menu(self) -> str | None:
        fm_count = 0; q_count = 0; h_count = 0
        try:
            from .follow_manager import FollowManager; fm_count = FollowManager().count
        except Exception: pass
        try:
            from .queue_manager import DownloadQueue; q_count = DownloadQueue().size
        except Exception: pass
        try:
            from .history import HistoryManager
            hm = HistoryManager(); h_count = hm.stats().get("total_downloads", 0); hm.close()
        except Exception: pass

        while True:
            self._show_banner()
            if fm_count or q_count or h_count:
                if fm_count: console.print(f"  [dim]Followed: {fm_count}[/dim]", end="  ")
                if q_count: console.print(f"  [dim]Queue: {q_count}[/dim]", end="  ")
                if h_count: console.print(f"  [dim]History: {h_count}[/dim]")
            console.print()
            console.print("  1. Download Manga")
            console.print("  2. Search Manga")
            console.print("  3. Followed Manga")
            console.print("  4. Check Updates")
            console.print("  5. Download History")
            console.print("  6. Download Queue")
            console.print("  7. Exit")
            console.print()
            choice = _input("Select [1-7]:")
            mapping = {"1": "download", "2": "search", "3": "followed", "4": "updates",
                       "5": "history", "6": "queue", "7": "exit"}
            if choice in mapping:
                return None if mapping[choice] == "exit" else mapping[choice]

    # ==================================================================
    # 1. DOWNLOAD
    # ==================================================================
    def download_screen(self) -> tuple[str | None, str]:
        _clear()
        console.print("\n  [bold cyan]Download Manga[/bold cyan]")
        console.print("  [dim]URL or manga name (searches all sites)[/dim]")
        console.print()
        user_input = _input("URL or Name (0 to cancel):")
        if not user_input or user_input == "0":
            return None, "cancel"
        return user_input, "input"

    # ==================================================================
    # 2. SEARCH (paginated + filter)
    # ==================================================================
    def search_screen_with_query(self, query: str) -> tuple[str | None, str]:
        sm = SearchManager()
        all_results = sm.search(query)
        if not all_results:
            console.print(f"\n  [yellow]No results for '{query}'.[/yellow]")
            _input("Press Enter to continue:")
            return None, "cancel"
        return self._paginated_search(query, all_results)

    def search_screen(self) -> tuple[str | None, str]:
        _clear()
        console.print("\n  [bold cyan]Search Manga[/bold cyan]")
        query = _input("Search term (0 to cancel):")
        if not query or query == "0":
            return None, "cancel"
        sm = SearchManager()
        all_results = sm.search(query)
        if not all_results:
            _clear()
            console.print(f"\n  [yellow]No results for '{query}'.[/yellow]")
            _input("Press Enter to continue:")
            return None, "cancel"
        return self._paginated_search(query, all_results)

    def _paginated_search(self, query: str, all_results: list) -> tuple[str | None, str]:
        providers = sorted(set(r.site for r in all_results))
        active_filter = "All"
        page = 0
        page_size = 10

        while True:
            _clear()
            filtered = all_results if active_filter == "All" else [r for r in all_results if r.site == active_filter]
            total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
            start = page * page_size
            end = min(start + page_size, len(filtered))
            page_items = filtered[start:end]

            console.print(f"\n  [bold cyan]Results for '{query}'[/bold cyan]")
            console.print(f"  [dim]Filter: {active_filter}  |  Page {page+1}/{total_pages}  |  {len(filtered)} results[/dim]")
            console.print(f"  [dim]F=filter  N=next page  P=prev page  0=cancel[/dim]")
            console.print()

            for i, r in enumerate(page_items, start + 1):
                console.print(f"  [bold cyan]{i:2d}.[/bold cyan] {r.title[:55]} [dim]({r.site})[/dim]")

            console.print()
            if len(filtered) > end:
                console.print(f"  [dim]Press N for next page — {len(filtered) - end} more results[/dim]")

            choice = _input("Select or action:").strip().lower()
            if choice == "0": return None, "cancel"
            elif choice == "n" and page < total_pages - 1: page += 1
            elif choice == "p" and page > 0: page -= 1
            elif choice == "f":
                _clear()
                console.print("\n  [bold cyan]Filter by Provider[/bold cyan]")
                console.print(f"  0. All ({len(all_results)} results)")
                for i, p in enumerate(providers, 1):
                    count = len([r for r in all_results if r.site == p])
                    console.print(f"  {i}. {p} ({count} results)")
                f_choice = _input("Select filter:")
                if f_choice == "0": active_filter = "All"
                else:
                    try:
                        idx = int(f_choice)
                        if 1 <= idx <= len(providers): active_filter = providers[idx - 1]
                    except ValueError: pass
                page = 0
            else:
                try:
                    idx = int(choice)
                    if 0 <= idx - 1 < len(filtered):
                        r = filtered[idx - 1]
                        if r.url and r.url.startswith("http"): return r.url, "url"
                except ValueError: pass

    # ==================================================================
    # 3. FOLLOWED MANGA
    # ==================================================================
    def followed_screen(self) -> str | None:
        from .follow_manager import FollowManager
        fm = FollowManager()
        while True:
            _clear()
            items = fm.items
            console.print("\n  [bold cyan]Followed Manga[/bold cyan]")
            console.print("  [dim]A=Add  R=Remove  U=Check Updates  0=Back[/dim]")
            console.print()
            if not items:
                console.print("  [dim]No manga followed yet. Press A to add.[/dim]")
            else:
                for i, m in enumerate(items, 1):
                    ch = f"Ch.{m.latest_known_chapter:.0f}" if m.latest_known_chapter else "?"
                    console.print(f"  [bold cyan]{i}.[/bold cyan] {m.title[:50]} [dim]({m.site}, {ch})[/dim]")
            key = _input("Select or action:").strip().lower()
            if key == "0": return None
            elif key == "a":
                url = _input("Manga URL:")
                if url and url.startswith("http"):
                    title = url.rstrip("/").rsplit("/",1)[-1].replace("-"," ").title()
                    fm.add(title, url); success(f"Added: {title}")
            elif key == "r":
                try:
                    idx = int(_input("Number to remove:"))
                    if 1 <= idx <= len(items):
                        fm.remove(items[idx-1].url); success(f"Removed: {items[idx-1].title}")
                except ValueError: pass
            elif key == "u": return "check_updates"
            elif key.isdigit():
                idx = int(key)
                if 1 <= idx <= len(items): return items[idx-1].url

    # ==================================================================
    # 4. UPDATE CHECKER
    # ==================================================================
    async def update_checker_screen(self) -> str:
        _clear()
        console.print("\n  [bold cyan]Check Updates[/bold cyan]")
        from .follow_manager import FollowManager
        from .update_checker import UpdateChecker
        fm = FollowManager()
        if fm.count == 0:
            console.print("  [yellow]No manga followed.[/yellow]")
            _input("Press Enter to continue:")
            return "back"
        info(f"Checking {fm.count} manga...")
        console.print()
        checker = UpdateChecker(fm)
        await checker.initialize()
        results = await checker.check_all()
        new = 0
        for r in results:
            if r.has_updates:
                new += 1
                success(f"  {r.manga.title[:40]}: {r.previous_chapter:.0f} -> {r.current_latest:.0f} (+{r.new_chapters})")
            else:
                console.print(f"  [dim]{r.manga.title[:40]}: up to date ({r.current_latest:.0f})[/dim]")
        await checker.close()
        console.print()
        if new: success(f"{new} manga have new chapters!")
        else: console.print("  [dim]All up to date.[/dim]")
        _input("Press Enter to continue:")
        return "back"

    # ==================================================================
    # 5. HISTORY
    # ==================================================================
    def history_screen(self) -> str:
        from datetime import datetime
        from .history import HistoryManager
        hm = HistoryManager()
        while True:
            _clear()
            console.print("\n  [bold cyan]Download History[/bold cyan]")
            s = hm.stats()
            console.print(f"  [dim]{s['total_downloads']} total  |  {s['completed']} ok  |  {s['failed']} failed  |  {s['total_images']} images[/dim]")
            console.print("  [dim]C=Clear  0=Back[/dim]")
            console.print()
            entries = hm.query(limit=20)
            if not entries:
                console.print("  [dim]No history yet. Downloads appear here automatically.[/dim]")
            else:
                for i, e in enumerate(entries, 1):
                    ts = datetime.fromtimestamp(e.timestamp).strftime("%m-%d %H:%M")
                    st = "[green]OK[/green]" if e.status == "completed" else "[red]FAIL[/red]"
                    console.print(f"  {i:2d}. {e.manga_title[:28]:<30} {e.chapter:<12} {st} {ts}")
            key = _input("Action:").strip().lower()
            if key == "0": hm.close(); return "back"
            elif key == "c":
                c = _input("Type 'yes' to delete all history:").strip().lower()
                if c == "yes":
                    import sqlite3
                    conn = sqlite3.connect(str(hm._path))
                    conn.execute("DELETE FROM downloads"); conn.commit(); conn.close()
                    success("History cleared.")

    # ==================================================================
    # 6. QUEUE
    # ==================================================================
    def queue_screen(self) -> str:
        from .queue_manager import DownloadQueue, QueueItemStatus
        q = DownloadQueue()
        colors = {QueueItemStatus.PENDING: "yellow", QueueItemStatus.DOWNLOADING: "cyan",
                  QueueItemStatus.COMPLETED: "green", QueueItemStatus.FAILED: "red",
                  QueueItemStatus.CANCELLED: "dim", QueueItemStatus.PAUSED: "yellow"}
        while True:
            _clear()
            console.print("\n  [bold cyan]Download Queue[/bold cyan]")
            items = q.items
            if not items:
                console.print("  [dim]Empty. Add from Download or Followed screens.[/dim]")
            else:
                pn = sum(1 for i in items if i.status == QueueItemStatus.PENDING)
                cp = sum(1 for i in items if i.status == QueueItemStatus.COMPLETED)
                console.print(f"  [dim]{len(items)} total  |  {pn} pending  |  {cp} completed[/dim]")
                console.print()
                for i, item in enumerate(items, 1):
                    c = colors.get(item.status, "white")
                    t = (item.manga_title or item.url.rsplit("/",1)[-1] if "/" in item.url else item.url)[:35]
                    console.print(f"  {i:2d}. [{c}]{t:<37}[/{c}] [{c}]{item.status.value.upper()}[/{c}]")
            if q.is_paused:
                console.print("\n  [bold yellow]PAUSED[/bold yellow]")
            console.print()
            console.print("  [dim]A=Add  P=Pause  R=Resume  C=Cancel  0=Back[/dim]")
            key = _input("Action:").strip().lower()
            if key == "0": return "back"
            elif key == "a":
                u = _input("Manga URL:")
                if u.startswith("http"): q.add(u); success("Added.")
            elif key == "p": q.pause()
            elif key == "r": q.resume()
            elif key == "c":
                try:
                    idx = int(_input("Cancel number:"))
                    if 1 <= idx <= len(items): q.cancel(idx-1); success(f"Cancelled {idx}.")
                except ValueError: pass

    # ==================================================================
    # 7. SETTINGS
    # ==================================================================
    def settings_screen(self, config) -> str:
        while True:
            _clear()
            console.print("\n  [bold cyan]Settings[/bold cyan]")
            console.print()
            console.print(f"  1. Output Directory:    [dim]{config.download_dir}[/dim]")
            console.print(f"  2. Export Format:       [dim]{config.default_output_format.upper()}[/dim]")
            console.print(f"  3. Concurrent Downloads:[dim]{config.download.concurrent_downloads}[/dim]")
            console.print(f"  0. Back")
            console.print()
            console.print("  [dim]Browser runs headful (Cloudflare requires it).[/dim]")
            key = _input("Select:").strip()
            if key == "0": return "back"
            elif key == "1":
                d = _input("New directory:")
                if d: config.download_dir = d; success("Updated.")
            elif key == "2":
                f = _input("Format (cbz/pdf/both):").strip().lower()
                if f in ("cbz","pdf","both"): config.default_output_format = f; success("Updated.")
            elif key == "3":
                try:
                    n = int(_input("1-20:"))
                    if 1 <= n <= 20: config.download.concurrent_downloads = n; success("Updated.")
                except ValueError: pass
