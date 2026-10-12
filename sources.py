"""Bounded readers for PoE2Wiki, RePoE, and pinned poe-game-data files."""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup

from .poe2db import Source

REPOE_BASE = "https://repoe-fork.github.io/poe2/"
REPOE_FILES = ("skill_gems.min.json", "keywords.min.json", "uniques.min.json")
GAME_DATA_REF = "v2026.10.13"
GAME_DATA_PATHS = (
    "skills/json/ActiveSkills.json",
    "skills/json/SupportGems.json",
    "uniques/json/uniques.json",
)
GAME_DATA_RAW = (
    "https://raw.githubusercontent.com/seominugi/poe-game-data/"
    f"{GAME_DATA_REF}/poe2/"
)
GAME_DATA_VIEW = (
    "https://github.com/seominugi/poe-game-data/blob/"
    f"{GAME_DATA_REF}/poe2/"
)
WIKI_API_URLS = (
    "https://www.poe2wiki.net/w/api.php",
    "https://www.poe2wiki.net/api.php",
)
WIKI_PAGE_BASE = "https://www.poe2wiki.net/wiki/"
USER_AGENT = "AstrBot-PoE2DB/1.2 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)"
MAX_JSON_BYTES = 2_500_000
MAX_HTML_BYTES = 3_000_000


class ExternalSourceError(RuntimeError):
    """A configured source could not be read or returned unexpected content."""


def normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", value)


