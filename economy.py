"""PoE2 economy snapshots from Poe2Scout's public API."""

from __future__ import annotations

import asyncio
import html
import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SCOUT_API_BASE = "https://api.poe2scout.com"
LEAGUES_URL = f"{SCOUT_API_BASE}/poe2/Leagues"
USER_AGENT = "AstrBot-PoE2DB/1.13.0 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)"
MAX_JSON_BYTES = 2_000_000
LEAGUES_CACHE_SECONDS = 300
PRICES_CACHE_SECONDS = 1800
UNIQUE_ITEM_PRICE_CACHE_SECONDS = 60
IMAGE_ROWS = 50
SCOUT_PAGE_SIZE = 100
SCOUT_DATA_POINTS = 7
SCOUT_FREQUENCY_HOURS = 24

# Labels supplied by Poe2Scout are English. Keep the image table localized while
# allowing the API to add categories without requiring a plugin update.
SCOUT_CATEGORY_LABELS = {
    "currency": "通货",
    "fragments": "碎片",
    "runes": "符文",
    "essences": "精华",
    "ultimatum": "灵核",
    "expedition": "先祖秘藏",
    "ritual": "驱灵仪式",
    "vaultkeys": "圣物箱钥匙",
    "breach": "裂隙",
    "abyss": "深渊",
    "uncutgems": "未切割的宝石",
    "lineagesupportgems": "辅助宝石",
    "delirium": "惊悸迷雾",
    "idol": "雕像",
    "verisium": "Verisium",
    "vaal": "瓦尔",
}
_CATEGORY_API_ID = re.compile(r"^[a-z0-9-]{1,60}$")
_LEAGUE_PATH_SEGMENT = re.compile(r"^[^/?#]+$")


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
    icon_url: str | None = None


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


@dataclass(frozen=True)
class UniqueItemPrice:
    """A current unique-item quote and its available seven-day price points."""

    name: str
    text: str
    chinese_name: str | None
    base_type: str | None
    current_price: float | None
    current_quantity: int | None
    icon_url: str | None
    implicit_mods: tuple[str, ...]
    explicit_mods: tuple[str, ...]
    price_history: tuple[float, ...]
    poe2db_url: str | None
    traditional_name: str | None = None


UNIQUE_ITEM_QUERY_ALIASES = {
    "魔血": ("Mageblood",),
    "魔血腰带": ("Mageblood",),
    "法师之血": ("Mageblood",),
}

_UNIQUE_ITEM_MOD_TRANSLATIONS = {
    "has (1-3) charm slots": "具有 (1—3) 个咒符位",
    "(20-20)% of flask recovery applied instantly": "20% 的药剂回复量立即生效",
    "all mage's legacies have (25-50)% increased effect per duplicate mage's legacy you have": "你每拥有一个重复的法师遗产，所有法师遗产效果提高 (25—50)%",
}
_MAGE_LEGACY_NAMES = {
    "amethyst": "紫晶",
    "basalt": "玄武岩",
    "black sulphur": "黑锑",
    "diamond": "钻石",
    "gold": "黄金",
    "granite": "坚岩",
    "jade": "青玉",
    "quicksilver": "水银",
    "ruby": "红玉",
    "sapphire": "蓝玉",
    "silver": "白银",
    "sulphur": "硫磺",
    "topaz": "黄玉",
}


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


def _chinese_name_key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).split()).casefold()


def _localized_item_name_aliases(
    chinese: str | None,
    traditional: str | None,
) -> set[str]:
    """Index full, base-less, and mixed-script variants of paired labels."""
    labels = [
        value.strip()
        for value in (chinese, traditional)
        if isinstance(value, str)
        and value.strip()
        and any("\u3400" <= char <= "\u9fff" for char in value)
    ]
    aliases = set(labels)
    prefixes = {label.split(maxsplit=1)[0] for label in labels}
    aliases.update(prefixes)

    if len(labels) == 2 and len(labels[0]) == len(labels[1]):
        differing = sum(left != right for left, right in zip(labels[0], labels[1]))
        if differing <= 6:
            variants = {""}
            for left, right in zip(labels[0], labels[1]):
                choices = {left, right}
                variants = {prefix + char for prefix in variants for char in choices}
            aliases.update(variants)

        left_prefix = labels[0].split(maxsplit=1)[0]
        right_prefix = labels[1].split(maxsplit=1)[0]
        if len(left_prefix) == len(right_prefix):
            differing = sum(left != right for left, right in zip(left_prefix, right_prefix))
            if differing <= 6:
                variants = {""}
                for left, right in zip(left_prefix, right_prefix):
                    choices = {left, right}
                    variants = {
                        prefix + char for prefix in variants for char in choices
                    }
                aliases.update(variants)
    return {alias for alias in aliases if alias}


