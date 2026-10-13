"""PoE2 economy snapshots from poe.ninja's public economy API."""

from __future__ import annotations

import asyncio
import html
import json
import math
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

LEAGUES_URL = "https://poe.ninja/poe2/api/economy/leagues"
OVERVIEW_URL = "https://poe.ninja/poe2/api/economy/exchange/current/overview"
USER_AGENT = "AstrBot-PoE2DB/1.8.0 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)"
MAX_JSON_BYTES = 2_000_000
LEAGUES_CACHE_SECONDS = 300
PRICES_CACHE_SECONDS = 1800
IMAGE_ROWS = 50

# Poe2DB exposes 14 Chinese economy pages. These names are scraped separately
# from the poe.ninja exchange types because the two sites' category lists do
# not line up one-to-one (for example, poe.ninja's Verisium is not Atziri's
# Temple). Item-level English slugs are the matching key.
POE2DB_ECONOMY_PAGES = (
    ("通货", "Economy_Currency"),
    ("碎片", "Economy_Fragments"),
    ("驱灵仪式", "Economy_Ritual"),
    ("精华", "Economy_Essences"),
    ("裂隙", "Economy_Breach"),
    ("惊悸迷雾", "Economy_Delirium"),
    ("先祖秘藏", "Economy_Expedition"),
    ("符文", "Economy_Runes"),
    ("灵核", "Economy_Soul_Cores"),
    ("雕像", "Economy_Idols"),
    ("未切割的宝石", "Economy_Uncut_Gems"),
    ("深渊", "Economy_Abyss"),
    ("宝石", "Economy_Gems"),
    ("阿兹里神庙", "Economy_Atziris_Temple"),
)

# poe.ninja's supported PoE2 exchange API types. Verisium is queried as its
# own type and is not assigned to a Poe2DB page category; when a returned item
# has a Poe2DB English slug, localization supplies the correct page category.
EXCHANGE_CATEGORIES = (
    ("Currency", "通货"),
    ("Fragments", "碎片"),
    ("Ritual", "驱灵仪式"),
    ("Essences", "精华"),
    ("Breach", "裂隙"),
    ("Delirium", "惊悸迷雾"),
    ("Expedition", "先祖秘藏"),
    ("Runes", "符文"),
    ("SoulCores", "灵核"),
    ("Idols", "雕像"),
    ("UncutGems", "未切割的宝石"),
    ("Abyss", "深渊"),
    ("LineageSupportGems", "宝石"),
    ("Verisium", ""),
)
EXCHANGE_TYPES = frozenset(category[0] for category in EXCHANGE_CATEGORIES)


class EconomyError(RuntimeError):
    """The current PoE2 economy overview is unavailable or malformed."""


@dataclass(frozen=True)
class CurrencyRow:
    name: str
    value: float
    change_percent: float | None
    trend: tuple[float, ...]
    sell_value: float | None = None
    sell_change_percent: float | None = None
    unit_label: str | None = None
    sort_key: str = ""
    category: str = ""


@dataclass(frozen=True)
class CurrencySnapshot:
    league: str
    reference: str
    rows: tuple[CurrencyRow, ...]
    total_count: int
    generated_at: str
    market: str = "国际服"
    category_label: str = ""
    source_updated_at: str | None = None


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _safe_html(value: str) -> str:
    # AstrBot's HTML renderer treats the output as a Jinja template.
    return html.escape(value, quote=True).replace("{", "&#123;").replace("}", "&#125;")


