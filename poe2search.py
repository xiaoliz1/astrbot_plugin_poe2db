"""Formatting and relevance helpers for direct PoE2 encyclopedia searches."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlencode, urlsplit

from .poe2db import Source, search_url

WIKI_SEARCH_URL = "https://www.poe2wiki.net/wiki/Special:Search?"


def wiki_search_url(query: str) -> str:
    return WIKI_SEARCH_URL + urlencode({"search": query})


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", value)


def _allowed_source(source: Source) -> bool:
    try:
        parts = urlsplit(source.url)
        port = parts.port
    except ValueError:
        return False
    if (
        parts.scheme != "https"
        or port is not None
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        return False
    if parts.hostname == "poe2db.tw":
        return parts.path.startswith(("/cn/", "/tw/"))
    if parts.hostname == "www.poe2wiki.net":
        return parts.path.startswith("/wiki/")
    return False


def _ranked(query: str, sources: list[Source]) -> list[Source]:
    wanted = _normalized(query)
    if not wanted:
        return list(sources)

    def rank(item: tuple[int, Source]) -> tuple[float, int]:
        index, source = item
        title = _normalized(source.title)
        if wanted == title:
            return -0.1, index
        if wanted in title:
            return 0.0, index
        if title and title in wanted:
            return 0.15, index
        similarity = SequenceMatcher(None, wanted, title).ratio()
        return 1.0 - similarity, index

    return [source for _, source in sorted(enumerate(sources), key=rank)]


def merge_search_sources(
    query: str,
    poe_sources: list[Source],
    wiki_sources: list[Source],
    limit: int,
) -> list[Source]:
    """Rank results within each source, show both sources first, and deduplicate."""
    if limit <= 0:
        return []
    buckets = [
        _ranked(query, [source for source in poe_sources if _allowed_source(source)]),
        _ranked(query, [source for source in wiki_sources if _allowed_source(source)]),
    ]
    results: list[Source] = []
    seen: set[str] = set()

    def add(source: Source) -> None:
        key = source.url.rstrip("/").casefold()
        if key not in seen and len(results) < limit:
            seen.add(key)
            results.append(source)

    for bucket in buckets:
        if bucket:
            add(bucket[0])
    for bucket in buckets:
        for source in bucket[1:]:
            add(source)
            if len(results) >= limit:
                return results
    return results


def format_search_results(query: str, sources: list[Source], limit: int) -> str:
    if not sources:
        return (
            f"没有找到“{query}”的词条。可以直接查看资料站搜索：\n"
            f"Poe2DB 简体：{search_url(query, 'cn')}\n"
            f"Poe2DB 繁体：{search_url(query, 'tw')}\n"
            f"PoE2Wiki：{wiki_search_url(query)}"
        )

    entries: list[str] = []
    for index, source in enumerate(sources[:limit], 1):
        summary = re.sub(r"\s+", " ", source.text).strip()
        if len(summary) > 320:
            summary = summary[:320].rsplit(" ", 1)[0].rstrip() + "…"
        entries.append(f"{index}. {source.title}\n{summary}\n{source.url}")
    return f"“{query}”的词条搜索结果：\n\n" + "\n\n".join(entries)
