"""POE2 China-server economy data from poecurrency.top."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .economy import (
    CurrencyRow,
    CurrencySnapshot,
    EconomyError,
    currency_key,
    safe_currency_icon_url,
)

SUMMARY_URL = "https://poecurrency.top/api/summary?version=2"
USER_AGENT = "AstrBot-PoE2DB/1.10.0 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)"
MAX_JSON_BYTES = 4_000_000
CACHE_SECONDS = 3600
CURRENCY_UNITS = {
    "c": "混沌石",
    "e": "崇高石",
    "d": "神圣石",
}


def _now_text() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_national_summary(payload: object) -> CurrencySnapshot:
    if not isinstance(payload, list):
        raise EconomyError("国服物价数据格式不正确")
    category = next(
        (
            entry
            for entry in payload
            if isinstance(entry, dict) and entry.get("category_label") == "通货仓库"
        ),
        None,
    )
    if not isinstance(category, dict) or not isinstance(category.get("items"), list):
        raise EconomyError("国服物价数据缺少通货仓库")

    rows: list[CurrencyRow] = []
    timestamps: list[str] = []
    for item in category["items"]:
        if not isinstance(item, dict) or item.get("error") is True:
            continue
        name = item.get("item_name")
        english_name = item.get("engname")
        unit = CURRENCY_UNITS.get(item.get("currency_unit"))
        if not isinstance(name, str) or not name or not unit:
            continue
        buy = _finite_number(item.get("buy_avg"))
        sell = _finite_number(item.get("sell_avg"))
        if buy is None or sell is None or (buy <= 0 and sell <= 0):
            continue
        buy_change = _finite_number(item.get("buy_avg_ratio"))
        sell_change = _finite_number(item.get("sell_avg_ratio"))
        rows.append(
            CurrencyRow(
                name=name[:90],
                value=max(0.0, buy),
                change_percent=buy_change,
                trend=(),
                sell_value=max(0.0, sell),
                sell_change_percent=sell_change,
                unit_label=unit,
                sort_key=currency_key(english_name) if isinstance(english_name, str) else "",
                category=str(category.get("category_label") or "通货")[:60],
                icon_url=safe_currency_icon_url(item.get("item_icon")),
            )
        )
        timestamp = item.get("latest_datetime")
        if isinstance(timestamp, str) and timestamp:
            timestamps.append(timestamp[:40])

    if not rows:
        raise EconomyError("当前国服赛季没有可显示的通货价格")
    return CurrencySnapshot(
        league="当前赛季",
        reference="",
        rows=tuple(rows),
        total_count=len(rows),
        generated_at=_now_text(),
        market="国服",
        category_label=category["category_label"],
        source_updated_at=max(timestamps) if timestamps else None,
    )


def parse_national_divine_summary(
    payload: object,
) -> CurrencySnapshot:
    """Keep national items whose API quote is directly denominated in Divine Orbs."""
    if not isinstance(payload, list):
        raise EconomyError("国服物价数据格式不正确")

    rows_by_key: dict[str, CurrencyRow] = {}
    timestamps: list[str] = []
    for category in payload:
        if not isinstance(category, dict) or not isinstance(category.get("items"), list):
            continue
        category_label = category.get("category_label")
        if not isinstance(category_label, str) or not category_label:
            category_label = "通货"
        for item in category["items"]:
            if not isinstance(item, dict) or item.get("error") is True:
                continue
            if item.get("currency_unit") != "d":
                continue
            english_name = item.get("engname")
            name = item.get("item_name")
            if not isinstance(english_name, str) or not english_name or not isinstance(name, str) or not name:
                continue
            sort_key = currency_key(english_name)
            buy = _finite_number(item.get("buy_avg"))
            sell = _finite_number(item.get("sell_avg"))
            if buy is None or sell is None or (buy <= 0 and sell <= 0):
                continue
            row = CurrencyRow(
                name=name[:90],
                value=max(0.0, buy),
                change_percent=_finite_number(item.get("buy_avg_ratio")),
                trend=(),
                sell_value=max(0.0, sell),
                sell_change_percent=_finite_number(item.get("sell_avg_ratio")),
                unit_label="神圣石",
                sort_key=sort_key,
                category=category_label[:60],
                icon_url=safe_currency_icon_url(item.get("item_icon")),
            )
            previous = rows_by_key.get(sort_key)
            if previous is None or row.value > previous.value:
                rows_by_key[sort_key] = row
            timestamp = item.get("latest_datetime")
            if isinstance(timestamp, str) and timestamp:
                timestamps.append(timestamp[:40])

    rows = tuple(
        sorted(rows_by_key.values(), key=lambda row: (-row.value, row.name))
    )
    if not rows:
        raise EconomyError("国服物价中没有以神圣石直接计价的物品")
    return CurrencySnapshot(
        league="当前赛季",
        reference="神圣石",
        rows=rows,
        total_count=len(rows),
        generated_at=_now_text(),
        market="国服",
        category_label="国服物价",
        source_updated_at=max(timestamps) if timestamps else None,
    )


def parse_national_chinese_names(payload: object) -> dict[str, str]:
    """Build an English-name to Chinese-name map from the national API itself."""
    if not isinstance(payload, list):
        raise EconomyError("国服物价数据格式不正确")
    names: dict[str, str] = {"divineorb": "神圣石"}
    for category in payload:
        if not isinstance(category, dict) or not isinstance(category.get("items"), list):
            continue
        for item in category["items"]:
            if not isinstance(item, dict) or item.get("error") is True:
                continue
            english_name = item.get("engname")
            chinese_name = item.get("item_name")
            if not isinstance(english_name, str) or not english_name:
                continue
            if not isinstance(chinese_name, str) or not chinese_name:
                continue
            names[currency_key(english_name)] = chinese_name[:90]
    return names


def sort_national_snapshot(
    snapshot: CurrencySnapshot, international_rows: Sequence[CurrencyRow]
) -> CurrencySnapshot:
    """Order shared national currencies by poe.ninja's live ranking."""
    rank: dict[str, int] = {}
    for index, row in enumerate(international_rows):
        if row.sort_key:
            rank.setdefault(row.sort_key, index)
    rows = tuple(
        row
        for _, row in sorted(
            enumerate(snapshot.rows),
            key=lambda pair: (rank.get(pair[1].sort_key, len(rank)), pair[0]),
        )
    )
    return replace(snapshot, rows=rows)


