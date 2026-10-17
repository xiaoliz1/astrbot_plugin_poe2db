"""Build the bundled Poe2Scout item name and link table.

The script joins current-season Poe2Scout item labels to the item listings on
Poe2DB's English, Simplified Chinese, and Traditional Chinese pages by their
shared article slug. Only unambiguous Poe2DB matches are localized; unmatched
names remain in the output with localized fields set to null so the caller can
safely retain English.
Each row also includes the Poe2Wiki page and a current-league official Trade
search URL whenever Poe2Scout exposes enough item metadata to build one.

Run from the plugin directory with:

    py -3.10 tools/generate_item_name_fallback.py

The script makes requests sequentially and waits between requests to keep the
Poe2DB crawl modest. It does not download or store live prices or item images.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen


PLUGIN_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PLUGIN_DIR / "item_name_fallback.json"
POE2SCOUT_API = "https://api.poe2scout.com"
POE2DB_BASE = "https://poe2db.tw"
POE2WIKI_BASE = "https://www.poe2wiki.net/wiki/"
POE_TRADE_BASE = "https://www.pathofexile.com"
USER_AGENT = "AstrBot-PoE2DB-NameTable/1.0 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)"
MAX_RESPONSE_BYTES = 12_000_000
ITEM_LINK_CLASSES = {"UniqueItem", "whiteitem", "item_currency", "gemitem", "currencyitem"}
SPECIAL_INDEX_PAGES = {
    "Unique_item",
    "Gem",
    "Flasks",
    "Essence",
    "Splinter",
    "Catalysts",
    "Strongbox",
    "Hideout",
    "Lineage_Supports",
}


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, str]] = []
        self._anchor: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._anchor = {key: value or "" for key, value in attrs}
            self._anchor["text"] = ""

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor is not None:
            self.anchors.append(self._anchor)
            self._anchor = None


def fetch_text(url: str, timeout: int) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/html;q=0.9,*/*;q=0.8"})
    with urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeds {MAX_RESPONSE_BYTES} bytes: {url}")
    return body.decode("utf-8", "replace")


def get_json(url: str, timeout: int) -> object:
    return json.loads(fetch_text(url, timeout))


def parse_anchors(page: str) -> list[dict[str, str]]:
    parser = AnchorParser()
    parser.feed(page)
    return parser.anchors


def category_pages(items_index: str, locale: str = "cn") -> list[str]:
    if locale not in {"cn", "tw"}:
        raise ValueError(f"Unsupported Poe2DB locale: {locale}")
    locale_prefix = f"/{locale}/"
    pages: set[str] = set()
    for anchor in parse_anchors(items_index):
        href = anchor.get("href", "").strip()
        path = href.split("?", 1)[0].split("#", 1)[0]
        classes = set(anchor.get("class", "").split())
        if "ItemClasses" in classes:
            if path.startswith(locale_prefix):
                pages.add(path)
            elif path and not path.startswith(("/", "#", "http", "javascript:")):
                pages.add(locale_prefix + path.lstrip("/"))
        elif path.startswith(locale_prefix) and path.rstrip("/").rsplit("/", 1)[-1] in SPECIAL_INDEX_PAGES:
            pages.add(path)
    pages.update(locale_prefix + page for page in SPECIAL_INDEX_PAGES)

    # The current league's mechanic page can contain currency or rune item
    # cards that are not part of the generic item-class index.
    for anchor in parse_anchors(items_index):
        href = anchor.get("href", "").strip()
        path = href.split("?", 1)[0].split("#", 1)[0]
        classes = set(anchor.get("class", "").split())
        if "league_bar_icon" in classes:
            if path.startswith(locale_prefix):
                pages.add(path)
            elif path and not path.startswith(("/", "#", "http", "javascript:")):
                pages.add(locale_prefix + path.lstrip("/"))
    return sorted(pages)


def canonical_slug(href: str, locale: str) -> str | None:
    href = unquote(href.strip())
    if not href or href.startswith(("#", "javascript:")):
        return None
    if href.startswith("http://") or href.startswith("https://"):
        parts = urlsplit(href)
        if parts.hostname != "poe2db.tw":
            return None
        path = parts.path
    else:
        path = urlsplit(href).path
    path = path.strip("/")
    if not path:
        return None
    pieces = path.split("/")
    if pieces[0] in {"cn", "us", "tw"}:
        if len(pieces) < 2 or pieces[0] != locale:
            return None
        pieces = pieces[1:]
    if len(pieces) != 1:
        return None
    slug = pieces[0]
    if not slug or slug.casefold() in {"items", "marked"}:
        return None
    return slug


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9]", "", value)


def has_cjk(value: str) -> bool:
    return any("\u3400" <= char <= "\u9fff" for char in value)


def item_labels(page: str, locale: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for anchor in parse_anchors(page):
        classes = set(anchor.get("class", "").split())
        if not ITEM_LINK_CLASSES.intersection(classes):
            continue
        label = " ".join(anchor.get("text", "").split())
        slug = canonical_slug(anchor.get("href", ""), locale)
        if slug and label:
            # Repeated image and text links point to the same article. Keep
            # the visible text anchor and ignore empty image wrappers.
            previous = labels.get(slug)
            if previous is None or len(label) > len(previous):
                labels[slug] = label
    return labels


def collect_scout_catalog(
    timeout: int,
) -> tuple[str, dict[str, object], dict[str, object], list[dict[str, object]]]:
    leagues = get_json(f"{POE2SCOUT_API}/poe2/Leagues", timeout)
    if not isinstance(leagues, list):
        raise ValueError("Poe2Scout leagues response is not an array")
    current = next((row for row in leagues if isinstance(row, dict) and row.get("IsCurrent") is True), None)
    if not current or not isinstance(current.get("Value"), str):
        raise ValueError("Poe2Scout did not identify a current league")
    league = current["Value"]
    realms = get_json(f"{POE2SCOUT_API}/Realms", timeout)
    if not isinstance(realms, list):
        raise ValueError("Poe2Scout realms response is not an array")
    realm = next(
        (
            row
            for row in realms
            if isinstance(row, dict)
            and row.get("GameApiId") == "poe2"
            and row.get("Value") == "poe2/poe2"
        ),
        None,
    )
    if not realm or not isinstance(realm.get("RealmApiId"), str) or not isinstance(realm.get("TradeApiPath"), str):
        raise ValueError("Poe2Scout did not expose the Path of Exile 2 trade realm")
    encoded_league = quote(league, safe="")
    items = get_json(f"{POE2SCOUT_API}/poe2/Leagues/{encoded_league}/Items", timeout)
    if not isinstance(items, list) or not all(isinstance(row, dict) for row in items):
        raise ValueError("Poe2Scout item response is not an array of objects")
    return league, current, realm, items


def collect_poe2db_matches(
    cn_pages: list[str], tw_pages: list[str], timeout: int, delay: float
) -> dict[str, set[tuple[str, str, str, str]]]:
    matches: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
    failures: list[str] = []
    tw_pages_by_slug = {
        path.removeprefix("/tw/"): path for path in tw_pages if path.startswith("/tw/")
    }
    for index, cn_path in enumerate(cn_pages, 1):
        slug = cn_path.removeprefix("/cn/")
        cn_url = POE2DB_BASE + cn_path
        tw_path = tw_pages_by_slug.get(slug)
        tw_url = POE2DB_BASE + tw_path if tw_path else None
        us_url = POE2DB_BASE + "/us/" + slug
        try:
            cn_html = fetch_text(cn_url, timeout)
            time.sleep(delay)
            us_html = fetch_text(us_url, timeout)
            time.sleep(delay)
            tw_html = ""
            if tw_url:
                try:
                    tw_html = fetch_text(tw_url, timeout)
                except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                    print(f"[{index}/{len(cn_pages)}] skip {tw_path}: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(delay)
            cn_labels = item_labels(cn_html, "cn")
            us_labels = item_labels(us_html, "us")
            tw_labels = item_labels(tw_html, "tw") if tw_html else {}
            for item_slug in cn_labels.keys() & us_labels.keys():
                english = us_labels[item_slug]
                chinese = cn_labels[item_slug]
                traditional = tw_labels.get(item_slug, "")
                if not has_cjk(traditional):
                    traditional = ""
                key = normalized_name(english)
                if key and chinese and has_cjk(chinese) and chinese != english:
                    matches[key].add((english, chinese, traditional, item_slug))
            paired_count = len(cn_labels.keys() & us_labels.keys())
            print(f"[{index}/{len(cn_pages)}] {slug}: {paired_count} paired item links, {len(tw_labels)} Traditional labels", flush=True)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            failures.append(f"{cn_path}: {type(exc).__name__}: {exc}")
            print(f"[{index}/{len(cn_pages)}] skip {cn_path}: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(delay)
    if failures:
        print(f"Poe2DB pages unavailable: {len(failures)}", file=sys.stderr)
    return matches


def wiki_url_for_item(item: dict[str, object]) -> str | None:
    # The Poe2Scout UI uses a unique item's name and otherwise its visible text.
    value = item.get("Name") or item.get("Text")
    if not isinstance(value, str) or not value.strip():
        return None
    return POE2WIKI_BASE + quote(" ".join(value.split()), safe="-_.!~*'()")


def trade_url_for_item(
    item: dict[str, object],
    league: str,
    realm: dict[str, object],
    league_metadata: dict[str, object],
) -> str | None:
    trade_api_path = realm.get("TradeApiPath")
    realm_api_id = realm.get("RealmApiId")
    if not isinstance(trade_api_path, str) or not isinstance(realm_api_id, str):
        return None
    base_url = f"{POE_TRADE_BASE}/{trade_api_path}"
    encoded_league = quote(league.casefold(), safe="")

    item_name = item.get("Name")
    if isinstance(item_name, str) and item_name.strip():
        query = {"query": {"filters": {}, "name": " ".join(item_name.split())}}
        path = f"{base_url}/search/{realm_api_id}/{encoded_league}"
    else:
        api_id = item.get("ApiId")
        if not isinstance(api_id, str) or not api_id.strip():
            return None
        base_currency_api_id = league_metadata.get("BaseCurrencyApiId")
        if not isinstance(base_currency_api_id, str) or not base_currency_api_id.strip():
            default_currency = league_metadata.get("DefaultCurrency")
            base_currency_api_id = default_currency.get("ApiId") if isinstance(default_currency, dict) else None
        if not isinstance(base_currency_api_id, str) or not base_currency_api_id.strip():
            return None
        query = {
            "exchange": {
                "status": {"option": "online"},
                "have": [base_currency_api_id],
                "want": [api_id],
            }
        }
        path = f"{base_url}/exchange/{realm_api_id}/{encoded_league}"

    encoded_query = quote(json.dumps(query, separators=(",", ":"), ensure_ascii=False), safe="-_.!~*'()")
    return f"{path}?q={encoded_query}"


def build_rows(
    scout_items: list[dict[str, object]],
    matches: dict[str, set[tuple[str, str, str, str]]],
    league: str,
    realm: dict[str, object],
    league_metadata: dict[str, object],
) -> tuple[list[dict[str, object]], list[str]]:
    rows: list[dict[str, object]] = []
    unmatched: list[str] = []
    for item in scout_items:
        english = item.get("Text")
        if not isinstance(english, str) or not english.strip():
            continue
        english = " ".join(english.split())
        candidates = matches.get(normalized_name(english), set())
        chinese_values = {candidate[1] for candidate in candidates}
        matched = len(chinese_values) == 1
        chinese = next(iter(chinese_values)) if matched else None
        traditional_values = {candidate[2] for candidate in candidates if candidate[2]}
        traditional = next(iter(traditional_values)) if len(traditional_values) == 1 else None
        if not matched:
            unmatched.append(english)
        candidate = sorted(candidates)[0] if matched else None
        rows.append(
            {
                "english": english,
                "chinese": chinese,
                "traditional": traditional,
                "category": item.get("CategoryApiId"),
                "name": item.get("Name"),
                "type": item.get("Type"),
                "api_id": item.get("ApiId"),
                "base_item_type_id": item.get("BaseItemTypeId"),
                "poe2db_url": f"{POE2DB_BASE}/cn/{candidate[3]}" if candidate else None,
                "wiki_url": wiki_url_for_item(item),
                "trade_url": trade_url_for_item(item, league, realm, league_metadata),
            }
        )
    rows.sort(key=lambda row: (str(row.get("category") or ""), str(row["english"]).casefold()))
    return rows, unmatched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=0.6, help="seconds to wait between Poe2DB requests (default: 0.6)")
    parser.add_argument("--timeout", type=int, default=25, help="request timeout in seconds")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="output JSON file")
    args = parser.parse_args()
    if args.delay < 0 or args.timeout < 1:
        parser.error("--delay must be nonnegative and --timeout must be positive")

    league, league_metadata, realm, scout_items = collect_scout_catalog(args.timeout)
    cn_index_html = fetch_text(POE2DB_BASE + "/cn/Items", args.timeout)
    time.sleep(args.delay)
    tw_index_html = fetch_text(POE2DB_BASE + "/tw/Items", args.timeout)
    time.sleep(args.delay)
    cn_pages = category_pages(cn_index_html, "cn")
    tw_pages = category_pages(tw_index_html, "tw")
    print(f"Poe2Scout current league {league}: {len(scout_items)} catalog rows", flush=True)
    print(f"Poe2DB CN/TW index pages to compare: {len(cn_pages)}/{len(tw_pages)}", flush=True)

    matches = collect_poe2db_matches(cn_pages, tw_pages, args.timeout, args.delay)
    rows, unmatched = build_rows(scout_items, matches, league, realm, league_metadata)
    mapped_count = sum(row["chinese"] is not None for row in rows)
    traditional_count = sum(row["traditional"] is not None for row in rows)
    document = {
        "schema_version": 3,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "league": league,
        "trade_league": league,
        "catalog_count": len(rows),
        "matched_count": mapped_count,
        "unmatched_count": len(rows) - mapped_count,
        "traditional_matched_count": traditional_count,
        "sources": {
            "catalog": f"{POE2SCOUT_API}/poe2/Leagues/{quote(league, safe='')}/Items",
            "chinese_names": "https://poe2db.tw/cn/Items",
            "traditional_names": "https://poe2db.tw/tw/Items",
            "english_names": "https://poe2db.tw/us/Items",
            "wiki": "https://www.poe2wiki.net/",
            "trade": "https://www.pathofexile.com/trade2/",
            "attribution": "Poe2DB content is credited to poe2db.tw and is licensed CC BY-NC-SA 3.0",
            "license_url": "https://creativecommons.org/licenses/by-nc-sa/3.0/",
        },
        "items": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}", flush=True)
    print(f"Matched {mapped_count}/{len(rows)} Poe2Scout item names ({100 * mapped_count / max(1, len(rows)):.1f}%)", flush=True)
    print(f"Matched {traditional_count}/{len(rows)} Traditional Chinese item names", flush=True)
    if unmatched:
        print("Unmatched sample:", "; ".join(unmatched[:30]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
