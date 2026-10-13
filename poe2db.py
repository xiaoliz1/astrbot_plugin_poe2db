"""Small, bounded reader for the Simplified and Traditional Chinese PoE2DB sites."""

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

from .economy import currency_key

BASE_URLS = {
    "cn": "https://poe2db.tw/cn/",
    "tw": "https://poe2db.tw/tw/",
}
PREFERRED_LOCALES = ("cn", "tw")
MAX_RESPONSE_BYTES = 3_000_000
USER_AGENT = "AstrBot-PoE2DB/1.7.3 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)"
CURRENCY_PAGE = BASE_URLS["cn"] + "Currency_Exchange"
LEAGUE_CN_PAGE = BASE_URLS["cn"] + "League"
LEAGUE_US_PAGE = "https://poe2db.tw/us/League"


class Poe2DBError(RuntimeError):
    """The source site could not be queried safely."""


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    text: str
    image_url: str | None = None


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


def parse_multilingual_terms(model_text: str, question: str) -> tuple[list[str], list[str]]:
    """Return up to two main names and their Traditional/English equivalents."""
    match = re.search(r"\{[\s\S]*\}", model_text or "")
    try:
        parsed = json.loads(match.group(0)) if match else None
    except (ValueError, TypeError):
        parsed = None
    if not isinstance(parsed, dict):
        fallback = parse_query_terms(model_text, question)
        return fallback, fallback

    by_locale: dict[str, list[str]] = {}
    for locale in ("zh", "tw", "en"):
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
            if len(cleaned) == 2:
                break
        by_locale[locale] = cleaned

    primary = by_locale["zh"] or by_locale["tw"] or by_locale["en"]
    if not primary:
        primary = parse_query_terms("", question)
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
            raise Poe2DBError("只允许读取 poe2db.tw/cn 或 /tw 页面")
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
    def _check_challenge(title: str) -> None:
        if title.casefold() in {"just a moment...", "attention required!"}:
            raise Poe2DBError("资料站点要求浏览器验证，暂时无法自动读取")

    async def currency_names(self) -> dict[str, str]:
        """Read current Simplified Chinese names from one Poe2DB currency page."""
        soup = BeautifulSoup(await self._fetch(CURRENCY_PAGE), "html.parser")
        self._check_challenge(self._title(soup))
        root = soup.select_one(".page-content")
        if root is None:
            raise Poe2DBError("中文通货页面缺少正文")
        names: dict[str, str] = {}
        # Some BeautifulSoup versions mishandle CSS class+attribute selectors.
        for anchor in root.find_all("a", class_="item_currency"):
            slug = anchor.get("href")
            name = anchor.get_text(" ", strip=True)
            if (
                isinstance(slug, str)
                and re.fullmatch(r"[A-Za-z0-9_'-]{1,100}", slug)
                and re.search(r"[\u3400-\u9fff]", name)
            ):
                names[currency_key(slug)] = name[:90]
        if not names:
            raise Poe2DBError("中文通货页面没有可用的物品名称")
        return names

    @staticmethod
    def _league_rows(soup: BeautifulSoup) -> dict[str, str]:
        rows: dict[str, str] = {}
        root = soup.select_one(".page-content") or soup
        for row in root.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 2:
                continue
            version = cells[0].get_text(" ", strip=True)
            name = cells[1].get_text(" ", strip=True)
            if re.fullmatch(r"\d+(?:\.\d+){0,3}", version) and name:
                rows[version] = name
        return rows

    async def chinese_league_name(self, english_name: str) -> str | None:
        """Match the live English and Chinese league lists by game version."""
        if not english_name or len(english_name) > 100:
            return None
        cn_html, us_html = await asyncio.gather(
            self._fetch(LEAGUE_CN_PAGE), self._fetch(LEAGUE_US_PAGE)
        )
        cn_soup = BeautifulSoup(cn_html, "html.parser")
        us_soup = BeautifulSoup(us_html, "html.parser")
        self._check_challenge(self._title(cn_soup))
        self._check_challenge(self._title(us_soup))
        english = self._league_rows(us_soup)
        chinese = self._league_rows(cn_soup)
        for version, name in english.items():
            translated = chinese.get(version)
            if name.casefold() == english_name.casefold() and translated and re.search(
                r"[\u3400-\u9fff]", translated
            ):
                return translated[:100]
        return None

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

    async def find_sources(
        self, terms: list[str], max_sources: int = 2, max_chars: int = 5000
    ) -> list[Source]:
        sources: list[Source] = []
        seen: set[str] = set()
        successful_searches = 0
        for term in terms[:2]:
            for locale in PREFERRED_LOCALES:
                try:
                    found = await self._search_term(term, locale, max_chars)
                except Poe2DBError:
                    continue
                successful_searches += 1
                for source in found:
                    key = self._source_key(source.url)
                    if key not in seen:
                        sources.append(source)
                        seen.add(key)
                    if len(sources) >= max_sources:
                        return sources
                if found:
                    break
        if not sources and not successful_searches:
            raise Poe2DBError("简体与繁体资料页均无法读取")
        return sources
