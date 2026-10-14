"""Convert public WeGame PoE2 share links to PoB2 codes through PoBR."""

from __future__ import annotations

import asyncio
import json
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


def _normalize_empty_wegame_jewel_data(payload: Any) -> tuple[Any, bool]:
    """Represent an empty WeGame jewel collection as the JSON array PoBR expects."""
    if (
        isinstance(payload, dict)
        and payload.get("format") == "wegame"
        and isinstance(payload.get("jewel_data"), str)
        and not payload["jewel_data"].strip()
    ):
        normalized = dict(payload)
        normalized["jewel_data"] = "[]"
        return normalized, True
    return payload, False


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
            stage = "启动 Chromium"
            try:
                browser = await self._get_browser()
                context = await browser.new_context(locale="en-US")
                page = await context.new_page()
                page.set_default_timeout(self.timeout_ms)
                await page.route("**/*", self._route_pobr_page)
                stage = "打开 PoBR 页面"
                await page.goto(
                    POBR_WEB_URL,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )

                dismiss = page.get_by_role("button", name=re.compile(r"^got it$", re.I))
                if await dismiss.count() and await dismiss.first.is_visible():
                    await dismiss.first.click()

                stage = "导入 WeGame 构筑并返回构筑页"
                input_box = page.locator("textarea.import-code").first
                await input_box.wait_for(state="visible")
                await input_box.fill(parsed.value)

                import_button = page.locator(".import-submit")
                await import_button.wait_for(state="visible")
                await import_button.click()
                await self._return_to_build_panel(page)

                stage = "生成 PoB2 分享码"
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
                    "WeGame to PoB2 conversion through PoBR failed during %s (%s)",
                    stage,
                    type(exc).__name__,
                    exc_info=True,
                )
                if stage == "启动 Chromium":
                    if isinstance(exc, ModuleNotFoundError) and exc.name == "playwright":
                        message = "AstrBot 运行环境没有安装 Playwright，请重新安装插件依赖后再试。"
                    else:
                        message = "PoBR 的 Chromium 无法启动，请确认已在 AstrBot 运行环境安装 Chromium 后重试。"
                elif stage == "打开 PoBR 页面":
                    message = "无法打开 PoBR 转换页面，请检查 AstrBot 到 pobr-web.pages.dev 的网络连接。"
                elif stage == "导入 WeGame 构筑并返回构筑页":
                    message = (
                        "PoBR 没有完成 WeGame 构筑导入。请检查 AstrBot 到 PoBR 的网络连接，"
                        "并确认该分享链接仍可公开访问。"
                    )
                else:
                    message = "PoBR 已导入构筑，但生成 PoB2 分享码失败，请稍后重试。"
                raise WeGameBuildError(f"{message}（阶段：{stage}；错误：{type(exc).__name__}）") from exc
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        LOGGER.debug("Unable to close PoBR browser context", exc_info=True)

    @staticmethod
    async def _return_to_build_panel(page) -> None:
        """Wait for a completed import, surface PoBR errors, then reopen Build."""
        await page.wait_for_function(
            """() => {
                const activeTab = document.querySelector('.topbar-tab.is-active');
                const itemsActive = activeTab?.textContent?.trim().toLowerCase() === 'items';
                return itemsActive || Boolean(document.querySelector('.calc-error[role="alert"]'));
            }"""
        )

        import_error = page.locator('.calc-error[role="alert"]').first
        if await import_error.is_visible():
            message = (await import_error.inner_text()).strip()
            if message:
                raise WeGameBuildError(f"PoBR 导入失败：{message[:400]}")

        build_tab = page.get_by_role("button", name=re.compile(r"^build$", re.I))
        await build_tab.wait_for(state="visible")
        await build_tab.click()

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
        elif parts.path == "/api/import/wegame" and request.method == "POST":
            try:
                response = await route.fetch()
            except Exception:
                LOGGER.debug("Unable to inspect PoBR WeGame import response", exc_info=True)
                await route.continue_()
                return

            try:
                payload = await response.json()
            except Exception:
                await route.fulfill(response=response)
                return

            normalized, changed = _normalize_empty_wegame_jewel_data(payload)
            if not changed:
                await route.fulfill(response=response)
                return

            headers = await response.all_headers()
            for name in ("content-length", "content-encoding", "transfer-encoding"):
                headers.pop(name, None)
            headers["content-type"] = "application/json; charset=utf-8"
            await route.fulfill(
                status=response.status,
                headers=headers,
                body=json.dumps(normalized, ensure_ascii=False).encode("utf-8"),
            )
            LOGGER.debug("Normalized empty WeGame jewel data for PoBR import")
        else:
            await route.continue_()
