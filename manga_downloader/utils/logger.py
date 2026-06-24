"""Logging utility using Rich for beautiful console output."""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Any, TextIO

from rich.console import Console, Group
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich.tree import Tree

_theme = Theme({
    "info": "cyan",
    "success": "green",
    "warning": "yellow",
    "error": "red bold",
    "chapter": "blue bold",
    "manga": "magenta bold",
    "speed": "green",
    "count": "white bold",
    "dim": "dim",
    "highlight": "bold yellow",
    "subtitle": "bold cyan",
})

console: Console = Console(theme=_theme, force_terminal=True)
_error_console: Console = Console(stderr=True, theme=_theme, force_terminal=True)


def info(message: str) -> None:
    """Log an informational message."""
    console.print(f"  [info]i[/info] {message}")


def success(message: str) -> None:
    """Log a success message."""
    console.print(f"  [success]+[/success] {message}")


def warning(message: str) -> None:
    """Log a warning message."""
    console.print(f"  [warning]![/warning] {message}")


def error(message: str) -> None:
    """Log an error message."""
    _error_console.print(f"  [error]x[/error] {message}")


def chapter_info(number: str, message: str) -> None:
    """Log chapter-specific information."""
    console.print(f"  [chapter]Ch {number}[/chapter]: {message}")


def manga_info(title: str) -> None:
    """Display manga title banner."""
    panel = Panel(
        f"[manga]{title}[/manga]",
        border_style="magenta",
        padding=(1, 4),
    )
    console.print(panel)


def startup_banner(version: str) -> None:
    """Display the startup banner with version info."""
    banner = Panel(
        Group(
            Text("Manga Starz Downloader", style="bold white", justify="center"),
            Text(f"Version {version}", style="dim", justify="center"),
        ),
        border_style="bold magenta",
        padding=(1, 2),
        width=44,
    )
    console.print()
    console.print(banner)
    console.print()


def display_chapter_list(chapters: list[tuple[str, str]], total: int) -> None:
    """Display a formatted chapter list.

    Args:
        chapters: List of (number_display, title) tuples.
        total: Total number of chapters.
    """
    table = Table(
        title=f"Available Chapters: [count]{total}[/count]",
        title_style="bold white",
        border_style="blue",
        header_style="bold cyan",
        show_lines=False,
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=5, justify="right")
    table.add_column("Chapter", style="chapter", width=16)
    table.add_column("Title", style="white")

    for i, (num, title) in enumerate(chapters, 1):
        table.add_row(str(i), num, title)

    console.print()
    console.print(table)


def selection_menu(title: str, options: list[tuple[str, str, str]]) -> None:
    """Display a Rich-styled selection menu.

    Args:
        title: Menu title.
        options: List of (key, label, description) tuples.
    """
    table = Table(
        title=title,
        title_style="bold white",
        border_style="cyan",
        header_style="bold cyan",
        show_header=False,
        padding=(0, 1),
    )
    table.add_column("Key", style="highlight", width=6)
    table.add_column("Option", style="bold white")
    table.add_column("Description", style="dim")

    for key, label, desc in options:
        table.add_row(f"  {key}", label, desc)

    console.print()
    console.print(table)
    console.print()


def format_menu(title: str, options: list[str], default: str = "1") -> None:
    """Display a simple format selection menu.

    Args:
        title: Menu title.
        options: List of option strings.
        default: Default option key.
    """
    console.print()
    console.print(f"[bold white]{title}[/bold white]")
    for i, opt in enumerate(options, 1):
        marker = f"[highlight](default)[/highlight]" if str(i) == default else ""
        console.print(f"  [highlight]{i}.[/highlight] {opt} {marker}")
    console.print()


def _separator(style: str = "bold magenta") -> None:
    """Print an ASCII separator line."""
    console.print()
    console.print("=" * 42, style=style)
    console.print()


def download_summary(stats: Any) -> None:
    """Display a comprehensive download summary.

    Args:
        stats: DownloadStats instance with all download metrics.
    """
    _separator("bold magenta")

    header = Panel(
        "[bold white]Download Complete[/bold white]",
        border_style="bold green",
        padding=(0, 2),
        width=36,
    )
    console.print(header)
    console.print()

    info_table = Table(
        show_header=False,
        border_style="dim",
        padding=(0, 2),
        expand=False,
    )
    info_table.add_column("Label", style="bold cyan", width=20)
    info_table.add_column("Value", style="white")

    info_table.add_row("Manga", f"[bold]{stats.manga_title}[/bold]")
    info_table.add_row("Chapters Processed", str(stats.total_chapters))
    info_table.add_row("Chapters Succeeded", f"[success]{stats.completed_chapters}[/success]")
    if stats.failed_chapters > 0:
        info_table.add_row("Chapters Failed", f"[error]{stats.failed_chapters}[/error]")
    info_table.add_row("Images Downloaded", str(stats.downloaded_images))
    if stats.failed_images > 0:
        info_table.add_row("Failed Images", f"[warning]{stats.failed_images}[/warning]")
    info_table.add_row("Total Size", f"[highlight]{stats.total_size_display}[/highlight]")
    info_table.add_row("Elapsed Time", f"[highlight]{stats.elapsed_display}[/highlight]")

    console.print(info_table)

    if stats.output_files:
        console.print()
        output_tree = Tree(f"[bold]Output:[/bold] [dim]downloads/{stats.manga_slug}/[/dim]")
        for f in stats.output_files[:20]:
            output_tree.add(f"[dim]{f}[/dim]")
        if len(stats.output_files) > 20:
            output_tree.add(f"[dim]... and {len(stats.output_files) - 20} more files[/dim]")
        console.print(output_tree)

    if stats.errors:
        console.print()
        console.print("[bold red]Errors encountered:[/bold red]")
        for err in stats.errors[:10]:
            console.print(f"  [dim]-[/dim] {err}")
        if len(stats.errors) > 10:
            console.print(f"  [dim]... and {len(stats.errors) - 10} more errors[/dim]")

    console.print()
    _separator("bold magenta")


def create_progress() -> Progress:
    """Create a Rich Progress bar for downloads."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


def write_log(file: TextIO, message: str) -> None:
    """Write a timestamped log message to a file."""
    timestamp = datetime.now().isoformat()
    file.write(f"[{timestamp}] {message}\n")
    file.flush()
