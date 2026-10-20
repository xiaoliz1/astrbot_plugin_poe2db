"""Public poe.ninja PoE2 Builds search and profile-link resolution."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import shutil
import time
import unicodedata
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlencode, urljoin, urlsplit

LOGGER = logging.getLogger(__name__)
POE_NINJA_BASE_URL = "https://poe.ninja"
_RESULTS_TIMEOUT_MS = 20_000
_SUCCESS_CACHE_SECONDS = 300
_EMPTY_CACHE_SECONDS = 45
_PROFILE_CACHE_SECONDS = 300
_POB_CODE_CACHE_SECONDS = 300
_PROFILE_TEXT_LIMIT = 12_000
_MAX_EQUIPMENT_TOOLTIPS = 36
_POB_CODE_SELECTOR = 'input[aria-label*="Import code for Path of Building" i]'
_MAX_POB_XML_BYTES = 8_000_000


@dataclass(frozen=True)
class UniqueBuildItem:
    """An equipped unique item found in the active PoB item/tree sets."""

    name: str
    is_jewel: bool


def parse_pob_unique_items(pob_code: str | None) -> list[UniqueBuildItem] | None:
    """Decode a PoB2 share code and return equipped unique items.

    ``None`` means the export could not be decoded or did not contain a usable
    active item set. An empty list means the export was valid and had no unique
    gear or socketed unique jewels in its active configuration.
    """
    if not isinstance(pob_code, str) or not pob_code.strip():
        return None
    code = "".join(pob_code.split())
    if len(code) > 1_000_000:
        return None

    try:
        padded = code + "=" * (-len(code) % 4)
        compressed = base64.b64decode(padded, altchars=b"-_", validate=True)
        xml_bytes = _bounded_pob_inflate(compressed)
        if xml_bytes is None:
            return None
        root = ET.fromstring(xml_bytes)
    except (ValueError, ET.ParseError, zlib.error):
        return None

    if root.tag.casefold() not in {"pathofbuilding2", "pathofbuilding"}:
        return None
    items_node = root.find("Items")
    if items_node is None:
        return None

    item_sets = items_node.findall("ItemSet")
    active_item_set_id = items_node.get("activeItemSet")
    if item_sets:
        active_item_set = next(
            (item_set for item_set in item_sets if item_set.get("id") == active_item_set_id),
            None,
        ) if active_item_set_id else item_sets[0]
        if active_item_set is None:
            return None
        equipped_ids = [
            slot.get("itemId", "").strip()
            for slot in active_item_set.findall(".//Slot")
            if slot.get("itemId", "").strip() not in {"", "0"}
        ]
    elif items_node.findall("Item"):
        return None
    else:
        return []

    # Passive-tree sockets store their jewel item IDs in the active Spec.
    tree_node = root.find("Tree")
    if tree_node is not None:
        specs = tree_node.findall("Spec")
        active_spec_id = tree_node.get("activeSpec")
        active_spec = next(
            (spec for spec in specs if spec.get("id") == active_spec_id), None
        ) if active_spec_id else (specs[0] if specs else None)
        if active_spec is not None:
            equipped_ids.extend(
                socket.get("itemId", "").strip()
                for socket in active_spec.findall(".//Socket")
                if socket.get("itemId", "").strip() not in {"", "0"}
            )

    item_by_id = {
        item.get("id", "").strip(): item
        for item in items_node.findall("Item")
        if item.get("id", "").strip()
    }
    results: list[UniqueBuildItem] = []
    seen_names: set[str] = set()
    for item_id in equipped_ids:
        item = item_by_id.get(item_id)
        if item is None:
            continue
        item_text = "".join(item.itertext())
        lines = [line.strip() for line in item_text.splitlines() if line.strip()]
        rarity_index = next(
            (
                index
                for index, line in enumerate(lines)
                if re.fullmatch(r"Rarity\s*:\s*Unique", line, re.IGNORECASE)
            ),
            None,
        )
        if rarity_index is None or rarity_index + 1 >= len(lines):
            continue
        name = lines[rarity_index + 1].strip()
        if not name or len(name) > 100 or "://" in name:
            continue
        name_key = name.casefold()
        if name_key in seen_names:
            continue
        item_class = ""
        for line in lines[: rarity_index + 3]:
            class_match = re.match(
                r"Item Class\s*:\s*(.+)$", line, re.IGNORECASE
            )
            if class_match:
                item_class = class_match.group(1).strip().casefold()
                break
        base_type = lines[rarity_index + 2] if rarity_index + 2 < len(lines) else ""
        is_jewel = "jewel" in item_class or "jewel" in base_type.casefold()
        results.append(UniqueBuildItem(name=name, is_jewel=is_jewel))
        seen_names.add(name_key)
    return results


def _bounded_pob_inflate(compressed: bytes) -> bytes | None:
    """Inflate an untrusted PoB payload with a strict decompressed-size cap."""
    for window_bits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            decoder = zlib.decompressobj(window_bits)
            result = decoder.decompress(compressed, _MAX_POB_XML_BYTES + 1)
            if (
                len(result) > _MAX_POB_XML_BYTES
                or decoder.unconsumed_tail
                or decoder.unused_data
                or not decoder.eof
            ):
                continue
            return result
        except zlib.error:
            continue
    return None


def _league_slug(league_name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", league_name.casefold())


def _find_system_chromium() -> str | None:
    """Return a system Chromium executable when Playwright's browser is absent."""
    for executable_name in (
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    ):
        executable_path = shutil.which(executable_name)
        if executable_path:
            return executable_path
    return None


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


