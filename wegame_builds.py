"""Convert public WeGame PoE2 share links to PoB2 codes through PoBR."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlsplit

from .analysis_input import classify_analysis_input
from .builds import normalize_visible_pob_code

LOGGER = logging.getLogger(__name__)
POBR_WEB_URL = "https://pobr-web.pages.dev/"
_POBR_HOST = "pobr-web.pages.dev"
_DEFAULT_TIMEOUT_MS = 45_000


class WeGameBuildError(RuntimeError):
    """A public WeGame share could not be converted to a PoB2 build."""


class WeGameBuildCodeResolver:
    """Use PoBR's public importer and browser-side exporter in an isolated context."""

    def __init__(self, timeout_seconds: int = 15):
        self.timeout_ms = max(_DEFAULT_TIMEOUT_MS, min(int(timeout_seconds) * 2_000, 60_000))
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._lock = asyncio.Lock()

    async def convert(self, share_url: str) -> str:
        try:
            parsed = classify_analysis_input(share_url)
        except ValueError as exc:
            raise WeGameBuildError(str(exc)) from exc
        if parsed.kind != "wegame":
            raise WeGameBuildError("请提供有效的 WeGame 分享链接。")

        async with self._lock:
            context = None
            try:
                browser = await self._get_browser()
                context = await browser.new_context(locale="en-US")
                page = await context.new_page()
                page.set_default_timeout(self.timeout_ms)
                await page.route("**/*", self._route_pobr_page)
                await page.goto(
                    POBR_WEB_URL,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )

                dismiss = page.get_by_role("button", name=re.compile(r"^got it$", re.I))
                if await dismiss.count() and await dismiss.first.is_visible():
                    await dismiss.first.click()

                input_box = page.locator("textarea.import-code").first
                await input_box.wait_for(state="visible")
                await input_box.fill(parsed.value)

                import_button = page.get_by_role(
                    "button", name=re.compile(r"import as new build", re.I)
                )
                await import_button.wait_for(state="visible")
                await import_button.click()
                await page.locator(".import-summary").wait_for(state="visible")

                generate_button = page.get_by_role(
                    "button", name=re.compile(r"generate share code", re.I)
                )
                await generate_button.wait_for(state="visible")
                await generate_button.click()

                await page.wait_for_function(
                    """() => Array.from(document.querySelectorAll('textarea'))
                        .some((node) => /^[A-Za-z0-9_+/=-]{80,1000000}$/
                            .test((node.value || '').replace(/\\s+/g, '')))""",
                    timeout=self.timeout_ms,
                )
                raw_codes = await page.locator("textarea").evaluate_all(
                    "nodes => nodes.map(node => node.value || '')"
                )
                for raw_code in reversed(raw_codes):
                    code = normalize_visible_pob_code(raw_code)
                    if code:
                        return code
                raise WeGameBuildError("PoBR 页面没有生成有效的 PoB2 导入码。")
            except WeGameBuildError:
                raise
            except Exception as exc:
                LOGGER.warning(
                    "WeGame to PoB2 conversion through PoBR failed (%s)",
                    type(exc).__name__,
                )
                raise WeGameBuildError(
                    "PoBR 转换页面没有完成导入。请确认 Chromium 已安装、网络可访问 PoBR/WeGame，"
                    "并检查 WeGame 分享链接是否仍有效。"
                ) from exc
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        LOGGER.debug("Unable to close PoBR browser context", exc_info=True)

    async def close(self) -> None:
        async with self._lock:
            browser, playwright = self._browser, self._playwright
            self._browser = None
            self._playwright = None
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    LOGGER.debug("Unable to close PoBR browser", exc_info=True)
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    LOGGER.debug("Unable to stop PoBR Playwright", exc_info=True)

    async def _get_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browser = None
        self._playwright = None

        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(headless=True)
        except Exception:
            await self._playwright.stop()
            self._playwright = None
            raise
        return self._browser

    @staticmethod
    async def _route_pobr_page(route) -> None:
        request = route.request
        parts = urlsplit(request.url)
        if parts.scheme != "https" or parts.hostname != _POBR_HOST:
            await route.abort()
        elif request.resource_type in {"image", "font", "media"}:
            await route.abort()
        else:
            await route.continue_()