def _translate_unique_item_mod(mod: str) -> str | None:
    key = " ".join(unicodedata.normalize("NFKC", mod).split()).casefold()
    translated = _UNIQUE_ITEM_MOD_TRANSLATIONS.get(key)
    if translated:
        return translated
    match = re.fullmatch(r"legacy of ([a-z ]+)", key)
    if match:
        legacy_name = _MAGE_LEGACY_NAMES.get(match.group(1))
        return f"{legacy_name}遗产" if legacy_name else None
    return None


def _chinese_unique_item_mods(
    item: UniqueItemPrice, localized_mods: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """Prefer Poe2DB's Chinese item text and translate known Poe2Scout labels."""
    mods: list[str] = []
    seen: set[str] = set()
    candidates = [*localized_mods, *item.implicit_mods[:2], *item.explicit_mods[:6]]
    for candidate in candidates:
        text = candidate if any("\u3400" <= char <= "\u9fff" for char in candidate) else _translate_unique_item_mod(candidate)
        if not text:
            continue
        key = re.sub(r"[\s—–-]+", "", unicodedata.normalize("NFKC", text)).casefold()
        if key not in seen:
            seen.add(key)
            mods.append(text)
        if len(mods) >= 8:
            break
    return tuple(mods)


def _load_builtin_item_name_fallback(
    path: Path | None = None,
) -> tuple[
    dict[str, str],
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
]:
    """Load localized aliases and Poe2Scout search labels shipped with the plugin."""
    source_path = path or Path(__file__).with_name("item_name_fallback.json")
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}, {}
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}, {}, {}

    english_candidates: dict[str, set[str]] = {}
    chinese_candidates: dict[str, set[str]] = {}
    english_search_candidates: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        english = row.get("english")
        if not isinstance(english, str) or not english.strip():
            continue
        english = english.strip()
        short_english = row.get("name")
        if not isinstance(short_english, str) or not short_english.strip():
            short_english = english
        else:
            short_english = short_english.strip()
        english_labels = tuple(dict.fromkeys((short_english, english)))
        english_keys = tuple(
            dict.fromkeys(
                key for label in english_labels if (key := currency_key(label))
            )
        )
        if not english_keys:
            continue

        chinese = row.get("chinese")
        traditional = row.get("traditional")
        localized_aliases = _localized_item_name_aliases(chinese, traditional)

        if (
            isinstance(chinese, str)
            and chinese.strip()
            and any("\u3400" <= char <= "\u9fff" for char in chinese)
        ):
            for english_key in english_keys:
                english_candidates.setdefault(english_key, set()).add(chinese.strip())

        for english_key in english_keys:
            english_search_candidates.setdefault(english_key, set()).update(english_labels)
        for alias in localized_aliases:
            alias_key = _chinese_name_key(alias)
            if alias_key:
                chinese_candidates.setdefault(alias_key, set()).update(english_labels)

    english_to_chinese = {
        key: next(iter(values))
        for key, values in english_candidates.items()
        if len(values) == 1
    }
    chinese_to_english = {
        key: tuple(sorted(values, key=lambda value: (len(value), value.casefold())))
        for key, values in chinese_candidates.items()
    }
    english_to_search = {
        key: tuple(sorted(values, key=lambda value: (len(value), value.casefold())))
        for key, values in english_search_candidates.items()
    }
    return english_to_chinese, chinese_to_english, english_to_search


(
    SCOUT_ITEM_NAME_EN_TO_ZH,
    SCOUT_ITEM_NAME_ZH_TO_EN,
    SCOUT_ITEM_NAME_EN_TO_SEARCH,
) = _load_builtin_item_name_fallback()


