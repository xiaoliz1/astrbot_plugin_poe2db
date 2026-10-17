"""Poe2Scout price acquisition, conversion, and image markup safety."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.economy import (
    EconomyError,
    LEAGUES_URL,
    SCOUT_API_BASE,
    CurrencyRow,
    CurrencySnapshot,
    Poe2ScoutEconomyClient,
    localize_currency_snapshot,
    parse_scout_currency_page,
    render_currency_html,
    safe_currency_icon_url,
)


def scout_item(api_id, name, price, logs=None, icon="/gen/image/item.webp"):
    return {
        "ApiId": api_id,
        "Text": name,
        "CurrentPrice": price,
        "PriceLogs": logs or [],
        "IconUrl": icon,
    }


def scout_page(items, pages=1, current_page=1):
    return {"CurrentPage": current_page, "Pages": pages, "Total": len(items), "Items": items}


class EconomyTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_season_uses_is_current_and_encodes_league_path(self):
        class FixtureClient(Poe2ScoutEconomyClient):
            async def _get_json(self, url, cache_seconds, force_refresh=False):
                self.url = url
                return [
                    {"Value": "Old Season", "IsCurrent": False},
                    {"Value": "Forbidden Rites", "IsCurrent": True},
                ]

        client = FixtureClient()
        self.assertEqual(await client.current_league_name(), "Forbidden Rites")
        self.assertEqual(client.url, LEAGUES_URL)
        self.assertEqual(
            client._allowed(
                f"{SCOUT_API_BASE}/poe2/Leagues/Forbidden%20Rites/Items/Categories"
            ),
            True,
        )

    def test_only_expected_poe2scout_urls_and_divine_queries_are_allowed(self):
        allowed = Poe2ScoutEconomyClient._allowed
        self.assertTrue(allowed(LEAGUES_URL))
        self.assertTrue(
            allowed(
                f"{SCOUT_API_BASE}/poe2/Leagues/Forbidden%20Rites/Items/Categories"
            )
        )
        self.assertTrue(
            allowed(
                f"{SCOUT_API_BASE}/poe2/Leagues/Forbidden%20Rites/ReferenceCurrencies"
            )
        )
        self.assertTrue(
            allowed(
                f"{SCOUT_API_BASE}/poe2/Leagues/Forbidden%20Rites/Currencies/ByCategory"
                "?category=currency&page=1&perPage=100&dataPoints=7&frequencyHours=24"
                "&referenceCurrency=divine"
            )
        )
        self.assertFalse(allowed("https://api.poe2scout.com.evil.test/poe2/Leagues"))
        self.assertFalse(allowed("http://api.poe2scout.com/poe2/Leagues"))
        self.assertFalse(allowed("https://api.poe2scout.com/poe1/Leagues"))
        self.assertFalse(
            allowed(
                f"{SCOUT_API_BASE}/poe2/Leagues/Forbidden%20Rites/Currencies/ByCategory"
                "?category=currency&page=1&perPage=100&dataPoints=7&frequencyHours=24"
                "&referenceCurrency=exalted"
            )
        )
        self.assertFalse(
            allowed(
                f"{SCOUT_API_BASE}/poe2/Leagues/Forbidden%20Rites/Currencies/ByCategory"
                "?category=currency&page=1&perPage=100&referenceCurrency=divine"
            )
        )

    async def test_current_economy_paginates_dynamic_categories_and_sorts_divine_quotes(self):
        class FixtureClient(Poe2ScoutEconomyClient):
            def __init__(self):
                super().__init__()
                self.urls = []

            async def _current_league(self, force_refresh=False):
                return "Forbidden Rites"

            async def _get_json(self, url, cache_seconds, force_refresh=False):
                self.urls.append((url, force_refresh))
                if url.endswith("/ReferenceCurrencies"):
                    return [{"ApiId": "exalted"}, {"ApiId": "divine"}]
                if url.endswith("/Items/Categories"):
                    return {
                        "CurrencyCategories": [
                            {"ApiId": "currency", "Label": "Currency"},
                            {"ApiId": "runes", "Label": "Runes"},
                        ]
                    }
                query = parse_qs(urlsplit(url).query)
                category = query["category"][0]
                page = int(query["page"][0])
                if query["referenceCurrency"] != ["divine"] or query["dataPoints"] != ["7"]:
                    raise AssertionError("price requests must use divine and 7 data points")
                if category == "currency" and page == 1:
                    return scout_page(
                        [
                            scout_item("divine", "Divine Orb", 1),
                            scout_item("chaos", "Chaos Orb", 0.2),
                        ],
                        pages=2,
                    )
                if category == "currency" and page == 2:
                    return scout_page([scout_item("mirror", "Mirror of Kalandra", 5000)], pages=2, current_page=2)
                return scout_page([scout_item("rune", "Greater Rune", 3)])

        client = FixtureClient()
        snapshot = await client.current_economy(force_refresh=True)
        self.assertEqual(snapshot.league, "Forbidden Rites")
        self.assertEqual(snapshot.reference, "Divine Orb")
        self.assertEqual(snapshot.total_count, 4)
        self.assertEqual([row.value for row in snapshot.rows], [5000, 3, 1, 0.2])
        self.assertEqual(snapshot.rows[0].category, "通货")
        self.assertEqual(snapshot.rows[1].category, "符文")
        self.assertEqual(snapshot.rows[-1].sort_key, "chaos")
        price_urls = [url for url, _ in client.urls if "/Currencies/ByCategory?" in url]
        self.assertEqual(len(price_urls), 3)
        self.assertTrue(all(force for _, force in client.urls))
        self.assertEqual(
            parse_qs(urlsplit(price_urls[0]).query)["referenceCurrency"], ["divine"]
        )

    async def test_current_economy_rejects_unavailable_divine_reference(self):
        class FixtureClient(Poe2ScoutEconomyClient):
            async def _current_league(self, force_refresh=False):
                return "Forbidden Rites"

            async def _get_json(self, url, cache_seconds, force_refresh=False):
                if url.endswith("/ReferenceCurrencies"):
                    return [{"ApiId": "exalted"}]
                raise AssertionError("must stop before fetching prices")

        with self.assertRaises(EconomyError):
            await FixtureClient().current_economy()

    async def test_current_economy_rejects_non_divine_response(self):
        class FixtureClient(Poe2ScoutEconomyClient):
            async def _current_league(self, force_refresh=False):
                return "Forbidden Rites"

            async def _get_json(self, url, cache_seconds, force_refresh=False):
                if url.endswith("/ReferenceCurrencies"):
                    return [{"ApiId": "divine"}]
                if url.endswith("/Items/Categories"):
                    return {"CurrencyCategories": [{"ApiId": "currency", "Label": "Currency"}]}
                return scout_page([scout_item("divine", "Divine Orb", 853.8)])

        with self.assertRaises(EconomyError):
            await FixtureClient().current_economy()

    def test_price_parser_reads_logs_oldest_first_and_normalizes_cdn_icon(self):
        rows, pages = parse_scout_currency_page(
            "Forbidden Rites",
            scout_page(
                [
                    scout_item(
                        "mirror-of-kalandra",
                        "Mirror of Kalandra",
                        5000,
                        logs=[
                            {"Price": 5000, "Time": "2026-10-17T00:00:00Z"},
                            {"Price": 4000, "Time": "2026-10-16T00:00:00Z"},
                            {"Price": 3000, "Time": "2026-10-15T00:00:00Z"},
                        ],
                        icon="https://web.poecdn.com//gen/image/mirror.webp",
                    )
                ],
                pages=4,
            ),
            "通货",
        )
        self.assertEqual(pages, 4)
        self.assertEqual(rows[0].trend, (3000, 4000, 5000))
        self.assertAlmostEqual(rows[0].change_percent, (5000 / 3000 - 1) * 100)
        self.assertEqual(rows[0].icon_url, "https://web.poecdn.com/gen/image/mirror.webp")
        self.assertEqual(rows[0].sort_key, "mirrorofkalandra")

    def test_invalid_price_payloads_are_rejected_or_skipped(self):
        with self.assertRaises(EconomyError):
            parse_scout_currency_page("Season", {"Items": []})
        with self.assertRaises(EconomyError):
            parse_scout_currency_page("Season", scout_page([], pages="many"))
        rows, _ = parse_scout_currency_page(
            "Season",
            scout_page(
                [
                    scout_item("zero", "Zero", 0),
                    scout_item("nan", "NaN", float("nan")),
                    scout_item("missing", "Missing", None),
                    {"ApiId": "no-name", "CurrentPrice": 5},
                ]
            ),
        )
        self.assertEqual(rows, [])

    def test_invalid_utf8_response_becomes_a_source_error(self):
        class Response:
            headers = type("Headers", (), {"get_content_type": lambda self: "application/json"})()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return LEAGUES_URL

            def read(self, _limit):
                return b"\xff"

        class Opener:
            def open(self, _request, timeout):
                return Response()

        with patch("astrbot_plugin_poe2db.economy.build_opener", return_value=Opener()):
            with self.assertRaises(EconomyError):
                Poe2ScoutEconomyClient()._download_sync(LEAGUES_URL)

    def test_image_markup_escapes_remote_names(self):
        rows, _ = parse_scout_currency_page(
            "Future <Season>",
            scout_page(
                [
                    scout_item("mirror", '<img src=x onerror=alert(1)>{{7*7}}', 5000),
                    scout_item("divine", "Divine Orb", 1),
                ]
            ),
        )
        snapshot = CurrencySnapshot("Future <Season>", "Divine Orb", tuple(rows), len(rows), "2026-10-13 12:00")
        markup = render_currency_html(snapshot)
        self.assertIn("Future &lt;Season&gt;", markup)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;&#123;&#123;7*7&#125;&#125;", markup)
        self.assertNotIn("<img src=x", markup)
        self.assertIn("Divine Orb", markup)
        self.assertIn("7 天趋势", markup)
        self.assertIn(
            '<img class="item-icon" src="https://web.poecdn.com/gen/image/item.webp" alt="">',
            markup,
        )

    def test_item_icon_urls_only_allow_price_source_image_hosts(self):
        self.assertEqual(
            safe_currency_icon_url("/gen/image/currency.webp"),
            "https://web.poecdn.com/gen/image/currency.webp",
        )
        self.assertEqual(
            safe_currency_icon_url("https://web.poecdn.com/gen/image/currency.webp"),
            "https://web.poecdn.com/gen/image/currency.webp",
        )
        self.assertEqual(
            safe_currency_icon_url("https://web.poecdn.com//gen/image/currency.webp"),
            "https://web.poecdn.com/gen/image/currency.webp",
        )
        self.assertIsNone(safe_currency_icon_url("https://api.poe2scout.com/gen/image/item.webp"))
        self.assertEqual(
            safe_currency_icon_url("https://cdn.poe2db.tw/image/currency.webp"),
            "https://cdn.poe2db.tw/image/currency.webp",
        )
        self.assertIsNone(safe_currency_icon_url("https://evil.example/image.webp"))
        self.assertIsNone(safe_currency_icon_url("/gen/image/../../x"))
        self.assertIsNone(safe_currency_icon_url("//evil.example/gen/image/a.webp"))

    def test_renderer_omits_untrusted_item_icon_url(self):
        row = CurrencyRow(
            name="测试物品",
            value=1.0,
            change_percent=None,
            trend=(),
            icon_url='https://evil.example/a.webp" onerror="alert(1)',
        )
        snapshot = CurrencySnapshot("测试赛季", "神圣石", (row,), 1, "2026-10-13 12:00")
        markup = render_currency_html(snapshot)
        self.assertNotIn("evil.example", markup)
        self.assertIn("测试物品", markup)

    def test_currency_image_localizes_names_and_keeps_divine_units(self):
        rows, _ = parse_scout_currency_page(
            "Forbidden Rites",
            scout_page(
                [
                    scout_item("mirror", "Mirror of Kalandra", 5000),
                    scout_item("chaos", "Chaos Orb", 0.1),
                    scout_item("divine", "Divine Orb", 1),
                ]
            ),
        )
        snapshot = CurrencySnapshot("Forbidden Rites", "Divine Orb", tuple(rows), len(rows), "2026-10-13 12:00")
        localized = localize_currency_snapshot(
            snapshot,
            {
                "mirrorofkalandra": ("卡兰德的魔镜", "通货"),
                "chaosorb": ("混沌石", "通货"),
                "divineorb": ("神圣石", "通货"),
            },
            "禁断仪式",
        )
        markup = render_currency_html(localized)
        for term in ("卡兰德的魔镜", "混沌石", "神圣石", "禁断仪式", "5.00千"):
            self.assertIn(term, markup)
        for term in ("Mirror of Kalandra", "Chaos Orb", "Divine Orb", "Forbidden Rites", "5.00k"):
            self.assertNotIn(term, markup)

    def test_localizes_categories_from_database_names(self):
        rows, _ = parse_scout_currency_page(
            "Forbidden Rites",
            scout_page([scout_item("mirror", "Mirror of Kalandra", 5000)]),
            "通货",
        )
        snapshot = CurrencySnapshot("Forbidden Rites", "Divine Orb", tuple(rows), 1, "2026-10-13 12:00")
        localized = localize_currency_snapshot(
            snapshot,
            {"mirror": ("卡兰德的魔镜", "通货")},
            "禁断仪式",
        )
        self.assertEqual(localized.rows[0].name, "卡兰德的魔镜")
        self.assertEqual(localized.rows[0].category, "通货")

    def test_market_image_caps_at_50_rows_and_shows_category_column(self):
        rows = tuple(
            CurrencyRow(
                name=f"物品{index}",
                value=float(100 - index),
                change_percent=None,
                trend=(),
                category="碎片",
            )
            for index in range(60)
        )
        markup = render_currency_html(
            CurrencySnapshot(
                league="测试赛季",
                reference="神圣石",
                rows=rows,
                total_count=len(rows),
                generated_at="2026-10-13 06:00",
            )
        )
        self.assertEqual(markup.count('<td class="rank">'), 50)
        self.assertIn("分类", markup)
        self.assertIn("神圣石", markup)


if __name__ == "__main__":
    unittest.main()