def currency_key(name: str) -> str:
    """Join English API names to Poe2DB article slugs without a name table."""
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def localize_currency_snapshot(
    snapshot: CurrencySnapshot,
    chinese_names: dict[str, str | tuple[str, str]],
    chinese_league: str | None,
) -> CurrencySnapshot:
    reference_entry = chinese_names.get(currency_key(snapshot.reference))
    reference = (
        reference_entry[0]
        if isinstance(reference_entry, tuple) and reference_entry
        else reference_entry
    )
    if not isinstance(reference, str) or not reference:
        raise EconomyError("报价基准通货缺少中文名称")
    localized_rows: list[CurrencyRow] = []
    for row in snapshot.rows:
        key = row.sort_key or currency_key(row.name)
        entry = chinese_names.get(key) or chinese_names.get(currency_key(row.name))
        if isinstance(entry, str):
            name, category = entry, row.category
        elif isinstance(entry, tuple) and entry:
            name = entry[0]
            category = entry[1] if len(entry) > 1 else row.category
        else:
            continue
        if isinstance(name, str) and name:
            localized_rows.append(
                replace(row, name=name[:90], category=(category or row.category)[:60])
            )
    rows = tuple(localized_rows)
    if not rows:
        raise EconomyError("当前通货没有可用的中文名称")
    return replace(
        snapshot,
        league=(chinese_league or "当前赛季")[:100],
        reference=reference[:90],
        rows=rows,
        total_count=len(rows),
    )


