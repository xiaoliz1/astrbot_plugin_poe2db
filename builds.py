"""Public poe.ninja PoE2 Builds search and profile-link resolution."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urljoin, urlencode, urlsplit

LOGGER = logging.getLogger(__name__)
POE_NINJA_BASE_URL = "https://poe.ninja"
_RESULTS_TIMEOUT_MS = 20_000
_SUCCESS_CACHE_SECONDS = 300
_EMPTY_CACHE_SECONDS = 45


def _league_slug(league_name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", league_name.casefold())


def _normalized_name(name: str) -> str:
    return " ".join(unicodedata.normalize("NFC", name).split()).casefold()


def character_search_page_url(character_name: str, league_name: str) -> str:
    """Build a public Builds-page link filtered by the supplied character name."""
    query = character_name.strip()
    league_slug = _league_slug(league_name)
    if not query or len(query) > 100:
        raise ValueError("角色名为空或超过 100 个字符")
    if not league_slug:
        raise ValueError("当前赛季名称无效")
    # The public Builds page uses both search state and a name filter in shared links.
    params = urlencode({"i": "0", "search": f"name={query}", "name": query})
    return f"{POE_NINJA_BASE_URL}/poe2/builds/{league_slug}?{params}"


def character_profile_url_from_results(
    character_name: str,
    league_name: str,
    results: Iterable[Mapping[str, Any]],
) -> str | None:
    """Return a unique exact-match profile URL found in public page links.

    Only public anchor links from the rendered Builds page are accepted. This
    deliberately does not query poe.ninja's private Builds/Profile endpoints.
    """
    target = _normalized_name(character_name)
    league_slug = _league_slug(league_name)
    if not target or not league_slug:
        return None

    allowed_path_prefix = f"/poe2/builds/{league_slug}/character/"
    matches: set[str] = set()
    for result in results:
        if _normalized_name(str(result.get("text", ""))) != target:
            continue
        href = str(result.get("href", "")).strip()
        if not href:
            continue
        parsed = urlsplit(urljoin(POE_NINJA_BASE_URL, href))
        if (
            parsed.scheme != "https"
            or parsed.hostname != "poe.ninja"
            or not parsed.path.startswith(allowed_path_prefix)
        ):
            continue
        matches.add(parsed.geturl())

    return next(iter(matches)) if len(matches) == 1 else None


class PoeNinjaBuildsResolver:
    """Resolve profile links from poe.ninja's rendered public Builds page.

    Playwright and its Chromium binary are loaded lazily. If they are missing,
    the page cannot be reached, or the name is ambiguous, callers can use the
    ordinary filtered search link instead.
    """

    def __init__(self, timeout_seconds: int = 15):
        self.timeout_ms = max(5_000, min(int(timeout_seconds) * 1_000, 30_000))
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._lock = asyncio.Lock()
        self._cache: dict[tuple[str, str], tuple[float, str | None]] = {}

    async def resolve_character_url(
        self, character_name: str, league_name: str
    ) -> str | None:
        """Resolve a unique visible match; return ``None`` for safe fallback."""
        search_url = character_search_page_url(character_name, league_name)
        key = (_league_slug(league_name), _normalized_name(character_name))
        now = time.monotonic()

        async with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1]
            if cached:
                self._cache.pop(key, None)

            context = None
            try:
                browser = await self._get_browser()
                context = await browser.new_context(locale="en-US")
                page = await context.new_page()
                await page.route("**/*", self._route_public_page)
                page.set_default_timeout(self.timeout_ms)
                await page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                await page.wait_for_function(
                    """() => {
                        const body = document.body?.innerText || "";
                        const loadedCount = /\\bFound\\s+\\d+\\s+characters?\\b/i.test(body)
                            || /找到\\s*\\d+\\s*(个|位)?角色/.test(body)
                            || /找到\\s*\\d+\\s*個?角色/.test(body);
                        const resultLink = document.querySelector(
                            'a[href*="/poe2/builds/"][href*="/character/"]'
                        );
                        return loadedCount || Boolean(resultLink);
                    }""",
                    timeout=self.timeout_ms,
                )
                links = await page.locator(
                    'a[href*="/poe2/builds/"][href*="/character/"]'
                ).evaluate_all(
                    """anchors => anchors.map(anchor => ({
                        text: (anchor.innerText || anchor.textContent || "").trim(),
                        href: anchor.href
                    }))"""
                )
                resolved = character_profile_url_from_results(
                    character_name, league_name, links
                )
                cache_seconds = (
                    _SUCCESS_CACHE_SECONDS if resolved else _EMPTY_CACHE_SECONDS
                )
                self._cache[key] = (time.monotonic() + cache_seconds, resolved)
                self._trim_cache()
                return resolved
            except Exception as exc:
                # Browser setup, network, anti-bot, and page-layout failures all
                # use the command's public search-page fallback.
                LOGGER.warning(
                    "poe.ninja Builds page lookup failed; returning search link (%s)",
                    type(exc).__name__,
                )
                self._cache[key] = (
                    time.monotonic() + _EMPTY_CACHE_SECONDS,
                    None,
                )
                self._trim_cache()
                return None
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        LOGGER.debug("Unable to close poe.ninja browser context", exc_info=True)

    async def close(self) -> None:
        """Close the lazily started Chromium process during plugin unload."""
        async with self._lock:
            browser, playwright = self._browser, self._playwright
            self._browser = None
            self._playwright = None
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    LOGGER.debug("Unable to close poe.ninja browser", exc_info=True)
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    LOGGER.debug("Unable to stop Playwright", exc_info=True)

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
    async def _route_public_page(route) -> None:
        request = route.request
        hostname = urlsplit(request.url).hostname or ""
        if hostname != "poe.ninja" and not hostname.endswith(".poe.ninja"):
            await route.abort()
        elif request.resource_type in {"image", "font", "media"}:
            await route.abort()
        else:
            await route.continue_()

    def _trim_cache(self) -> None:
        now = time.monotonic()
        for key, (expires, _) in list(self._cache.items()):
            if expires <= now:
                self._cache.pop(key, None)
        while len(self._cache) > 128:
            self._cache.pop(next(iter(self._cache)))
