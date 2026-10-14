"""Small, bounded reader for the Simplified and Traditional Chinese PoE2DB sites."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

BASE_URLS = {
    "cn": "https://poe2db.tw/cn/",
    "tw": "https://poe2db.tw/tw/",
    "us": "https://poe2db.tw/us/",
}
PREFERRED_LOCALES = ("cn", "tw")
MAX_RESPONSE_BYTES = 3_000_000
USER_AGENT = "AstrBot-PoE2DB/1.21.0 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)"


class Poe2DBError(RuntimeError):
    """The source site could not be queried safely."""


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    text: str
    image_url: str | None = None


@dataclass(frozen=True)
class MarketLink:
    """A Poe2DB The Market link with its Chinese item-page title when known."""

    item_name: str
    chinese_name: str | None
    url: str


def search_url(term: str, locale: str = "cn") -> str:
    if locale not in BASE_URLS:
        raise ValueError(f"Unsupported Poe2DB locale: {locale}")
    return BASE_URLS[locale] + "search?" + urlencode({"q": term})


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


def parse_multilingual_names(
    model_text: str, max_names: int = 2
) -> dict[str, list[str]]:
    """Parse bounded Chinese, Traditional Chinese, and English names from JSON."""
    match = re.search(r"\{[\s\S]*\}", model_text or "")
    try:
        parsed = json.loads(match.group(0)) if match else None
    except (ValueError, TypeError):
        parsed = None
    if not isinstance(parsed, dict):
        return {locale: [] for locale in ("zh", "tw", "en")}

    by_locale: dict[str, list[str]] = {}
    limit = max(0, max_names)
    for locale in ("zh", "tw", "en"):
        if limit == 0:
            by_locale[locale] = []
            continue
        values = parsed.get(locale, [])
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            values = []
        cleaned: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            name = re.sub(r"\s+", " ", value).strip(" \t\r\n,，。？?\"'「」")
            if name and len(name) <= 40 and "://" not in name and name not in cleaned:
                cleaned.append(name)
            if len(cleaned) >= limit:
                break
        by_locale[locale] = cleaned

    return by_locale


def parse_multilingual_terms(model_text: str, question: str) -> tuple[list[str], list[str]]:
    """Return up to two main names and their Traditional/English equivalents."""
    by_locale = parse_multilingual_names(model_text, max_names=2)

    primary = by_locale["zh"] or by_locale["tw"] or by_locale["en"]
    if not primary:
        primary = parse_query_terms(model_text, question)
    names: list[str] = []
    for name in primary + by_locale["tw"] + by_locale["en"]:
        if name not in names:
            names.append(name)
    return primary[:2], names[:6]


class Poe2DBClient:
    def __init__(self, timeout_seconds: int = 15, cache_seconds: int = 900):
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, str]] = {}
        self._resolved_urls: dict[str, str] = {}
        self._semaphore = asyncio.Semaphore(2)

    @staticmethod
    def _allowed(url: str) -> bool:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname == "poe2db.tw"
            and parts.port is None
            and parts.username is None
            and (
                any(parts.path.startswith(f"/{locale}/") for locale in BASE_URLS)
                or parts.path == "/us/League"
            )
        )

    def _download_sync(self, url: str) -> tuple[str, str]:
        if not self._allowed(url):
            raise Poe2DBError("只允许读取 poe2db.tw 的中文或英文资料页")
        request = Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                resolved_url = response.geturl()
                if not self._allowed(resolved_url):
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
        return resolved_url, data.decode("utf-8", errors="replace")

    async def _fetch(self, url: str) -> str:
        now = time.monotonic()
        cached = self._cache.get(url)
        if cached and cached[0] > now:
            return cached[1]
        async with self._semaphore:
            resolved_url, html = await asyncio.to_thread(self._download_sync, url)
        self._resolved_urls[url] = resolved_url
        self._cache[url] = (time.monotonic() + self.cache_seconds, html)
        if len(self._cache) > 64:
            old_url = next(iter(self._cache))
            self._cache.pop(old_url)
            self._resolved_urls.pop(old_url, None)
        return html

    @staticmethod
    def _title(soup: BeautifulSoup) -> str:
        if not soup.title:
            return ""
        return soup.title.get_text(" ", strip=True).split(" - ", 1)[0].strip()

    @staticmethod
    def _article_heading(soup: BeautifulSoup) -> str:
        heading = soup.select_one(".page-content h1, .page-content h2, .page-content h3")
        return heading.get_text(" ", strip=True) if heading else ""

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
        locale = urlsplit(base).path.split("/", 2)[1]
        if locale not in BASE_URLS or not parts.path.startswith(f"/{locale}/"):
            return None
        if parts.path == f"/{locale}/search":
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

    @staticmethod
    def _trade_market_url(
        url: str, item_name: str, league_name: str
    ) -> str | None:
        """Validate a Poe2DB Market link and point it at the current league."""
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            return None
        path_match = re.fullmatch(r"/trade2/search/poe2/([^/]+)", parts.path)
        if (
            parts.scheme != "https"
            or parts.hostname != "www.pathofexile.com"
            or port is not None
            or parts.username is not None
            or parts.password is not None
            or not path_match
            or not league_name.strip()
            or len(league_name) > 100
        ):
            return None
        encoded_query = parse_qs(parts.query).get("q", [])
        if not encoded_query:
            return None
        try:
            search_query = json.loads(encoded_query[0])
        except (TypeError, ValueError):
            return None
        query_name = (
            search_query.get("query", {}).get("name")
            if isinstance(search_query, dict)
            and isinstance(search_query.get("query"), dict)
            else None
        )
        if not isinstance(query_name, str) or query_name.casefold() != item_name.casefold():
            return None
        current_league_path = quote(league_name.strip(), safe="")
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                f"/trade2/search/poe2/{current_league_path}",
                parts.query,
                parts.fragment,
            )
        )

    @classmethod
    def _market_href_from_item_page(cls, soup: BeautifulSoup) -> str | None:
        market_labels = {"the market", "市集", "市場"}
        for row in soup.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if not cells or cells[0].get_text(" ", strip=True).casefold() not in market_labels:
                continue
            for anchor in row.find_all("a", href=True):
                if anchor.get_text(" ", strip=True).casefold() in market_labels:
                    return str(anchor["href"])
        return None

    @staticmethod
    def _china_trade_market_url(url: str, item_name: str) -> str | None:
        """Validate and preserve a national-market link from a Poe2DB /cn page."""
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            return None
        path_match = re.fullmatch(r"/trade2/search/poe2/([^/]+)", parts.path)
        if (
            parts.scheme != "https"
            or parts.hostname != "poe.game.qq.com"
            or port is not None
            or parts.username is not None
            or parts.password is not None
            or not path_match
            or not path_match.group(1)
            or len(path_match.group(1)) > 120
        ):
            return None
        encoded_query = parse_qs(parts.query).get("q", [])
        if not encoded_query:
            return None
        try:
            search_query = json.loads(encoded_query[0])
        except (TypeError, ValueError):
            return None
        query_name = (
            search_query.get("query", {}).get("name")
            if isinstance(search_query, dict)
            and isinstance(search_query.get("query"), dict)
            else None
        )
        if not isinstance(query_name, str) or query_name.casefold() != item_name.casefold():
            return None
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))

    async def find_unique_market_link(
        self, item_name: str, league_name: str, locale: str = "us"
    ) -> MarketLink | None:
        """Find an exact unique item's market link from its requested Poe2DB locale.

        National-market URLs are read from the matching Simplified Chinese
        article and keep that article's own league path. International URLs
        continue to use the current poe.ninja league name.
        """
        name = re.sub(r"\s+", " ", item_name).strip()
        if not name or len(name) > 100 or "://" in name or locale not in {"us", "cn"}:
            return None

        query_url = search_url(name, "us")
        try:
            search_html = await self._fetch(query_url)
            search_soup = BeautifulSoup(search_html, "html.parser")
            search_title = self._title(search_soup)
            self._check_challenge(search_title)
            resolved_url = self._resolved_urls.get(query_url, query_url)

            if search_title.casefold() == "search":
                pages: list[tuple[str, BeautifulSoup]] = []
                for candidate_url in self._result_urls(search_soup, query_url):
                    try:
                        article_html = await self._fetch(candidate_url)
                    except Poe2DBError:
                        continue
                    pages.append(
                        (
                            self._resolved_urls.get(candidate_url, candidate_url),
                            BeautifulSoup(article_html, "html.parser"),
                        )
                    )
            else:
                pages = [(resolved_url, search_soup)]

            for article_url, article_soup in pages:
                self._check_challenge(self._title(article_soup))
                if self._article_heading(article_soup).casefold() != name.casefold():
                    continue

                if locale == "cn":
                    chinese_article = await self._chinese_article_for_us_article(article_url)
                    if not chinese_article:
                        continue
                    chinese_url, chinese_soup, chinese_name = chinese_article
                    market_href = self._market_href_from_item_page(chinese_soup)
                    if not market_href:
                        continue
                    market_url = self._china_trade_market_url(
                        urljoin(chinese_url, market_href), chinese_name
                    )
                    if market_url:
                        return MarketLink(name, chinese_name, market_url)
                    continue

                market_href = self._market_href_from_item_page(article_soup)
                if not market_href:
                    continue
                market_url = self._trade_market_url(
                    urljoin(article_url, market_href), name, league_name
                )
                if not market_url:
                    continue
                chinese_name = await self._chinese_title_for_us_article(article_url)
                return MarketLink(name, chinese_name, market_url)
        except Poe2DBError:
            return None
        return None

    async def _chinese_title_for_us_article(self, article_url: str) -> str | None:
        article = await self._chinese_article_for_us_article(article_url)
        return article[2] if article else None

    async def _chinese_article_for_us_article(
        self, article_url: str
    ) -> tuple[str, BeautifulSoup, str] | None:
        parts = urlsplit(article_url)
        if (
            parts.scheme != "https"
            or parts.hostname != "poe2db.tw"
            or not parts.path.startswith("/us/")
            or parts.path.count("/") != 2
        ):
            return None
        chinese_url = urlunsplit(
            (parts.scheme, parts.netloc, "/cn/" + parts.path[len("/us/") :], "", "")
        )
        try:
            soup = BeautifulSoup(await self._fetch(chinese_url), "html.parser")
        except Poe2DBError:
            return None
        self._check_challenge(self._title(soup))
        title = self._article_heading(soup)
        resolved_url = self._resolved_urls.get(chinese_url, chinese_url)
        return (resolved_url, soup, title) if title else None

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
    def _allowed_image_url(url: str) -> bool:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            return False
        return (
            parts.scheme == "https"
            and parts.hostname == "cdn.poe2db.tw"
            and port is None
            and parts.username is None
            and parts.password is None
            and parts.path.startswith("/image/")
            and parts.path.lower().endswith((".webp", ".png", ".jpg", ".jpeg", ".gif"))
            and not parts.query
            and not parts.fragment
        )

    @classmethod
    def _image_from_page(cls, soup: BeautifulSoup, page_url: str) -> str | None:
        root = soup.select_one(".page-content") or soup
        candidates: list[tuple[int, str]] = []

        for anchor in root.find_all("a", href=True):
            candidate = urljoin(page_url, str(anchor.get("href", "")))
            if cls._allowed_image_url(candidate):
                label = anchor.get_text(" ", strip=True).casefold()
                priority = 1 if label in {"image", "图片", "圖片", "图标", "圖標"} else 2
                candidates.append((priority, candidate))

        for image in root.find_all("img"):
            for attribute in ("src", "data-src", "data-original", "srcset"):
                value = image.get(attribute)
                if not isinstance(value, str):
                    continue
                candidates_in_attribute = value.split(",") if attribute == "srcset" else [value]
                for item in candidates_in_attribute:
                    candidate_value = item.strip().split()[0] if item.strip() else ""
                    candidate = urljoin(page_url, candidate_value)
                    if cls._allowed_image_url(candidate):
                        candidates.append((3, candidate))

        metadata_image = soup.find("meta", attrs={"property": "og:image", "content": True})
        if metadata_image:
            candidate = urljoin(page_url, str(metadata_image.get("content", "")))
            if cls._allowed_image_url(candidate):
                # Prefer the page's declared preview image; page-content may
                # begin with unrelated atlas and minimap artwork.
                candidates.append((0, candidate))

        candidates.sort(key=lambda item: item[0])
        return candidates[0][1] if candidates else None

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

    @classmethod
    def _source_from_page(
        cls, soup: BeautifulSoup, url: str, max_chars: int
    ) -> Source | None:
        title = cls._title(soup)
        cls._check_challenge(title)
        if not title or title.casefold() in {"家园", "首页", "家園", "首頁", "home", "search"}:
            return None
        # Text extraction rewrites table rows and can remove the self-link.
        canonical = cls._canonical_url(soup, title, url)
        image_url = cls._image_from_page(soup, url)
        text = cls._extract_text(soup, max_chars)
        if not text:
            return None
        return Source(title, canonical, text, image_url)

    async def _search_term(self, term: str, locale: str, max_chars: int) -> list[Source]:
        url = search_url(term, locale)
        soup = BeautifulSoup(await self._fetch(url), "html.parser")
        resolved_url = self._resolved_urls.get(url, url)
        title = self._title(soup)
        self._check_challenge(title)
        if title.casefold() != "search":
            source = self._source_from_page(soup, resolved_url, max_chars)
            return [source] if source else []

        sources: list[Source] = []
        for candidate in self._result_urls(soup, url):
            try:
                page = BeautifulSoup(await self._fetch(candidate), "html.parser")
                source = self._source_from_page(
                    page, self._resolved_urls.get(candidate, candidate), max_chars
                )
            except Poe2DBError:
                continue
            if source:
                sources.append(source)
        return sources

    @staticmethod
    def _source_key(url: str) -> str:
        parts = urlsplit(url)
        slug = parts.path.split("/", 2)[2]
        return slug.casefold() + ("?" + parts.query if parts.query else "")

    async def find_sources_by_term(
        self, terms: list[str], max_sources: int = 6, max_chars: int = 5000
    ) -> list[tuple[str, list[Source]]]:
        """Keep each extracted topic's results separate for disambiguation."""
        if max_sources <= 0:
            return []
        grouped: list[tuple[str, list[Source]]] = []
        successful_searches = 0
        for term in terms[:2]:
            term_sources: list[Source] = []
            seen_for_term: set[str] = set()
            for locale in PREFERRED_LOCALES:
                try:
                    found = await self._search_term(term, locale, max_chars)
                except Poe2DBError:
                    continue
                successful_searches += 1
                for source in found:
                    key = self._source_key(source.url)
                    if key not in seen_for_term:
                        term_sources.append(source)
                        seen_for_term.add(key)
                    if len(term_sources) >= max_sources:
                        break
                if found:
                    break
            grouped.append((term, term_sources))
        if not successful_searches:
            raise Poe2DBError("简体与繁体资料页均无法读取")
        return grouped

    async def find_sources(
        self, terms: list[str], max_sources: int = 2, max_chars: int = 5000
    ) -> list[Source]:
        if max_sources <= 0:
            return []
        grouped = await self.find_sources_by_term(terms, max_sources, max_chars)
        sources: list[Source] = []
        seen: set[str] = set()
        for _, term_sources in grouped:
            for source in term_sources:
                key = self._source_key(source.url)
                if key not in seen:
                    sources.append(source)
                    seen.add(key)
                if len(sources) >= max_sources:
                    return sources
        return sources