def _load_builtin_item_traditional_names(
    path: Path | None = None,
) -> dict[str, str]:
    """Load unambiguous English-to-Traditional Chinese names from the bundle."""
    source_path = path or Path(__file__).with_name("item_name_fallback.json")
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}

    candidates: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        traditional = row.get("traditional")
        if (
            not isinstance(traditional, str)
            or not traditional.strip()
            or not any("\u3400" <= char <= "\u9fff" for char in traditional)
        ):
            continue
        for english in (row.get("english"), row.get("name")):
            if isinstance(english, str) and english.strip():
                key = currency_key(english)
                if key:
                    candidates.setdefault(key, set()).add(traditional.strip())
    return {
        key: next(iter(values))
        for key, values in candidates.items()
        if len(values) == 1
    }


SCOUT_ITEM_NAME_EN_TO_TW = _load_builtin_item_traditional_names()


def scout_item_english_names(chinese_name: str) -> tuple[str, ...]:
    """Return Poe2Scout English labels for an exact bundled CN or TW item name."""
    return SCOUT_ITEM_NAME_ZH_TO_EN.get(_chinese_name_key(chinese_name), ())


def scout_item_search_names(item_name: str) -> tuple[str, ...]:
    """Resolve a bundled CN/TW or English alias to Poe2Scout search labels."""
    if any("\u3400" <= char <= "\u9fff" for char in item_name):
        return scout_item_english_names(item_name)
    return SCOUT_ITEM_NAME_EN_TO_SEARCH.get(currency_key(item_name), ())


def scout_item_traditional_name(english_name: str) -> str | None:
    """Return the bundled Traditional Chinese name for an exact English item name."""
    return SCOUT_ITEM_NAME_EN_TO_TW.get(currency_key(english_name))


def _load_builtin_item_references(
    path: Path | None = None,
) -> dict[str, tuple[str, str]]:
    """Load unambiguous Poe2DB Simplified Chinese pages by English item name."""
    source_path = path or Path(__file__).with_name("item_name_fallback.json")
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}

    candidates: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        chinese = row.get("chinese")
        article_url = row.get("poe2db_url")
        if not isinstance(chinese, str) or not chinese.strip():
            continue
        if not isinstance(article_url, str):
            continue
        try:
            parts = urlsplit(article_url)
        except ValueError:
            continue
        if (
            parts.scheme != "https"
            or parts.hostname != "poe2db.tw"
            or parts.username is not None
            or parts.password is not None
            or parts.port is not None
            or not parts.path.startswith("/cn/")
            or parts.query
            or parts.fragment
        ):
            continue
        for english in (row.get("english"), row.get("name")):
            if isinstance(english, str) and english.strip():
                candidates.setdefault(currency_key(english), set()).add(
                    (chinese.strip(), article_url)
                )
    return {
        key: next(iter(values))
        for key, values in candidates.items()
        if key and len(values) == 1
    }


SCOUT_ITEM_REFERENCES = _load_builtin_item_references()


def scout_item_reference(english_name: str) -> tuple[str, str] | None:
    """Return a bundled Chinese title and article URL for an exact item name."""
    return SCOUT_ITEM_REFERENCES.get(currency_key(english_name))


def _builtin_unique_item_price(query: str) -> UniqueItemPrice | None:
    """Build a name-and-link result when Poe2Scout has no current quote."""
    search_names = scout_item_search_names(query)
    matches: dict[str, tuple[str, tuple[str, str]]] = {}
    for english_name in search_names:
        reference = scout_item_reference(english_name)
        if reference:
            matches.setdefault(reference[1], (english_name, reference))
    if len(matches) != 1:
        return None

    english_name, (chinese_name, article_url) = next(iter(matches.values()))
    return UniqueItemPrice(
        name=english_name,
        text=english_name,
        chinese_name=chinese_name,
        base_type=None,
        current_price=None,
        current_quantity=None,
        icon_url=None,
        implicit_mods=(),
        explicit_mods=(),
        price_history=(),
        poe2db_url=article_url,
        traditional_name=scout_item_traditional_name(english_name),
    )