class _AllowedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not PoECurrencyClient._allowed(newurl):
            raise EconomyError("国服物价接口跳转到了允许范围以外")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PoECurrencyClient:
    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = timeout_seconds
        self._cached_until = 0.0
        self._cached_snapshot: CurrencySnapshot | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _allowed(url: str) -> bool:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            return False
        return (
            parts.scheme == "https"
            and parts.hostname == "poecurrency.top"
            and port is None
            and parts.username is None
            and parts.password is None
            and not parts.fragment
            and parts.path == "/api/summary"
            and parse_qs(parts.query) == {"version": ["2"]}
        )

    def _download_payload_sync(self) -> object:
        if not self._allowed(SUMMARY_URL):
            raise EconomyError("国服物价网址不在允许范围内")
        request = Request(
            SUMMARY_URL,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        )
        try:
            with build_opener(_AllowedRedirects()).open(
                request, timeout=self.timeout_seconds
            ) as response:
                if not self._allowed(response.geturl()):
                    raise EconomyError("国服物价接口跳转到了允许范围以外")
                if response.headers.get_content_type() not in ("application/json", "text/plain"):
                    raise EconomyError("国服物价接口没有返回 JSON")
                data = response.read(MAX_JSON_BYTES + 1)
        except HTTPError as exc:
            raise EconomyError(f"国服物价接口返回 HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise EconomyError("连接国服物价接口失败或超时") from exc
        if len(data) > MAX_JSON_BYTES:
            raise EconomyError("国服物价响应过大，已停止读取")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise EconomyError("国服物价接口没有返回有效 JSON") from exc

    def _download_sync(self) -> CurrencySnapshot:
        return parse_national_summary(self._download_payload_sync())

    async def current_divine_snapshot(
    ) -> CurrencySnapshot:
        """Fetch current national market data, keeping only Divine-denominated rows."""
        payload = await asyncio.to_thread(self._download_payload_sync)
        return parse_national_divine_summary(payload)

    async def current_divine_data(self) -> tuple[CurrencySnapshot, dict[str, str]]:
        """Fetch once for both the national chart and Chinese item localization."""
        payload = await asyncio.to_thread(self._download_payload_sync)
        return (
            parse_national_divine_summary(payload),
            parse_national_chinese_names(payload),
        )

    async def current_currency(self) -> CurrencySnapshot:
        if self._cached_snapshot and self._cached_until > time.monotonic():
            return replace(self._cached_snapshot, generated_at=_now_text())
        async with self._lock:
            if self._cached_snapshot and self._cached_until > time.monotonic():
                return replace(self._cached_snapshot, generated_at=_now_text())
            snapshot = await asyncio.to_thread(self._download_sync)
            self._cached_snapshot = snapshot
            self._cached_until = time.monotonic() + CACHE_SECONDS
            return snapshot