def is_public_character_profile_url(profile_url: str, league_name: str) -> bool:
    """Accept only poe.ninja character pages for the requested PoE2 league."""
    league_slug = _league_slug(league_name)
    if not league_slug:
        return False
    try:
        parsed = urlsplit(profile_url.strip())
        port = parsed.port
    except (AttributeError, ValueError):
        return False

    prefix = f"/poe2/builds/{league_slug}/character/"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "poe.ninja"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith(prefix)
    ):
        return False

    path_parts = parsed.path[len(prefix) :].split("/")
    if len(path_parts) != 2 or not all(path_parts):
        return False
    decoded_parts = [unquote(part) for part in path_parts]
    return all(
        part not in {".", ".."} and "/" not in part and "\\" not in part
        for part in decoded_parts
    )


def normalize_visible_pob_code(value: str) -> str | None:
    """Validate a PoB share code read from poe.ninja's rendered public page."""
    if not isinstance(value, str):
        return None
    code = "".join(value.split()).strip("`")
    if not re.fullmatch(r"[A-Za-z0-9_+/=-]{80,1000000}", code):
        return None
    return code


def format_build_page_text(
    page_text: str,
    equipment_tooltips: Iterable[str],
    max_chars: int = _PROFILE_TEXT_LIMIT,
) -> str:
    """Normalize and bound visible public profile text sent to the chat model."""
    limit = max(100, int(max_chars))
    body = "\n".join(line.strip() for line in page_text.splitlines() if line.strip())
    tooltips: list[str] = []
    seen: set[str] = set()
    for tooltip in equipment_tooltips:
        cleaned = "\n".join(line.strip() for line in tooltip.splitlines() if line.strip())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            tooltips.append(cleaned)

    body_header = "页面公开信息：\n"
    equipment_header = "\n\n装备物品悬停信息：\n"
    if not tooltips:
        content = body_header + body
        if len(content) <= limit:
            return content
        marker = "\n[页面后续内容已省略]"
        return content[: max(0, limit - len(marker))].rstrip() + marker[:limit]

    tooltip_text = "\n\n".join(tooltips)
    available = max(0, limit - len(body_header) - len(equipment_header))
    body_budget = min(len(body), int(available * 0.6))
    body_chunk = body[:body_budget]
    if body_budget < len(body):
        body_chunk = body_chunk.rstrip() + "\n[页面后续内容已省略]"
    used = len(body_header) + len(body_chunk) + len(equipment_header)
    tooltip_budget = max(0, limit - used)
    tooltip_chunk = tooltip_text[:tooltip_budget]
    if tooltip_budget < len(tooltip_text) and tooltip_budget >= 18:
        marker = "\n[装备信息已截断]"
        tooltip_chunk = tooltip_chunk[: tooltip_budget - len(marker)].rstrip() + marker
    return (body_header + body_chunk + equipment_header + tooltip_chunk)[:limit]


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
        self._profile_cache: dict[str, tuple[float, str | None]] = {}
        self._pob_code_cache: dict[str, tuple[float, str | None]] = {}

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

    async def read_character_pob_code(
        self, profile_url: str, league_name: str
    ) -> str | None:
        """Read the visible PoB import code from a public character page."""
        if not is_public_character_profile_url(profile_url, league_name):
            return None

        cache_key = urlsplit(profile_url).geturl()
        now = time.monotonic()
        async with self._lock:
            cached = self._pob_code_cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]
            if cached:
                self._pob_code_cache.pop(cache_key, None)

            context = None
            try:
                browser = await self._get_browser()
                context = await browser.new_context(locale="en-US")
                page = await context.new_page()
                await page.route("**/*", self._route_public_page)
                page.set_default_timeout(self.timeout_ms)
                await page.goto(
                    profile_url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                await page.wait_for_function(
                    """selector => {
                        const input = document.querySelector(selector);
                        return Boolean(input && input.value && input.value.length >= 80);
                    }""",
                    arg=_POB_CODE_SELECTOR,
                    timeout=self.timeout_ms,
                )
                raw_code = await page.locator(_POB_CODE_SELECTOR).input_value()
                code = normalize_visible_pob_code(raw_code)
                self._pob_code_cache[cache_key] = (
                    time.monotonic()
                    + (_POB_CODE_CACHE_SECONDS if code else _EMPTY_CACHE_SECONDS),
                    code,
                )
                self._trim_pob_code_cache()
                return code
            except Exception as exc:
                LOGGER.warning(
                    "Unable to read visible PoB code from poe.ninja profile (%s)",
                    type(exc).__name__,
                )
                self._pob_code_cache[cache_key] = (
                    time.monotonic() + _EMPTY_CACHE_SECONDS,
                    None,
                )
                self._trim_pob_code_cache()
                return None
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        LOGGER.debug(
                            "Unable to close poe.ninja PoB context", exc_info=True
                        )

    async def read_character_build_text(
        self, profile_url: str, league_name: str
    ) -> str | None:
        """Read visible build details and item hover text from a public profile page.

        This follows the normal poe.ninja page in Chromium and reads rendered DOM
        text. It does not call poe.ninja's internal Builds/Profile endpoints.
        """
        if not is_public_character_profile_url(profile_url, league_name):
            return None

        cache_key = urlsplit(profile_url).geturl()
        now = time.monotonic()
        async with self._lock:
            cached = self._profile_cache.get(cache_key)
            if cached and cached[0] > now:
                return cached[1]
            if cached:
                self._profile_cache.pop(cache_key, None)

            context = None
            try:
                browser = await self._get_browser()
                context = await browser.new_context(locale="en-US")
                page = await context.new_page()
                await page.route("**/*", self._route_public_page)
                page.set_default_timeout(self.timeout_ms)
                await page.goto(
                    profile_url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                await page.wait_for_function(
                    """() => {
                        const text = document.body?.innerText || "";
                        const hasStats = /\\bStats\\b|统计|統計/i.test(text);
                        const hasSkills = /\\bSkills\\b|技能/i.test(text);
                        return text.length > 600 && hasStats && hasSkills;
                    }""",
                    timeout=self.timeout_ms,
                )

                page_text = await page.locator("body").inner_text()
                equipment_tooltips = await self._read_equipment_tooltips(page)
                result = format_build_page_text(
                    page_text,
                    equipment_tooltips,
                    max_chars=_PROFILE_TEXT_LIMIT,
                )
                self._profile_cache[cache_key] = (
                    time.monotonic() + _PROFILE_CACHE_SECONDS,
                    result or None,
                )
                self._trim_profile_cache()
                return result or None
            except Exception as exc:
                LOGGER.warning(
                    "poe.ninja public Builds profile read failed (%s)",
                    type(exc).__name__,
                )
                self._profile_cache[cache_key] = (
                    time.monotonic() + _EMPTY_CACHE_SECONDS,
                    None,
                )
                self._trim_profile_cache()
                return None
            finally:
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        LOGGER.debug(
                            "Unable to close poe.ninja profile context",
                            exc_info=True,
                        )

    async def _read_equipment_tooltips(self, page) -> list[str]:
        """Collect item text exposed by the public page's visible hover tooltips."""
        triggers = page.locator(
            "div[class*='_equipment_'] [data-tooltip-trigger='true']"
        )
        try:
            count = min(await triggers.count(), _MAX_EQUIPMENT_TOOLTIPS)
        except Exception:
            return []

        tooltip_locator = page.locator('[role="tooltip"]:visible')
        collected: list[str] = []
        seen: set[str] = set()
        for index in range(count):
            trigger = triggers.nth(index)
            try:
                has_item_visual = await trigger.evaluate(
                    """element => {
                        const rect = element.getBoundingClientRect();
                        const background = getComputedStyle(element).backgroundImage;
                        return rect.width > 0 && rect.height > 0 &&
                            (Boolean(element.querySelector('img')) || background !== 'none');
                    }"""
                )
                if not has_item_visual:
                    continue
                await trigger.hover(timeout=min(self.timeout_ms, 3_000))
                await page.wait_for_timeout(180)
                for tooltip in await tooltip_locator.all_inner_texts():
                    cleaned = "\n".join(
                        line.strip() for line in tooltip.splitlines() if line.strip()
                    )
                    if cleaned and cleaned not in seen:
                        seen.add(cleaned)
                        collected.append(cleaned)
            except Exception:
                # Empty slots and responsive overlays do not always expose a tooltip.
                continue
        return collected

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
            try:
                self._browser = await self._playwright.chromium.launch(headless=True)
            except Exception as bundled_error:
                system_chromium = _find_system_chromium()
                if not system_chromium:
                    raise
                LOGGER.warning(
                    "Playwright Chromium is unavailable; trying system Chromium (%s)",
                    type(bundled_error).__name__,
                )
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    executable_path=system_chromium,
                )
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

    def _trim_profile_cache(self) -> None:
        now = time.monotonic()
        for key, (expires, _) in list(self._profile_cache.items()):
            if expires <= now:
                self._profile_cache.pop(key, None)
        while len(self._profile_cache) > 64:
            self._profile_cache.pop(next(iter(self._profile_cache)))

    def _trim_pob_code_cache(self) -> None:
        now = time.monotonic()
        for key, (expires, _) in list(self._pob_code_cache.items()):
            if expires <= now:
                self._pob_code_cache.pop(key, None)
        while len(self._pob_code_cache) > 16:
            self._pob_code_cache.pop(next(iter(self._pob_code_cache)))
