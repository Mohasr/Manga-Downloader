"""Kameleo browser backend — v5.0.0 Local API.

Launches browsers with anti-detect fingerprints via Kameleo.
Connects Playwright via CDP when available.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from .browser_manager import BrowserManager
from ..utils.logger import console, info, success, warning, error


class KameleoManager(BrowserManager):
    """Browser backend using Kameleo anti-detect browser."""

    PROVIDER = "Kameleo"

    _FP_DEVICE = "desktop"
    _FP_OS = "windows"
    _FP_BROWSER = "chrome"

    def __init__(self, profile_dir: str = "browser_profile", debug: bool = False,
                 kameleo_port: int = 5050, kameleo_executable: str = "") -> None:
        super().__init__(profile_dir, debug)
        self._kameleo_port = kameleo_port
        self._kameleo_exe = kameleo_executable
        self._profile_id: str = ""
        self._cdp_endpoint: str = ""
        self._browser_process: Any = None

    @property
    def profile_id(self) -> str:
        return self._profile_id

    @property
    def kameleo_port(self) -> int:
        return self._kameleo_port

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self._kameleo_port}"

    @property
    def cdp_endpoint(self) -> str:
        return self._cdp_endpoint

    def check_api_reachable(self) -> bool:
        try:
            resp = urllib.request.urlopen(f"{self.api_url}/general/healthcheck", timeout=5)
            return resp.status == 200
        except Exception:
            return False

    @staticmethod
    def _filter_fingerprints(fingerprints: list[Any]) -> list[Any]:
        filtered = []
        for fp in fingerprints:
            try:
                d = fp.to_dict() if hasattr(fp, "to_dict") else fp.__dict__
            except Exception:
                continue
            device = d.get("device", {})
            os_info = d.get("os", {})
            browser = d.get("browser", {})
            proxy = d.get("proxy", {})
            if isinstance(device, dict):
                dev_type = str(device.get("type", "")).lower()
            else:
                dev_type = str(getattr(fp, "device", "")).lower()
            if isinstance(os_info, dict):
                os_name = str(os_info.get("name", os_info.get("family", ""))).lower()
            else:
                os_name = ""
            if isinstance(browser, dict):
                b_name = str(browser.get("name", browser.get("product", ""))).lower()
                b_ver = browser.get("version", browser.get("majorVersion", ""))
            else:
                b_name, b_ver = "", "0"
            if isinstance(proxy, dict):
                proxy_type = str(proxy.get("type", proxy.get("mode", "none"))).lower()
                proxy_host = proxy.get("host", "")
            else:
                proxy_type, proxy_host = "none", ""
            if "desktop" in dev_type and "windows" in os_name and "chrome" in b_name:
                has_proxy = proxy_type != "none" or bool(proxy_host)
                filtered.append({
                    "obj": fp, "id": d.get("id", ""), "version": str(b_ver),
                    "proxy": has_proxy, "os": os_name, "browser": b_name,
                })
        filtered = [f for f in filtered if not f["proxy"]]
        filtered.sort(key=lambda x: _parse_version(x["version"]), reverse=True)
        return filtered

    async def start(self, headless: bool = False, mode: str = "unknown") -> tuple[Any, Any]:
        info(f"Kameleo API:  {self.api_url}")

        if not self.check_api_reachable():
            raise RuntimeError(
                f"Kameleo Local API not reachable at {self.api_url}\n"
                "Ensure Kameleo is running."
            )

        success("Kameleo API reachable")
        self._playwright = await async_playwright().start()

        try:
            from kameleo.local_api_client import KameleoLocalApiClient
            from kameleo.local_api_client.models import CreateProfileRequest as CPR
        except ImportError:
            raise RuntimeError(
                "Kameleo SDK not installed. Install with:  pip install kameleo-local-api-client"
            )

        client = KameleoLocalApiClient(endpoint=self.api_url)
        profile_id = self._load_profile_id()

        if profile_id:
            info(f"Reusing Kameleo profile: {profile_id}")
            try:
                client.profile.read_profile(profile_id)
            except Exception:
                warning("Saved profile not found on server, creating new one")
                profile_id = ""

        if not profile_id:
            info("Creating new Kameleo profile")
            info(f"Searching fingerprints: {self._FP_DEVICE} / {self._FP_OS} / {self._FP_BROWSER}")
            try:
                raw_fps = client.fingerprint.search_fingerprints(
                    device_type=self._FP_DEVICE, browser_product=self._FP_BROWSER,
                )
            except Exception as e:
                raise RuntimeError(f"Failed to fetch fingerprints: {e}") from e
            if not raw_fps:
                raise RuntimeError("No fingerprints returned by Kameleo")
            filtered = self._filter_fingerprints(raw_fps)
            if not filtered:
                raise RuntimeError(f"No fingerprints match criteria. Total returned: {len(raw_fps)}")
            best = filtered[0]
            info(f"Selected fingerprint: {best['browser']} v{best['version']}")
            body = CPR(fingerprint_id=best["id"])
            result = client.profile.create_profile(create_profile_request=body)
            profile_id = result.id
            self._save_profile_id(profile_id)

        self._profile_id = profile_id
        info(f"Profile ID: {profile_id}")

        info("Launching browser via Kameleo...")
        try:
            status = client.profile.start_profile(profile_id)
            info(f"Profile state: {status.lifetime_state}")
        except Exception as e:
            error_msg = str(e)
            if "already_running" in error_msg.lower():
                info("Profile was already running -- stopping and retrying...")
                try:
                    client.profile.stop_profile(profile_id)
                    await asyncio.sleep(2)
                    status = client.profile.start_profile(profile_id)
                    info(f"Profile state: {status.lifetime_state}")
                except Exception as e2:
                    raise RuntimeError(f"Kameleo failed to restart profile {profile_id}.\n{e2}") from e2
            else:
                error(f"start_profile failed: {error_msg}")
                try:
                    import requests as _r
                    raw = _r.post(f"{self.api_url}/profiles/{profile_id}/start", timeout=10)
                    error(f"Raw HTTP {raw.status_code}: {raw.text[:500]}")
                except Exception:
                    pass
                raise RuntimeError(f"Kameleo failed to start profile {profile_id}.\n{error_msg}") from e

        success("Kameleo browser launched")

        try:
            full_status = client.profile.get_profile_status(profile_id)
            self._cdp_endpoint = getattr(full_status, "cdp_endpoint", "") or ""
        except Exception:
            self._cdp_endpoint = ""

        if self._cdp_endpoint:
            info(f"CDP Endpoint: {self._cdp_endpoint[:80]}")
            try:
                browser = await self._playwright.chromium.connect_over_cdp(self._cdp_endpoint)
            except Exception as e:
                raise RuntimeError(f"CDP connection failed: {e}") from e
            contexts = browser.contexts
            if not contexts:
                await browser.close()
                raise RuntimeError("No browser contexts found after CDP connect")
            self._context = contexts[0]
            self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

            try:
                await asyncio.wait_for(
                    self._page.goto("about:blank", wait_until="domcontentloaded"), timeout=10
                )
            except (asyncio.TimeoutError, Exception):
                pass

            self._browser_ua = await self._page.evaluate("() => navigator.userAgent")
            self._browser_type = await self._page.evaluate(
                "() => { const ua = navigator.userAgent; "
                "return ua.includes('Chrome/') && !ua.includes('Chromium') ? 'Google Chrome' : "
                "(ua.includes('Chromium') ? 'Chromium' : ua.includes('Edge/') ? 'Edge' : 'Unknown'); }"
            )
            self._browser_version = await self._page.evaluate(
                "() => { const m = navigator.userAgent.match(/Chrome\\/(\\d+\\.\\d+\\.\\d+\\.\\d+)/); "
                "return m ? m[1] : 'unknown'; }"
            )
            cookie_count = len(await self._context.cookies())
            info(f"Browser:     {self._browser_type} {self._browser_version}")
            info(f"Cookies:     {cookie_count}")
        else:
            info("CDP not exposed by this Kameleo version — browser is running manually")
            self._context = None
            self._page = None

        return self._context, self._page

    async def close(self) -> None:
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            finally:
                self._page = None

        if self._profile_id:
            try:
                from kameleo.local_api_client import KameleoLocalApiClient
                client = KameleoLocalApiClient(endpoint=self.api_url)
                client.profile.stop_profile(self._profile_id)
                info("Kameleo profile stopped")
            except Exception:
                pass

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            finally:
                self._context = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            finally:
                self._playwright = None

        # Give transports time to flush before event loop ends
        await asyncio.sleep(0.5)

    async def get_headers(self) -> dict[str, str]:
        if self._page is None:
            return {}
        try:
            user_agent = await self._page.evaluate("() => navigator.userAgent")
        except Exception:
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        return {
            "User-Agent": user_agent, "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1", "Connection": "keep-alive",
            "Sec-Fetch-Dest": "image", "Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Site": "cross-site",
        }

    def provider_name(self) -> str:
        return self.PROVIDER

    def collect_fingerprint(self) -> dict[str, Any]:
        return {"backend": self.PROVIDER, "profile_id": self._profile_id,
                "kameleo_port": self._kameleo_port, "cdp_endpoint": self._cdp_endpoint}

    def _load_profile_id(self) -> str:
        cache_dir = Path(self.profile_dir).parent / "cache"
        profile_file = cache_dir / "kameleo_profile.json"
        if not profile_file.exists():
            return ""
        try:
            data = json.loads(profile_file.read_text())
            return data.get("profile_id", "")
        except Exception:
            return ""

    def _save_profile_id(self, profile_id: str) -> None:
        cache_dir = Path(self.profile_dir).parent / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "kameleo_profile.json").write_text(json.dumps({
            "profile_id": profile_id,
            "created": str(Path(self.profile_dir).stat().st_ctime if Path(self.profile_dir).exists() else 0),
            "browser_type": "chrome",
        }, indent=2))


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0,)
