"""Bounded PoE2Wiki reader and names derived from matched Poe2DB pages."""

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

WIKI_API_URLS = (
    "https://www.poe2wiki.net/w/api.php",
    "https://www.poe2wiki.net/api.php",
)
WIKI_PAGE_BASE = "https://www.poe2wiki.net/wiki/"
USER_AGENT = "AstrBot-PoE2DB/1.7.4 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)"
MAX_JSON_BYTES = 2_500_000
MAX_HTML_BYTES = 3_000_000


class WikiSourceError(RuntimeError):
    """PoE2Wiki could not be read or returned unexpected content."""


def _normalize_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", value)


def _short_text(value: object, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[: max(0, max_chars)]


def wiki_names_from_poe2db(sources: list[Source]) -> list[str]:
    """Use only English-looking slugs from already matched Poe2DB articles."""
    names: list[str] = []
    for source in sources:
        parts = urlsplit(source.url)
        path = parts.path.split("/", 2)
        try:
            port = parts.port
        except ValueError:
            continue
        if (
            parts.scheme != "https"
            or parts.hostname != "poe2db.tw"
            or port is not None
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or len(path) != 3
            or path[1] not in ("cn", "tw")
        ):
            continue
        name = unquote(path[2]).replace("_", " ").strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 ()'\-]{0,39}", name):
            continue
        if _normalize_name(name) not in {_normalize_name(existing) for existing in names}:
            names.append(name)
    return names


class _AllowedRedirects(HTTPRedirectHandler):
    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allowed(newurl):
            raise WikiSourceError("Wiki 跳转到了指定来源范围以外")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class WikiClient:
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
            or parts.hostname != "www.poe2wiki.net"
            or parts.username is not None
            or parts.password is not None
            or port is not None
        ):
            return False
        if parts.path in ("/w/api.php", "/api.php"):
            return True
        path = unquote(parts.path)
        if not path.startswith("/wiki/") or "\\" in path or parts.query:
            return False
        return all(part not in ("", ".", "..") for part in path.split("/")[2:])

    def _download_sync(self, url: str, kind: str) -> Any:
        if not self._allowed(url):
            raise WikiSourceError("来源网址不在 PoE2Wiki 范围内")
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
                    raise WikiSourceError("Wiki 跳转到了指定来源范围以外")
                media_type = response.headers.get_content_type()
                expected = ("application/json", "text/plain") if kind == "json" else ("text/html",)
                if media_type not in expected:
                    raise WikiSourceError("Wiki 返回了非预期格式")
                data = response.read(limit + 1)
        except HTTPError as exc:
            raise WikiSourceError(f"Wiki 返回 HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise WikiSourceError("连接 Wiki 失败或超时") from exc
        if len(data) > limit:
            raise WikiSourceError("Wiki 页面或响应过大，已停止读取")
        decoded = data.decode("utf-8", errors="replace")
        if kind == "html":
            return decoded
        try:
            return json.loads(decoded)
        except ValueError as exc:
            raise WikiSourceError("Wiki 没有返回有效 JSON") from exc

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

    async def _wiki_api(self, params: dict[str, object]) -> dict[str, Any]:
        last_error: WikiSourceError | None = None
        for base in WIKI_API_URLS:
            try:
                result = await self._fetch(base + "?" + urlencode(params), "json")
                if isinstance(result, dict):
                    if "error" in result:
                        raise WikiSourceError("Wiki API 返回错误")
                    return result
                raise WikiSourceError("Wiki API 返回的数据格式不正确")
            except WikiSourceError as exc:
                last_error = exc
        raise last_error or WikiSourceError("Wiki API 暂时无法读取")

    @staticmethod
    def _wiki_page_text(html: str, max_chars: int) -> str:
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
        if any(marker in title for marker in ("making sure you're not a bot", "just a moment", "attention required")):
            raise WikiSourceError("Wiki 要求浏览器验证")
        root = soup.select_one("#mw-content-text .mw-parser-output")
        if root is None:
            raise WikiSourceError("Wiki 页面缺少词条正文")
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
            except WikiSourceError:
                errors += 1
                continue
            queried += 1
            hits = (search.get("query") or {}).get("search") or []
            wanted = _normalize_name(name)
            exact = [hit for hit in hits if _normalize_name(str(hit.get("title", ""))) == wanted]
            candidates = exact or [
                hit
                for hit in hits
                if len(wanted) >= 4 and wanted in _normalize_name(str(hit.get("title", "")))
            ][:1]
            for hit in candidates[:1]:
                title = hit.get("title")
                if not isinstance(title, str) or not title or title.casefold() in used:
                    continue
                page_url = WIKI_PAGE_BASE + quote(title.replace(" ", "_"), safe="()")
                try:
                    text = self._wiki_page_text(await self._fetch(page_url, "html"), max_chars)
                except WikiSourceError:
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
                    except WikiSourceError:
                        continue
                if text:
                    used.add(title.casefold())
                    results.append(Source(title=f"PoE2Wiki：{title}", url=page_url, text=text))
                    if len(results) >= max_sources:
                        return results
        if errors and not queried and not results:
            raise WikiSourceError("PoE2Wiki 暂时无法读取")
        return results
