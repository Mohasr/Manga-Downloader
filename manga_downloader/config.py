"""Configuration management for Manga Downloader."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


@dataclass
class DownloadConfig:
    concurrent_downloads: int = 5
    max_retries: int = 5
    retry_delay: float = 2.0
    request_timeout: int = 60
    chunk_size: int = 8192
    resume_downloads: bool = True


@dataclass
class ExportConfig:
    pdf_quality: int = 95
    pdf_max_image_size: int = 100_000_000
    cbz_compression: int = 8
    skip_corrupted_images: bool = True


@dataclass
class BrowserConfig:
    headless: bool = False
    chrome_channel: str = "chrome"
    browser_backend: str = "playwright"
    kameleo_port: int = 5050
    kameleo_executable: str = ""
    viewport_width: int = 1920
    viewport_height: int = 1080
    timeout: int = 120_000
    cloudflare_wait_timeout: int = 120_000
    cloudflare_poll_interval: float = 2.0


@dataclass
class AppConfig:
    download: DownloadConfig = field(default_factory=DownloadConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    profile_dir: str = "browser_profile"
    progress_file: str = "progress.json"
    download_dir: str = "downloads"
    cache_dir: str = "cache"
    default_output_format: str = "cbz"
    log_level: str = "INFO"

    _instance: AppConfig | None = field(default=None, init=False, repr=False)

    @classmethod
    def get_instance(cls) -> AppConfig:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def load_from_file(cls, path: str | Path) -> AppConfig:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "rb") as f:
            raw: dict[str, Any] = tomllib.load(f)

        config = cls()

        if "download" in raw:
            dl = raw["download"]
            config.download = DownloadConfig(
                concurrent_downloads=dl.get("concurrent_downloads", 5),
                max_retries=dl.get("max_retries", 5),
                retry_delay=dl.get("retry_delay", 2.0),
                request_timeout=dl.get("request_timeout", 60),
                chunk_size=dl.get("chunk_size", 8192),
                resume_downloads=dl.get("resume_downloads", True),
            )

        if "export" in raw:
            ex = raw["export"]
            config.export = ExportConfig(
                pdf_quality=ex.get("pdf_quality", 95),
                pdf_max_image_size=ex.get("pdf_max_image_size", 100_000_000),
                cbz_compression=ex.get("cbz_compression", 8),
                skip_corrupted_images=ex.get("skip_corrupted_images", True),
            )

        if "browser" in raw:
            br = raw["browser"]
            config.browser = BrowserConfig(
                headless=br.get("headless", False),
                chrome_channel=br.get("chrome_channel", "chrome"),
                browser_backend=br.get("browser_backend", "playwright"),
                kameleo_port=br.get("kameleo_port", 5050),
                kameleo_executable=br.get("kameleo_executable", ""),
                viewport_width=br.get("viewport_width", 1920),
                viewport_height=br.get("viewport_height", 1080),
                timeout=br.get("timeout", 120_000),
                cloudflare_wait_timeout=br.get("cloudflare_wait_timeout", 120_000),
                cloudflare_poll_interval=br.get("cloudflare_poll_interval", 2.0),
            )

        config.profile_dir = raw.get("profile_dir", config.profile_dir)
        config.progress_file = raw.get("progress_file", config.progress_file)
        config.download_dir = raw.get("download_dir", config.download_dir)
        config.cache_dir = raw.get("cache_dir", config.cache_dir)
        config.default_output_format = raw.get("default_output_format", config.default_output_format)
        config.log_level = raw.get("log_level", config.log_level)

        cls._instance = config
        return config

    @property
    def profile_path(self) -> Path:
        return Path(os.path.join(os.path.dirname(__file__), self.profile_dir))

    @property
    def progress_path(self) -> Path:
        return Path(self.cache_dir) / self.progress_file

    @property
    def download_path(self) -> Path:
        return Path(self.download_dir)

    @property
    def cache_path(self) -> Path:
        return Path(self.cache_dir)