class _AllowedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not NinjaEconomyClient._allowed(newurl):
            raise EconomyError("物价接口跳转到了指定来源范围以外")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class NinjaEconomyClient:
    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _allowed(url: str) -> bool:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            return False
        if (
            parts.scheme != "https"
            or parts.hostname != "poe.ninja"
            or port is not None
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            return False
        if parts.path == "/poe2/api/economy/leagues":
            return not parts.query
        if parts.path != "/poe2/api/economy/exchange/current/overview":
            return False
        params = parse_qs(parts.query)
        return (
            set(params) == {"league", "type"}
            and len(params["league"]) == 1
            and 0 < len(params["league"][0]) <= 100
            and len(params["type"]) == 1
            and params["type"][0] in EXCHANGE_TYPES
        )

    def _download_sync(self, url: str) -> Any:
        if not self._allowed(url):
            raise EconomyError("物价网址不在允许范围内")
        request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        opener = build_opener(_AllowedRedirects())
        try:
            with opener.open(request, timeout=self.timeout_seconds) as response:
                if not self._allowed(response.geturl()):
                    raise EconomyError("物价接口跳转到了指定来源范围以外")
                if response.headers.get_content_type() not in ("application/json", "text/plain"):
                    raise EconomyError("物价接口没有返回 JSON")
                data = response.read(MAX_JSON_BYTES + 1)
        except HTTPError as exc:
            raise EconomyError(f"物价接口返回 HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise EconomyError("连接物价接口失败或超时") from exc
        if len(data) > MAX_JSON_BYTES:
            raise EconomyError("物价响应过大，已停止读取")
        try:
            return json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise EconomyError("物价接口没有返回有效 JSON") from exc

    async def _get_json(
        self, url: str, cache_seconds: int, force_refresh: bool = False
    ) -> Any:
        cached = self._cache.get(url)
        if not force_refresh and cached and cached[0] > time.monotonic():
            return cached[1]
        async with self._lock:
            cached = self._cache.get(url)
            if not force_refresh and cached and cached[0] > time.monotonic():
                return cached[1]
            result = await asyncio.to_thread(self._download_sync, url)
            self._cache[url] = (time.monotonic() + cache_seconds, result)
            if len(self._cache) > 8:
                self._cache.pop(next(iter(self._cache)))
            return result

    async def current_currency(self, force_refresh: bool = False) -> CurrencySnapshot:
        league_id, league_name = await self._current_league(force_refresh)
        url = OVERVIEW_URL + "?" + urlencode({"league": league_id, "type": "Currency"})
        overview = await self._get_json(url, PRICES_CACHE_SECONDS, force_refresh)
        return parse_currency_overview(league_name, overview)

    async def current_economy(self, force_refresh: bool = False) -> CurrencySnapshot:
        """Fetch and merge the 14 exchange categories, quoted only in Divine Orbs."""
        league_id, league_name = await self._current_league(force_refresh)
        rows: list[CurrencyRow] = []
        for exchange_type, category_label in EXCHANGE_CATEGORIES:
            url = OVERVIEW_URL + "?" + urlencode(
                {"league": league_id, "type": exchange_type}
            )
            overview = await self._get_json(
                url, PRICES_CACHE_SECONDS, force_refresh=force_refresh
            )
            category_snapshot = parse_currency_overview(
                league_name,
                overview,
                category_label=category_label,
                require_divine=True,
                allow_empty=True,
            )
            rows.extend(category_snapshot.rows)

        # A source item may appear in more than one economy category. Keep one
        # row per stable Poe2DB/Wiki slug, preferring its higher Divine quote.
        unique_rows: dict[str, CurrencyRow] = {}
        for row in rows:
            key = row.sort_key or currency_key(row.name)
            current = unique_rows.get(key)
            if current is None or row.value > current.value:
                unique_rows[key] = row
        ordered_rows = tuple(
            sorted(unique_rows.values(), key=lambda row: (-row.value, row.name))
        )
        if not ordered_rows:
            raise EconomyError("14 个经济分类中没有神圣石报价")
        return CurrencySnapshot(
            league=league_name[:100],
            reference="Divine Orb",
            rows=ordered_rows,
            total_count=len(ordered_rows),
            generated_at=datetime.now(timezone(timedelta(hours=8))).strftime(
                "%Y-%m-%d %H:%M"
            ),
            category_label="14 类经济物品",
        )

    async def current_league_name(self) -> str:
        """Return the current challenge league name without fetching price data."""
        _, league_name = await self._current_league()
        return league_name

    async def _current_league(self, force_refresh: bool = False) -> tuple[str, str]:
        leagues = await self._get_json(
            LEAGUES_URL, LEAGUES_CACHE_SECONDS, force_refresh
        )
        if not isinstance(leagues, list) or not leagues or not isinstance(leagues[0], dict):
            raise EconomyError("未取得当前赛季")
        current = leagues[0]
        league_id = current.get("id")
        league_name = current.get("name")
        if not isinstance(league_id, str) or not 0 < len(league_id.strip()) <= 100:
            raise EconomyError("当前赛季名称无效")
        if not isinstance(league_name, str) or not league_name.strip():
            league_name = league_id
        return league_id.strip(), league_name.strip()


def parse_currency_overview(
    league: str,
    overview: object,
    category_label: str = "",
    require_divine: bool = False,
    allow_empty: bool = False,
) -> CurrencySnapshot:
    if not isinstance(overview, dict):
        raise EconomyError("通货数据格式不正确")
    core = overview.get("core")
    items = overview.get("items")
    lines = overview.get("lines")
    if not isinstance(core, dict) or not isinstance(items, list) or not isinstance(lines, list):
        raise EconomyError("通货数据缺少物品或价格")
    primary_id = core.get("primary")
    if not isinstance(primary_id, str) or not primary_id:
        raise EconomyError("通货数据缺少报价基准")
    reference_items = core.get("items")
    if not isinstance(reference_items, list):
        raise EconomyError("通货数据缺少报价基准名称")
    reference = primary_id
    for item in reference_items:
        if isinstance(item, dict) and item.get("id") == primary_id:
            candidate = item.get("name")
            if isinstance(candidate, str) and candidate:
                reference = candidate
            break
    if require_divine and currency_key(reference) not in {"divine", "divineorb"}:
        raise EconomyError("此经济分类不是以神圣石报价")
    names = {
        item["id"]: (item["name"], item.get("detailsId"))
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("name"), str)
        and item["name"]
    }
    rows: list[CurrencyRow] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        line_id = line.get("id")
        if not isinstance(line_id, str) or line_id not in names:
            continue
        value = _finite_number(line.get("primaryValue"))
        if value is None or value <= 0:
            continue
        sparkline = line.get("sparkline")
        if not isinstance(sparkline, dict):
            sparkline = {}
        change = _finite_number(sparkline.get("totalChange"))
        raw_trend = sparkline.get("data")
        trend = (
            tuple(number for value in raw_trend if (number := _finite_number(value)) is not None)[:14]
            if isinstance(raw_trend, list)
            else ()
        )
        item_name, details_id = names[line_id]
        stable_key = (
            currency_key(details_id)
            if isinstance(details_id, str) and details_id
            else currency_key(item_name)
        )
        rows.append(
            CurrencyRow(
                item_name[:90],
                value,
                change,
                trend,
                sort_key=stable_key,
                category=category_label,
            )
        )
    if not rows and not allow_empty:
        raise EconomyError("当前赛季没有可显示的通货价格")
    rows.sort(key=lambda row: (-row.value, row.name))
    generated_at = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    return CurrencySnapshot(
        league=league[:100],
        reference=reference[:90],
        rows=tuple(rows),
        total_count=len(rows),
        generated_at=generated_at,
    )


def _format_price(value: float) -> str:
    if value >= 10000:
        return f"{value / 10000:.2f}万"
    if value >= 1000:
        return f"{value / 1000:.2f}千"
    if value >= 10:
        return f"{value:.1f}"
    if value >= 1:
        return f"{value:.2f}"
    if value >= 0.1:
        return f"{value:.3f}"
    return f"{value:.5f}"


def _trend_svg(values: tuple[float, ...], positive: bool) -> str:
    if len(values) < 2:
        return '<span class="empty">—</span>'
    low, high = min(values), max(values)
    spread = high - low
    points = " ".join(
        f"{index * 104 / (len(values) - 1):.1f},{12 if not spread else 22 - (value - low) * 20 / spread:.1f}"
        for index, value in enumerate(values)
    )
    colour = "#35d5a7" if positive else "#f77983"
    return (
        '<svg width="108" height="26" viewBox="0 0 108 26" aria-hidden="true">'
        f'<polyline points="{points}" fill="none" stroke="{colour}" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    )


def render_currency_html(snapshot: CurrencySnapshot) -> str:
    """Build a self-contained image layout; data never becomes active markup."""
    is_national = snapshot.market == "国服"
    card_class = "card national" if is_national else "card"
    league = _safe_html(snapshot.league)
    reference = _safe_html(snapshot.reference)
    rows: list[str] = []
    display_rows = snapshot.rows[:IMAGE_ROWS]
    for index, row in enumerate(display_rows, 1):
        change = row.change_percent
        change_text = "—" if change is None else f"{change:+.1f}%"
        change_class = "flat" if change is None else ("up" if change >= 0 else "down")
        if is_national:
            sell_value = (
                "暂无"
                if row.sell_value is None or row.sell_value <= 0
                else _format_price(row.sell_value)
            )
            if row.sell_value is not None and row.unit_label:
                if row.sell_value > 0:
                    sell_value += " " + _safe_html(row.unit_label)
            buy_value = _format_price(row.value) if row.value > 0 else "暂无"
            if row.unit_label:
                buy_value += " " + _safe_html(row.unit_label)
            sell_change = row.sell_change_percent
            sell_change_text = "—" if sell_change is None else f"{sell_change:+.1f}%"
            sell_class = "flat" if sell_change is None else ("up" if sell_change >= 0 else "down")
            rows.append(
                "<tr>"
                f'<td class="rank">{index:02d}</td>'
                f'<td class="category">{_safe_html(row.category or snapshot.category_label)}</td>'
                f'<td class="name">{_safe_html(row.name)}</td>'
                f'<td class="metric">{buy_value}</td>'
                f'<td class="metric">{sell_value}</td>'
                f'<td class="delta {change_class}">{change_text}</td>'
                f'<td class="delta {sell_class}">{sell_change_text}</td>'
                "</tr>"
            )
        else:
            rows.append(
                "<tr>"
                f'<td class="rank">{index:02d}</td>'
                f'<td class="category">{_safe_html(row.category or snapshot.category_label or "通货")}</td>'
                f'<td class="name">{_safe_html(row.name)}</td>'
                f'<td class="price">{_format_price(row.value)}</td>'
                f'<td class="trend">{_trend_svg(row.trend, change is not None and change >= 0)}'
                f'<span class="{change_class}">{change_text}</span></td>'
                "</tr>"
            )
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
*{box-sizing:border-box}body{margin:0;padding:28px;background:#0d1520;color:#eaf3fb;
font:16px/1.5 Arial,"Microsoft YaHei","Noto Sans CJK SC",sans-serif}
.card{width:1000px;background:#192632;border:1px solid #304252;border-radius:16px;
padding:28px 32px 22px;box-shadow:0 12px 30px #06101d}
.eyebrow{font-size:13px;color:#28d1b0;font-weight:700;letter-spacing:2px}
h1{font-size:30px;line-height:1.25;margin:8px 0 10px}.meta{color:#a9bac7;font-size:14px;margin-bottom:22px}
.league{color:#fff;font-weight:700}.count{color:#a9bac7;font-size:13px;margin:0 0 10px}
table{width:100%;border-collapse:collapse;table-layout:fixed}th{color:#a9bac7;font-size:13px;
font-weight:600;text-align:left;background:#223340;padding:10px 12px;border-bottom:2px solid #17c7a8}
td{height:43px;padding:7px 12px;border-bottom:1px solid #2c3d4a;white-space:nowrap}
tbody tr:nth-child(even){background:#1d2b37}.rank{width:48px;color:#7490a3}
.category{width:128px;color:#9eb2c2;font-size:13px;overflow:hidden;text-overflow:ellipsis}
.name{width:40%;overflow:hidden;text-overflow:ellipsis;font-weight:600}
.price{width:160px;color:#d3e9ff;font-variant-numeric:tabular-nums;font-weight:700}
.national .category{width:116px}.national .name{width:31%}
.national .metric{width:14%;color:#d3e9ff;font-weight:700}
.national .delta{width:9%;text-align:right;font-variant-numeric:tabular-nums}
.trend{width:220px;display:flex;gap:12px;align-items:center;justify-content:flex-end;
font-variant-numeric:tabular-nums}.trend span{min-width:65px;text-align:right}
.up{color:#35d5a7}.down{color:#f77983}.flat,.empty{color:#9eb2c2}
footer{display:flex;justify-content:space-between;gap:16px;color:#9eb2c2;font-size:12px;margin-top:18px}
</style></head><body>
""" + f'<main class="{card_class}"><div class="eyebrow">流放之路 2 · 经济行情</div>' + (
        f"<h1>{'国服' if is_national else '国际服'}神圣石物价表</h1>"
        + (
            f'<div class="meta">{_safe_html(snapshot.category_label or "14 类经济物品")} · '
            "只显示以神圣石计价的条目</div>"
            if is_national
            else f'<div class="meta">赛季 <span class="league">{league}</span> · '
            f"价格单位：{reference} / 个 · {_safe_html(snapshot.category_label or '14 类经济物品')}</div>"
        )
        + (
            f'<p class="count">按国际服排序 · 只显示神圣石报价 · 显示 {len(display_rows)} / {snapshot.total_count} 项</p>'
            if is_national
            else f'<p class="count">按神圣石估价降序 · 显示 {len(display_rows)} / {snapshot.total_count} 项</p>'
        )
        + (
            '<table><thead><tr><th class="rank">#</th><th class="category">分类</th><th class="name">物品</th>'
            '<th class="metric">买入均价（神圣石）</th><th class="metric">卖出均价（神圣石）</th>'
            '<th class="delta">买入涨跌</th><th class="delta">卖出涨跌</th>'
            if is_national
            else '<table><thead><tr><th class="rank">#</th><th class="category">分类</th><th class="name">物品</th>'
            f'<th class="price">价格（{reference}）</th><th class="trend">7 天趋势</th>'
        )
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + (
            f"<footer><span>数据来源：poecurrency.top 国服行情 · OCR 统计仅供参考</span>"
            f"<span>源站时点：{_safe_html(snapshot.source_updated_at or '暂无')} · 表格更新：{_safe_html(snapshot.generated_at)} 北京时间</span></footer>"
            if is_national
            else f"<footer><span>价格：poe.ninja · 名称与分类：Poe2DB · 仅供参考</span>"
            f"<span>表格更新：{_safe_html(snapshot.generated_at)} 北京时间</span></footer>"
        )
        + "</main></body></html>"
    )
