"""Small, bounded reader for the Traditional Chinese PoE2DB site."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

BASE_URL = "https://poe2db.tw/tw/"
MAX_RESPONSE_BYTES = 3_000_000
USER_AGENT = "AstrBot-PoE2DB/1.0 (+https://poe2db.tw/tw/)"


class Poe2DBError(RuntimeError):
    """The source site could not be queried safely."""


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    text: str


def search_url(term: str) -> str:
    return BASE_URL + "search?" + urlencode({"q": term})


def parse_query_terms(model_text: str, question: str) -> list[str]:
    """Read a short JSON term list; fall back to a term from the question."""
    match = re.search(r"\[[\s\S]*?\]", model_text or "")
    try:
        parsed = json.loads(match.group(0)) if match else []
    except (ValueError, TypeError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = []

    terms: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        term = re.sub(r"\s+", " ", item).strip(" \t\r\n,，。？?\"'「」")
        if not term or len(term) > 40 or "://" in term:
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) == 2:
            break
    if terms:
        return terms

    quoted = re.search(r"[「『“\"]([^」』”\"]{1,40})[」』”\"]", question)
    if quoted:
        return [quoted.group(1).strip()]
    fallback = re.sub(r"(?i)(流放之路|流亡之路|path of exile|poe)\s*2", "", question)
    fallback = re.sub(r"^(请问|請問|想问|想問|我想知道|关于|關於)", "", fallback).strip()
    fallback = re.split(r"[？?。，,；;]|是什么|是什麼|有什么|有什麼|怎么|怎麼|如何|哪些", fallback)[0]
    return [fallback.strip()[:40] or question.strip()[:40]]


class Poe2DBClient:
    def __init__(self, timeout_seconds: int = 15, cache_seconds: int = 900):
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, str]] = {}
        self._semaphore = asyncio.Semaphore(2)

    @staticmethod
    def _allowed(url: str) -> bool:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname == "poe2db.tw"
            and parts.port is None
            and parts.username is None
            and parts.path.startswith("/tw/")
        )

    def _download_sync(self, url: str) -> str:
        if not self._allowed(url):
            raise Poe2DBError("只允许读取 poe2db.tw/tw 页面")
        request = Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if not self._allowed(response.geturl()):
                    raise Poe2DBError("站点跳转到了指定资料范围以外")
                if response.headers.get_content_type() != "text/html":
                    raise Poe2DBError("站点返回的不是 HTML 页面")
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise Poe2DBError(f"站点返回 HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise Poe2DBError("连接资料站点失败或超时") from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise Poe2DBError("资料页面过大，已停止读取")
        return data.decode("utf-8", errors="replace")

    async def _fetch(self, url: str) -> str:
        now = time.monotonic()
        cached = self._cache.get(url)
        if cached and cached[0] > now:
            return cached[1]
        async with self._semaphore:
            html = await asyncio.to_thread(self._download_sync, url)
        self._cache[url] = (time.monotonic() + self.cache_seconds, html)
        if len(self._cache) > 64:
            self._cache.pop(next(iter(self._cache)))
        return html

    @staticmethod
    def _title(soup: BeautifulSoup) -> str:
        if not soup.title:
            return ""
        return soup.title.get_text(" ", strip=True).split(" - ", 1)[0].strip()

    @staticmethod
    def _check_challenge(title: str) -> None:
        if title.casefold() in {"just a moment...", "attention required!"}:
            raise Poe2DBError("资料站点要求浏览器验证，暂时无法自动读取")

    @classmethod
    def _clean_url(cls, base: str, href: str) -> str | None:
        full = urljoin(base, href)
        if not cls._allowed(full):
            return None
        parts = urlsplit(full)
        if parts.path == "/tw/search":
            return None
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @classmethod
    def _result_urls(cls, soup: BeautifulSoup, base: str) -> list[str]:
        links = soup.select(".page-content .card .list-group a[href]")
        result: list[str] = []
        for anchor in links:
            url = cls._clean_url(base, anchor.get("href", ""))
            if url and url not in result:
                result.append(url)
        return result[:6]

    @classmethod
    def _canonical_url(cls, soup: BeautifulSoup, title: str, fallback: str) -> str:
        root = soup.select_one(".page-content")
        if root:
            for anchor in root.select("a[href]"):
                if anchor.get_text(" ", strip=True) == title:
                    url = cls._clean_url(fallback, anchor.get("href", ""))
                    if url:
                        return url
        return fallback

    @staticmethod
    def _extract_text(soup: BeautifulSoup, max_chars: int) -> str:
        root = soup.select_one(".page-content .tab-content .tab-pane")
        if root is None:
            root = soup.select_one(".page-content")
        if root is None:
            return ""
        for node in root.select(
            "script, style, noscript, footer, form, nav, "
            "#head_toolbar, #topbanner970, #bottom_banner"
        ):
            node.decompose()
        for row in root.select("tr"):
            cells = [
                cell.get_text(" ", strip=True)
                for cell in row.find_all(["th", "td"], recursive=False)
            ]
            if cells:
                row.clear()
                row.append("【" + " | ".join(cells) + "】")
        # Inline game-term spans are extremely common on this site. A newline
        # separator would split nearly every word and waste the model context.
        text = re.sub(r"\s+", " ", root.get_text(" ", strip=True)).strip()
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rsplit(" ", 1)[0]

    async def find_sources(
        self, terms: list[str], max_sources: int = 2, max_chars: int = 5000
    ) -> list[Source]:
        sources: list[Source] = []
        candidate_urls: list[str] = []
        seen: set[str] = set()
        for term in terms[:2]:
            url = search_url(term)
            soup = BeautifulSoup(await self._fetch(url), "html.parser")
            title = self._title(soup)
            self._check_challenge(title)
            if not title or title.casefold() in {"家園", "首頁", "home"}:
                continue
            if title.casefold() == "search":
                for candidate in self._result_urls(soup, url):
                    if candidate not in candidate_urls:
                        candidate_urls.append(candidate)
                continue
            canonical = self._canonical_url(soup, title, url)
            text = self._extract_text(soup, max_chars)
            if text and canonical not in seen:
                sources.append(Source(title, canonical, text))
                seen.add(canonical)
            if len(sources) >= max_sources:
                return sources

        for url in candidate_urls:
            if len(sources) >= max_sources:
                break
            try:
                soup = BeautifulSoup(await self._fetch(url), "html.parser")
            except Poe2DBError:
                continue
            title = self._title(soup)
            self._check_challenge(title)
            if not title or title.casefold() in {"家園", "首頁", "home", "search"}:
                continue
            canonical = self._canonical_url(soup, title, url)
            text = self._extract_text(soup, max_chars)
            if text and canonical not in seen:
                sources.append(Source(title, canonical, text))
                seen.add(canonical)
        return sources