def _short_text(value: object, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[: max(0, max_chars)]


class _AllowedRedirects(HTTPRedirectHandler):
    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allowed(newurl):
            raise ExternalSourceError("资料站点跳转到了指定来源范围以外")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ExternalSourcesClient:
    def __init__(self, timeout_seconds: int = 15, cache_seconds: int = 900):
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, Any]] = {}
        self._semaphore = asyncio.Semaphore(4)

    @staticmethod
    def _allowed(url: str) -> bool:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            return False
        if (
            parts.scheme != "https"
            or parts.username is not None
            or parts.password is not None
            or port is not None
        ):
            return False
        if parts.hostname == "repoe-fork.github.io":
            return parts.path in {f"/poe2/{name}" for name in REPOE_FILES}
        if parts.hostname == "raw.githubusercontent.com":
            return parts.path in {
                f"/seominugi/poe-game-data/{GAME_DATA_REF}/poe2/{path}"
                for path in GAME_DATA_PATHS
            }
        if parts.hostname == "www.poe2wiki.net":
            if parts.path in ("/w/api.php", "/api.php"):
                return True
            path = unquote(parts.path)
            if not path.startswith("/wiki/") or "\\" in path or parts.query:
                return False
            return all(part not in ("", ".", "..") for part in path.split("/")[2:])
        return False

    def _download_sync(self, url: str, kind: str) -> Any:
        if not self._allowed(url):
            raise ExternalSourceError("来源网址不在允许范围内")
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json" if kind == "json" else "text/html",
            },
        )
        opener = build_opener(_AllowedRedirects(self._allowed))
        limit = MAX_JSON_BYTES if kind == "json" else MAX_HTML_BYTES
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                if not self._allowed(response.geturl()):
                    raise ExternalSourceError("资料站点跳转到了指定来源范围以外")
                media_type = response.headers.get_content_type()
                expected = ("application/json", "text/plain") if kind == "json" else ("text/html",)
                if media_type not in expected:
                    raise ExternalSourceError("资料站点返回了非预期格式")
                data = response.read(limit + 1)
        except HTTPError as exc:
            raise ExternalSourceError(f"资料站点返回 HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ExternalSourceError("连接资料站点失败或超时") from exc
        if len(data) > limit:
            raise ExternalSourceError("资料文件过大，已停止读取")
        decoded = data.decode("utf-8", errors="replace")
        if kind == "html":
            return decoded
        try:
            return json.loads(decoded)
        except ValueError as exc:
            raise ExternalSourceError("资料站点没有返回有效 JSON") from exc

    async def _fetch(self, url: str, kind: str) -> Any:
        cached = self._cache.get(url)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        async with self._semaphore:
            cached = self._cache.get(url)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            value = await asyncio.to_thread(self._download_sync, url, kind)
            if self.cache_seconds > 0:
                self._cache[url] = (time.monotonic() + self.cache_seconds, value)
                if len(self._cache) > 32:
                    self._cache.pop(next(iter(self._cache)))
            return value

    @staticmethod
    def _match(record_name: object, names: set[str]) -> bool:
        return isinstance(record_name, str) and normalize_name(record_name) in names

    async def find_repoe(
        self, names: list[str], max_sources: int = 2, max_chars: int = 2500
    ) -> list[Source]:
        if max_sources <= 0:
            return []
        fetched = await asyncio.gather(
            *(self._fetch(REPOE_BASE + name, "json") for name in REPOE_FILES),
            return_exceptions=True,
        )
        datasets = {
            filename: data
            for filename, data in zip(REPOE_FILES, fetched)
            if isinstance(data, dict)
        }
        if not datasets:
            raise ExternalSourceError("RePoE 数据文件暂时无法读取")

        results: list[Source] = []
        used: set[tuple[str, str]] = set()
        for name in names[:18]:
            wanted = {normalize_name(name)}
            for filename in REPOE_FILES:
                for record_id, record in datasets.get(filename, {}).items():
                    if not isinstance(record, dict):
                        continue
                    if filename == "skill_gems.min.json":
                        item = record.get("base_item") or {}
                        if not isinstance(item, dict) or item.get("release_state") != "released":
                            continue
                        display = item.get("display_name")
                        if not self._match(display, wanted):
                            continue
                        details = (
                            f"RePoE 宝石记录 ID：{record_id}。英文名称：{display}。"
                            f"类型：{record.get('gem_type')}。标签：{', '.join(record.get('tags') or [])}。"
                            f"授予技能 ID：{', '.join(record.get('grants_skills') or [])}。"
                        )
                    elif filename == "keywords.min.json":
                        display = record.get("term")
                        if not self._match(display, wanted):
                            continue
                        definition = _short_text(record.get("definition"), max_chars - 100)
                        if not definition:
                            continue
                        details = f"RePoE 机制词条 ID：{record_id}。英文名称：{display}。定义：{definition}"
                    else:
                        display = record.get("name")
                        if not self._match(display, wanted):
                            continue
                        details = (
                            f"RePoE 暗金物品索引 ID：{record.get('id') or record_id}。"
                            f"英文名称：{display}。物品类别：{record.get('item_class')}。"
                            "此索引不提供词缀。"
                        )
                    key = (filename, normalize_name(str(display)))
                    if key in used:
                        continue
                    used.add(key)
                    results.append(
                        Source(
                            title=f"RePoE：{display}",
                            url=REPOE_BASE + filename,
                            text=details[:max_chars],
                        )
                    )
                    if len(results) >= max_sources:
                        return results
        return results

    @staticmethod
    def _game_data_text(path: str, record: dict[str, Any], max_chars: int) -> str:
        names = record.get("name") or {}
        if not isinstance(names, dict):
            return ""
        prefix = (
            f"poe-game-data {GAME_DATA_REF}；记录 ID：{record.get('id')}。"
            f"繁体名称：{names.get('tw')}；英文名称：{names.get('en')}。"
        )
        if path.endswith("ActiveSkills.json"):
            descriptions = record.get("description") or {}
            prefix += f"技能效果 ID：{record.get('grantedEffect')}。"
            category = "主动技能"
        elif path.endswith("SupportGems.json"):
            descriptions = record.get("supportText") or {}
            prefix += f"辅助效果 ID：{record.get('effectId')}。"
            category = "辅助宝石"
        else:
            base_item = record.get("baseItem") or {}
            category_names = base_item.get("category") if isinstance(base_item, dict) else {}
            if not isinstance(category_names, dict):
                category_names = {}
            prefix += (
                f"物品类别：{category_names.get('tw') or category_names.get('en')}。"
                "此数据集未提供该暗金物品的词缀。"
            )
            descriptions = {}
            category = "暗金物品"
        if isinstance(descriptions, dict):
            prefix += (
                f"繁体说明：{_short_text(descriptions.get('tw'), max_chars // 2)}。"
                f"英文说明：{_short_text(descriptions.get('en'), max_chars // 2)}。"
            )
        return f"{category}。{prefix}"[:max_chars]

    async def find_game_data(
        self, names: list[str], max_sources: int = 2, max_chars: int = 2500
    ) -> list[Source]:
        if max_sources <= 0:
            return []
        fetched = await asyncio.gather(
            *(self._fetch(GAME_DATA_RAW + path, "json") for path in GAME_DATA_PATHS),
            return_exceptions=True,
        )
        datasets = {
            path: records
            for path, records in zip(GAME_DATA_PATHS, fetched)
            if isinstance(records, list)
        }
        if not datasets:
            raise ExternalSourceError("poe-game-data 数据文件暂时无法读取")
        results: list[Source] = []
        used: set[tuple[str, str]] = set()
        for name in names[:18]:
            wanted = {normalize_name(name)}
            for path in GAME_DATA_PATHS:
                for record in datasets.get(path, []):
                    if not isinstance(record, dict):
                        continue
                    record_names = record.get("name") or {}
                    if not isinstance(record_names, dict) or not any(
                        self._match(record_names.get(locale), wanted)
                        for locale in ("tw", "en")
                    ):
                        continue
                    display = record_names.get("tw") or record_names.get("en")
                    key = (path, normalize_name(str(display)))
                    if key in used:
                        continue
                    used.add(key)
                    text = self._game_data_text(path, record, max_chars)
                    if not text:
                        continue
                    results.append(
                        Source(
                            title=f"poe-game-data：{display}",
                            url=GAME_DATA_VIEW + path,
                            text=text,
                        )
                    )
                    if len(results) >= max_sources:
                        return results
        return results

    async def _wiki_api(self, params: dict[str, object]) -> dict[str, Any]:
        last_error: ExternalSourceError | None = None
        for base in WIKI_API_URLS:
            try:
                result = await self._fetch(base + "?" + urlencode(params), "json")
                if isinstance(result, dict):
                    if "error" in result:
                        raise ExternalSourceError("PoE2Wiki API 返回错误")
                    return result
                raise ExternalSourceError("PoE2Wiki API 返回的数据格式不正确")
            except ExternalSourceError as exc:
                last_error = exc
        raise last_error or ExternalSourceError("PoE2Wiki API 暂时无法读取")

    @staticmethod
    def _wiki_page_text(html: str, max_chars: int) -> str:
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
        if any(marker in title for marker in ("making sure you're not a bot", "just a moment", "attention required")):
            raise ExternalSourceError("PoE2Wiki 要求浏览器验证")
        root = soup.select_one("#mw-content-text .mw-parser-output")
        if root is None:
            raise ExternalSourceError("PoE2Wiki 页面缺少词条正文")
        for node in root.select(
            "script, style, noscript, nav, .toc, .mw-editsection, .navbox, "
            ".mw-references-wrap, .reflist, sup.reference"
        ):
            node.decompose()
        for table in root.select("table"):
            if len(table.select("tr")) > 20:
                table.decompose()
        return _short_text(root.get_text(" ", strip=True), max_chars)

    async def find_wiki(
        self, names: list[str], max_sources: int = 2, max_chars: int = 3500
    ) -> list[Source]:
        if max_sources <= 0:
            return []
        results: list[Source] = []
        used: set[str] = set()
        queried = 0
        errors = 0
        for name in names[:18]:
            if not re.search(r"[A-Za-z]", name):
                continue
            try:
                search = await self._wiki_api(
                    {
                        "action": "query",
                        "format": "json",
                        "formatversion": 2,
                        "list": "search",
                        "srsearch": name[:40],
                        "srnamespace": 0,
                        "srlimit": 5,
                    }
                )
            except ExternalSourceError:
                errors += 1
                continue
            queried += 1
            hits = (search.get("query") or {}).get("search") or []
            wanted = normalize_name(name)
            exact = [hit for hit in hits if normalize_name(str(hit.get("title", ""))) == wanted]
            candidates = exact or [
                hit
                for hit in hits
                if len(wanted) >= 4 and wanted in normalize_name(str(hit.get("title", "")))
            ][:1]
            for hit in candidates[:1]:
                title = hit.get("title")
                if not isinstance(title, str) or not title or title.casefold() in used:
                    continue
                page_url = WIKI_PAGE_BASE + quote(title.replace(" ", "_"), safe="()")
                try:
                    text = self._wiki_page_text(await self._fetch(page_url, "html"), max_chars)
                except ExternalSourceError:
                    text = ""
                if not text:
                    try:
                        details = await self._wiki_api(
                            {
                                "action": "query",
                                "format": "json",
                                "formatversion": 2,
                                "titles": title,
                                "prop": "extracts",
                                "explaintext": 1,
                                "exchars": 1200,
                            }
                        )
                        pages = (details.get("query") or {}).get("pages") or []
                        text = _short_text(pages[0].get("extract"), max_chars) if pages else ""
                    except ExternalSourceError:
                        continue
                if text:
                    used.add(title.casefold())
                    results.append(Source(title=f"PoE2Wiki：{title}", url=page_url, text=text))
                    if len(results) >= max_sources:
                        return results
        if errors and not queried and not results:
            raise ExternalSourceError("PoE2Wiki 暂时无法读取")
        return results

    async def find_sources(self, names: list[str]) -> tuple[dict[str, list[Source]], list[str]]:
        lookups = (
            ("poe-game-data", self.find_game_data(names)),
            ("PoE2Wiki", self.find_wiki(names)),
            ("RePoE", self.find_repoe(names)),
        )
        fetched = await asyncio.gather(*(task for _, task in lookups), return_exceptions=True)
        sources: dict[str, list[Source]] = {}
        failures: list[str] = []
        for (site, _), result in zip(lookups, fetched):
            if isinstance(result, Exception):
                failures.append(site)
                sources[site] = []
            else:
                sources[site] = result
        return sources, failures