def _safe_item_mods(value: object, limit: int = 8) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    mods: list[str] = []
    for candidate in value:
        if not isinstance(candidate, str):
            continue
        mod = " ".join("".join(char for char in candidate if char.isprintable()).split())
        if mod and len(mod) <= 240:
            mods.append(mod)
        if len(mods) >= limit:
            break
    return tuple(mods)


def _parse_unique_item(item: object) -> UniqueItemPrice | None:
    if not isinstance(item, dict):
        return None
    name = item.get("Name")
    text = item.get("Text")
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(text, str) or not text.strip():
        text = name
    name = name.strip()[:100]
    text = text.strip()[:160]
    metadata = item.get("ItemMetadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    reference = scout_item_reference(name) or scout_item_reference(text)
    chinese_name = reference[0] if reference else SCOUT_ITEM_NAME_EN_TO_ZH.get(currency_key(text))
    traditional_name = scout_item_traditional_name(name) or scout_item_traditional_name(text)
    current_price = _finite_number(item.get("CurrentPrice"))
    if current_price is not None and current_price <= 0:
        current_price = None
    quantity = item.get("CurrentQuantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        quantity = None
    logs = item.get("PriceLogs")
    history: list[tuple[float | None, int, float]] = []
    if isinstance(logs, list):
        for index, log in enumerate(logs[:7]):
            if not isinstance(log, dict):
                continue
            price = _finite_number(log.get("Price"))
            if price is None or price <= 0:
                continue
            history.append((_parse_timestamp(log.get("Time")), index, price))
    if history and all(timestamp is not None for timestamp, _, _ in history):
        history.sort(key=lambda entry: entry[0])
    else:
        # Poe2Scout returns its latest price log first when timestamps are absent.
        history.reverse()
    base_type = item.get("Type")
    if not isinstance(base_type, str) or not base_type.strip():
        base_type = metadata.get("base_type")
    if not isinstance(base_type, str) or not base_type.strip():
        base_type = None
    return UniqueItemPrice(
        name=name,
        text=text,
        chinese_name=chinese_name if isinstance(chinese_name, str) else None,
        base_type=base_type.strip()[:100] if base_type else None,
        current_price=current_price,
        current_quantity=quantity,
        icon_url=safe_currency_icon_url(item.get("IconUrl")),
        implicit_mods=_safe_item_mods(metadata.get("implicit_mods")),
        explicit_mods=_safe_item_mods(metadata.get("explicit_mods")),
        price_history=tuple(entry[2] for entry in history),
        poe2db_url=reference[1] if reference else None,
        traditional_name=traditional_name,
    )


def _format_unique_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 10:
        return f"{value:.1f}"
    if value >= 1:
        return f"{value:.2f}"
    if value >= 0.1:
        return f"{value:.3f}"
    return f"{value:.5f}"


def _format_price_history(values: tuple[float, ...]) -> str:
    if len(values) < 2:
        return "暂无足够的 7 日历史报价"
    bars = "▁▂▃▄▅▆▇█"
    low, high = min(values), max(values)
    span = high - low
    sparkline = "".join(
        bars[0]
        if span == 0
        else bars[min(7, max(0, round((value - low) * 7 / span)))]
        for value in values
    )
    change = (values[-1] / values[0] - 1) * 100 if values[0] else 0.0
    return f"{sparkline}（{change:+.1f}%）"


def format_unique_item_price_reply(
    item: UniqueItemPrice,
    market_url: str | None = None,
    article_url: str | None = None,
    localized_mods: tuple[str, ...] = (),
    traditional_name: str | None = None,
) -> str:
    """Format a concise Chinese single-item price reply."""
    title = item.chinese_name or item.name
    if title.casefold() != item.name.casefold():
        title += f"（{item.name}）"
    price = "暂无报价" if item.current_price is None else f"{_format_unique_price(item.current_price)} 神圣石"
    quantity = "暂无数据" if item.current_quantity is None else f"{item.current_quantity:,} 件"
    lines = [
        title,
        f"国际服当前价：{price}｜在售：{quantity}",
        f"7 日走势：{_format_price_history(item.price_history)}",
    ]
    traditional_name = " ".join((traditional_name or item.traditional_name or "").split())
    if traditional_name and traditional_name.casefold() != title.casefold():
        lines.insert(1, f"繁体名称：{traditional_name}")
    mods = _chinese_unique_item_mods(item, localized_mods)
    lines.append("主要词缀：" + ("；".join(mods) if mods else "暂无中文词缀资料"))
    article_url = article_url or item.poe2db_url
    if article_url:
        lines.append(f"Poe2DB 中文词条：{article_url}")
    if market_url:
        lines.append(f"国际服市集：{market_url}")
    else:
        lines.append("国际服市集：暂未找到可用链接")
    return "\n".join(lines)


def safe_currency_icon_url(value: object) -> str | None:
    """Allow only the static item image paths returned by our two price APIs."""
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.query or parts.fragment or parts.username or parts.password or port is not None:
        return None
    decoded_segments = [unquote(segment) for segment in parts.path.split("/")]
    if any(segment in {".", ".."} or "\\" in segment for segment in decoded_segments):
        return None
    path = parts.path
    # Poe2Scout currently emits web.poecdn.com URLs with a doubled slash before
    # gen/image. Normalize only that known CDN prefix.
    if path.startswith("//gen/image/"):
        path = path[1:]
    if not parts.scheme and not parts.netloc and path.startswith("/gen/image/"):
        return "https://web.poecdn.com" + path
    if (
        parts.scheme == "https"
        and parts.hostname == "web.poecdn.com"
        and path.startswith("/gen/image/")
    ):
        return "https://web.poecdn.com" + path
    if (
        parts.scheme == "https"
        and parts.hostname == "cdn.poe2db.tw"
        and parts.path.startswith("/image/")
    ):
        return "https://cdn.poe2db.tw" + parts.path
    return None


def localize_currency_snapshot(
    snapshot: CurrencySnapshot,
    chinese_names: dict[str, str | tuple[str, str]],
    chinese_league: str | None,
) -> CurrencySnapshot:
    reference_entry = chinese_names.get(currency_key(snapshot.reference))
    if reference_entry is None:
        reference_entry = SCOUT_ITEM_NAME_EN_TO_ZH.get(currency_key(snapshot.reference))
    reference = (
        reference_entry[0]
        if isinstance(reference_entry, tuple) and reference_entry
        else reference_entry
    )
    if not isinstance(reference, str) or not reference:
        reference = "神圣石" if currency_key(snapshot.reference) in {"divine", "divineorb"} else snapshot.reference
    localized_rows: list[CurrencyRow] = []
    for row in snapshot.rows:
        key = row.sort_key or currency_key(row.name)
        name_key = currency_key(row.name)
        entry = chinese_names.get(key)
        if entry is None:
            entry = chinese_names.get(name_key)
        if entry is None:
            entry = SCOUT_ITEM_NAME_EN_TO_ZH.get(name_key)
        if isinstance(entry, str):
            name, category = entry, row.category
        elif isinstance(entry, tuple) and entry:
            name = entry[0]
            category = entry[1] if len(entry) > 1 else row.category
        else:
            # Keep the live price row if the Chinese source has no translation
            # for a newly added item yet.
            name, category = row.name, row.category
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
        if not Poe2ScoutEconomyClient._allowed(newurl):
            raise EconomyError("物价接口跳转到了指定来源范围以外")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Poe2ScoutEconomyClient:
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
            or parts.hostname != "api.poe2scout.com"
            or port is not None
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            return False
        if parts.path == "/poe2/Leagues":
            return not parts.query
        match = re.fullmatch(
            r"/poe2/Leagues/([^/]+)/(Items/Categories|ReferenceCurrencies|Currencies/ByCategory|Uniques/ByCategory)",
            parts.path,
        )
        if not match:
            return False
        league_segment, endpoint = match.groups()
        if not _LEAGUE_PATH_SEGMENT.fullmatch(league_segment):
            return False
        decoded_league = unquote(league_segment)
        if (
            not decoded_league
            or len(decoded_league) > 100
            or "/" in decoded_league
            or "\\" in decoded_league
            or decoded_league in {".", ".."}
            or any(ord(character) < 32 for character in decoded_league)
        ):
            return False
        if endpoint in {"Items/Categories", "ReferenceCurrencies"}:
            return not parts.query
        params = parse_qs(parts.query, keep_blank_values=True)
        required = {
            "category",
            "page",
            "perPage",
            "dataPoints",
            "frequencyHours",
            "referenceCurrency",
        }
        if endpoint == "Uniques/ByCategory":
            required.add("search")
        if set(params) != required or any(len(values) != 1 for values in params.values()):
            return False
        category = params["category"][0]
        if not _CATEGORY_API_ID.fullmatch(category) or params["referenceCurrency"][0] != "divine":
            return False
        if endpoint == "Uniques/ByCategory":
            search = params["search"][0].strip()
            if (
                not search
                or len(search) > 100
                or any(ord(character) < 32 for character in search)
            ):
                return False
        try:
            page = int(params["page"][0])
            per_page = int(params["perPage"][0])
            data_points = int(params["dataPoints"][0])
            frequency_hours = int(params["frequencyHours"][0])
        except ValueError:
            return False
        return (
            1 <= page <= 1000
            and 1 <= per_page <= SCOUT_PAGE_SIZE
            and 1 <= data_points <= 30
            and 1 <= frequency_hours <= 168
        )

    async def unique_item_prices(
        self, query: str, force_refresh: bool = False
    ) -> tuple[UniqueItemPrice, ...]:
        """Find exact unique-item matches with current Divine prices and history."""
        query = " ".join(query.strip().split())
        if not query or len(query) > 80 or "://" in query:
            raise EconomyError("请提供 1–80 字的传奇物品名称")
        if any(ord(character) < 32 for character in query):
            raise EconomyError("物品名称中包含无效字符")

        normalized = _chinese_name_key(query)
        alias_terms = next(
            (
                terms
                for alias, terms in UNIQUE_ITEM_QUERY_ALIASES.items()
                if _chinese_name_key(alias) == normalized
            ),
            (),
        )
        search_terms = alias_terms or scout_item_search_names(query) or (query,)
        league_name = await self._current_league(force_refresh)
        league_path = quote(league_name, safe="")
        categories_url = (
            f"{SCOUT_API_BASE}/poe2/Leagues/{league_path}/Items/Categories"
        )
        categories_payload = await self._get_json(
            categories_url, LEAGUES_CACHE_SECONDS, force_refresh
        )
        categories = (
            categories_payload.get("UniqueCategories")
            if isinstance(categories_payload, dict)
            else None
        )
        valid_categories = [
            category["ApiId"]
            for category in categories or []
            if isinstance(category, dict)
            and isinstance(category.get("ApiId"), str)
            and _CATEGORY_API_ID.fullmatch(category["ApiId"])
        ]
        if not valid_categories:
            raise EconomyError("Poe2Scout 没有提供可查询的传奇物品分类")

        exact: dict[tuple[str, str | None, str], UniqueItemPrice] = {}
        for search_term in search_terms[:3]:
            for category in valid_categories:
                params = urlencode(
                    {
                        "category": category,
                        "page": 1,
                        "perPage": 100,
                        "dataPoints": 7,
                        "frequencyHours": 24,
                        "referenceCurrency": "divine",
                        "search": search_term,
                    }
                )
                url = (
                    f"{SCOUT_API_BASE}/poe2/Leagues/{league_path}"
                    f"/Uniques/ByCategory?{params}"
                )
                payload = await self._get_json(
                    url, UNIQUE_ITEM_PRICE_CACHE_SECONDS, force_refresh
                )
                raw_items = payload.get("Items") if isinstance(payload, dict) else None
                if not isinstance(raw_items, list):
                    raise EconomyError("Poe2Scout 传奇物品数据格式不正确")
                target = currency_key(search_term)
                for raw_item in raw_items:
                    parsed = _parse_unique_item(raw_item)
                    if parsed is None:
                        continue
                    if target not in {
                        currency_key(parsed.name),
                        currency_key(parsed.text),
                    }:
                        continue
                    exact[(parsed.name.casefold(), parsed.base_type, parsed.text.casefold())] = parsed

        if not exact:
            fallback_item = _builtin_unique_item_price(query)
            if fallback_item:
                return (fallback_item,)
            raise EconomyError("没有找到精确匹配的传奇物品，请检查名称或尝试英文名")
        return tuple(exact.values())

    def _download_sync(self, url: str) -> Any:
        if not self._allowed(url):
            raise EconomyError("物价网址不在允许范围内")
        request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
        opener = build_opener(_AllowedRedirects())
        for attempt in range(2):
            try:
                with opener.open(request, timeout=self.timeout_seconds) as response:
                    if not self._allowed(response.geturl()):
                        raise EconomyError("物价接口跳转到了指定来源范围以外")
                    if response.headers.get_content_type() not in ("application/json", "text/plain"):
                        raise EconomyError("物价接口没有返回 JSON")
                    data = response.read(MAX_JSON_BYTES + 1)
                break
            except HTTPError as exc:
                raise EconomyError(f"物价接口返回 HTTP {exc.code}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
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
            if len(self._cache) > 256:
                self._cache.pop(next(iter(self._cache)))
            return result

    async def current_economy(self, force_refresh: bool = False) -> CurrencySnapshot:
        """Fetch current categories, requesting all prices in Divine Orbs."""
        league_name = await self._current_league(force_refresh)
        league_path = quote(league_name, safe="")
        reference_url = f"{SCOUT_API_BASE}/poe2/Leagues/{league_path}/ReferenceCurrencies"
        references = await self._get_json(
            reference_url, LEAGUES_CACHE_SECONDS, force_refresh
        )
        if not isinstance(references, list) or not any(
            isinstance(item, dict) and item.get("ApiId") == "divine"
            for item in references
        ):
            raise EconomyError("Poe2Scout 当前赛季没有可用的神圣石报价基准")

        categories_url = f"{SCOUT_API_BASE}/poe2/Leagues/{league_path}/Items/Categories"
        categories_payload = await self._get_json(
            categories_url, LEAGUES_CACHE_SECONDS, force_refresh
        )
        if not isinstance(categories_payload, dict):
            raise EconomyError("Poe2Scout 经济分类数据格式不正确")
        categories = categories_payload.get("CurrencyCategories")
        if not isinstance(categories, list) or not categories:
            raise EconomyError("Poe2Scout 当前赛季没有经济分类")

        rows: list[CurrencyRow] = []
        valid_categories = [
            category
            for category in categories
            if isinstance(category, dict)
            and isinstance(category.get("ApiId"), str)
            and _CATEGORY_API_ID.fullmatch(category["ApiId"])
        ]
        if not valid_categories:
            raise EconomyError("Poe2Scout 当前赛季没有可用的经济分类")

        for category in valid_categories:
            category_id = category["ApiId"]
            source_label = category.get("Label")
            category_label = SCOUT_CATEGORY_LABELS.get(
                category_id,
                source_label[:60] if isinstance(source_label, str) else category_id,
            )
            page = 1
            total_pages: int | None = None
            while page <= (total_pages if total_pages is not None else 1):
                query = urlencode(
                    {
                        "category": category_id,
                        "page": page,
                        "perPage": SCOUT_PAGE_SIZE,
                        "dataPoints": SCOUT_DATA_POINTS,
                        "frequencyHours": SCOUT_FREQUENCY_HOURS,
                        "referenceCurrency": "divine",
                    }
                )
                url = (
                    f"{SCOUT_API_BASE}/poe2/Leagues/{league_path}"
                    f"/Currencies/ByCategory?{query}"
                )
                payload = await self._get_json(
                    url, PRICES_CACHE_SECONDS, force_refresh=force_refresh
                )
                category_rows, pages = parse_scout_currency_page(
                    league_name, payload, category_label
                )
                if not isinstance(payload, dict) or payload.get("CurrentPage") != page:
                    raise EconomyError("Poe2Scout 返回了错误的物价分页")
                rows.extend(category_rows)
                if total_pages is None:
                    total_pages = pages
                    if total_pages > 100:
                        raise EconomyError("Poe2Scout 返回的分页数量超出安全上限")
                elif pages != total_pages:
                    raise EconomyError("Poe2Scout 经济分类分页信息不一致")
                page += 1

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
            raise EconomyError("Poe2Scout 经济分类中没有神圣石报价")
        divine = next(
            (row for row in ordered_rows if row.sort_key == "divine"), None
        )
        if divine is None or not math.isclose(divine.value, 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise EconomyError("Poe2Scout 没有按神圣石单位返回价格")
        return CurrencySnapshot(
            league=league_name[:100],
            reference="Divine Orb",
            rows=ordered_rows,
            total_count=len(ordered_rows),
            generated_at=datetime.now(timezone(timedelta(hours=8))).strftime(
                "%Y-%m-%d %H:%M"
            ),
            category_label=f"{len(valid_categories)} 类经济物品",
        )

    async def current_league_name(self) -> str:
        """Return the current challenge league name without fetching price data."""
        return await self._current_league()

    async def _current_league(self, force_refresh: bool = False) -> str:
        leagues = await self._get_json(
            LEAGUES_URL, LEAGUES_CACHE_SECONDS, force_refresh
        )
        if not isinstance(leagues, list):
            raise EconomyError("未取得当前赛季")
        current = next(
            (item for item in leagues if isinstance(item, dict) and item.get("IsCurrent") is True),
            None,
        )
        if current is None:
            raise EconomyError("Poe2Scout 未标记当前赛季")
        league_name = current.get("Value")
        if not isinstance(league_name, str) or not 0 < len(league_name.strip()) <= 100:
            raise EconomyError("当前赛季名称无效")
        return league_name.strip()


def parse_scout_currency_page(
    league: str, payload: object, category_label: str = ""
) -> tuple[list[CurrencyRow], int]:
    if not isinstance(payload, dict) or not isinstance(payload.get("Items"), list):
        raise EconomyError("Poe2Scout 通货分页数据格式不正确")
    pages = payload.get("Pages")
    current_page = payload.get("CurrentPage")
    if (
        isinstance(pages, bool)
        or not isinstance(pages, int)
        or pages < 0
        or isinstance(current_page, bool)
        or not isinstance(current_page, int)
        or current_page < 1
    ):
        raise EconomyError("Poe2Scout 通货分页信息无效")
    rows: list[CurrencyRow] = []
    for item in payload["Items"]:
        if not isinstance(item, dict):
            continue
        name = item.get("Text")
        if not isinstance(name, str) or not name.strip():
            continue
        value = _finite_number(item.get("CurrentPrice"))
        if value is None or value <= 0:
            continue
        api_id = item.get("ApiId")
        stable_key = currency_key(api_id) if isinstance(api_id, str) else ""
        raw_logs = item.get("PriceLogs")
        logs: list[tuple[float | None, int, float]] = []
        if isinstance(raw_logs, list):
            for index, log in enumerate(raw_logs):
                if not isinstance(log, dict):
                    continue
                price = _finite_number(log.get("Price"))
                if price is None or price <= 0:
                    continue
                timestamp = _parse_timestamp(log.get("Time"))
                logs.append((timestamp, index, price))
        if logs and all(timestamp is not None for timestamp, _, _ in logs):
            logs.sort(key=lambda entry: entry[0])
        else:
            # Poe2Scout currently returns newest-first; reverse for the chart.
            logs.reverse()
        trend = tuple(entry[2] for entry in logs[:14])
        change = None
        if len(trend) >= 2 and trend[0] > 0:
            change = (trend[-1] / trend[0] - 1) * 100
            if not math.isfinite(change):
                change = None
        rows.append(
            CurrencyRow(
                name.strip()[:90],
                value,
                change,
                trend,
                sort_key=stable_key,
                category=category_label[:60],
                icon_url=safe_currency_icon_url(item.get("IconUrl")),
            )
        )
    return rows, pages


def _parse_timestamp(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        timestamp = datetime.fromisoformat(normalized)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.timestamp()
    except (ValueError, OverflowError):
        return None


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
        icon_url = safe_currency_icon_url(row.icon_url)
        icon = (
            f'<img class="item-icon" src="{_safe_html(icon_url)}" alt="">'
            if icon_url
            else ""
        )
        name_cell = (
            f'<span class="item-name-cell">{icon}'
            f'<span class="name-label">{_safe_html(row.name)}</span></span>'
        )
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
                f'<td class="name">{name_cell}</td>'
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
                f'<td class="name">{name_cell}</td>'
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
.name{width:40%;overflow:hidden;font-weight:600}
.item-name-cell{display:flex;align-items:center;gap:10px;min-width:0;overflow:hidden}
.item-icon{width:30px;height:30px;object-fit:contain;flex:0 0 30px}
.name-label{min-width:0;overflow:hidden;text-overflow:ellipsis}
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
