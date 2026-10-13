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


def _title_rank(query: str, title: str) -> float:
    wanted = _normalized(query)
    normalized_title = _normalized(title)
    if not wanted or not normalized_title:
        return 1.0
    if wanted == normalized_title:
        return -0.1
    if wanted in normalized_title:
        return 0.0
    if normalized_title in wanted:
        return 0.15
    return 1.0 - SequenceMatcher(None, wanted, normalized_title).ratio()


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
    def rank(item: tuple[int, Source]) -> tuple[float, int]:
        index, source = item
        return _title_rank(query, source.title), index

    return [source for _, source in sorted(enumerate(sources), key=rank)]


def rank_source_groups(
    grouped_sources: list[tuple[str, list[Source]]],
) -> list[tuple[str, list[Source]]]:
    """Rank each extracted topic independently, preserving its group."""
    return [(query, _ranked(query, sources)) for query, sources in grouped_sources]


def find_ambiguous_groups(
    grouped_sources: list[tuple[str, list[Source]]],
    *,
    max_candidates: int = 5,
    max_rank: float = 0.15,
    max_rank_gap: float = 0.12,
) -> list[tuple[int, str, list[Source]]]:
    """Return same-query groups with at least two similarly relevant titles."""
    if max_candidates < 2:
        return []
    ambiguous: list[tuple[int, str, list[Source]]] = []
    for group_index, (query, sources) in enumerate(grouped_sources):
        ranked = _ranked(query, sources)
        if len(ranked) < 2:
            continue
        best_rank = _title_rank(query, ranked[0].title)
        candidates: list[Source] = []
        seen_titles: set[str] = set()
        for source in ranked:
            rank = _title_rank(query, source.title)
            if rank > max_rank or rank - best_rank > max_rank_gap:
                break
            title_key = _normalized(source.title)
            if title_key and title_key not in seen_titles:
                seen_titles.add(title_key)
                candidates.append(source)
            if len(candidates) >= max_candidates:
                break
        if len(candidates) >= 2:
            ambiguous.append((group_index, query, candidates))
    return ambiguous


def format_disambiguation_prompt(query: str, candidates: list[Source]) -> str:
    lines = [f"“{query}”可能对应多个词条，请选择："]
    for index, source in enumerate(candidates, 1):
        title = re.sub(r"\s+", " ", source.title).strip()[:100] or "未命名词条"
        lines.append(f"{index}. {title}")
    lines.append("请在 60 秒内回复编号；回复“取消”可结束选择。")
    return "\n".join(lines)


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
