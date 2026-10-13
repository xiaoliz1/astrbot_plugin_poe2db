"""Current league selection, price parsing, and image markup safety."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.economy import (
    EXCHANGE_CATEGORIES,
    EconomyError,
    LEAGUES_URL,
    CurrencyRow,
    CurrencySnapshot,
    NinjaEconomyClient,
    localize_currency_snapshot,
    parse_currency_overview,
    render_currency_html,
)


def overview():
    return {
        "core": {"primary": "divine", "items": [{"id": "divine", "name": "Divine Orb"}]},
        "items": [
            {
                "id": "mirror",
                "name": "Mirror of Kalandra",
                "detailsId": "mirror-of-kalandra",
                "image": "/gen/image/mirror.webp",
            },
            {"id": "chaos", "name": "Chaos Orb", "image": "/gen/image/chaos.webp"},
            {"id": "invalid", "name": "Invalid"},
        ],
        "lines": [
            {"id": "chaos", "primaryValue": 0.1, "sparkline": {"totalChange": -2, "data": [-1, -2]}},
            {"id": "mirror", "primaryValue": 5000, "sparkline": {"totalChange": 4, "data": [0, 4]}},
            {"id": "invalid", "primaryValue": 0},
        ],
    }


class EconomyTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_league_id_is_used_and_can_change(self):
        class FixtureClient(NinjaEconomyClient):
            def __init__(self):
                super().__init__()
                self.league = "Forbidden Rites"
                self.urls = []

            async def _get_json(self, url, cache_seconds, force_refresh=False):
                self.urls.append(url)
                if url.endswith("/leagues"):
                    return [{"id": self.league, "name": self.league}]
                return overview()

        client = FixtureClient()
        first = await client.current_currency()
        self.assertEqual(first.league, "Forbidden Rites")
        self.assertEqual(first.reference, "Divine Orb")
        self.assertEqual([row.name for row in first.rows], ["Mirror of Kalandra", "Chaos Orb"])
        self.assertEqual(parse_qs(urlsplit(client.urls[-1]).query), {"league": ["Forbidden Rites"], "type": ["Currency"]})

        client.league = "Future Season"
        second = await client.current_currency()
        self.assertEqual(second.league, "Future Season")
        self.assertEqual(parse_qs(urlsplit(client.urls[-1]).query)["league"], ["Future Season"])

    def test_only_poe_ninja_currency_api_urls_are_allowed(self):
        allowed = NinjaEconomyClient._allowed
        self.assertTrue(allowed("https://poe.ninja/poe2/api/economy/leagues"))
        self.assertTrue(allowed("https://poe.ninja/poe2/api/economy/exchange/current/overview?league=Forbidden+Rites&type=Currency"))
        self.assertTrue(allowed("https://poe.ninja/poe2/api/economy/exchange/current/overview?league=Forbidden+Rites&type=Fragments"))
        self.assertTrue(allowed("https://poe.ninja/poe2/api/economy/exchange/current/overview?league=Forbidden+Rites&type=Verisium"))
        self.assertFalse(allowed("https://poe.ninja.evil.test/poe2/api/economy/leagues"))
        self.assertFalse(allowed("https://poe.ninja/poe1/api/economy/leagues"))
        self.assertFalse(allowed("https://poe.ninja/poe2/api/economy/exchange/current/overview?league=Wrong&type=NotAType"))

    async def test_current_economy_merges_all_categories_in_divine_order(self):
        class FixtureClient(NinjaEconomyClient):
            def __init__(self):
                super().__init__()
                self.requests = []

            async def _current_league(self, force_refresh=False):
                return "Forbidden Rites", "Forbidden Rites"

            async def _get_json(self, url, cache_seconds, force_refresh=False):
                from urllib.parse import parse_qs, urlsplit

                exchange_type = parse_qs(urlsplit(url).query)["type"][0]
                self.requests.append((exchange_type, force_refresh))
                index = next(
                    i for i, category in enumerate(EXCHANGE_CATEGORIES)
                    if category[0] == exchange_type
                )
                item_id = f"item-{exchange_type}"
                return {
                    "core": {
                        "primary": "divine",
                        "items": [{"id": "divine", "name": "Divine Orb"}],
                    },
                    "items": [{
                        "id": item_id,
                        "name": f"{exchange_type} item",
                        "detailsId": f"{exchange_type.lower()}-item",
                    }],
                    "lines": [{"id": item_id, "primaryValue": index + 1}],
                }

        client = FixtureClient()
        snapshot = await client.current_economy(force_refresh=True)
        self.assertEqual(len(EXCHANGE_CATEGORIES), 14)
        self.assertEqual(snapshot.total_count, 14)
        self.assertEqual(len(client.requests), 14)
        self.assertTrue(all(force for _, force in client.requests))
        self.assertEqual(
            [row.value for row in snapshot.rows],
            sorted((row.value for row in snapshot.rows), reverse=True),
        )
        self.assertEqual(snapshot.rows[0].category, EXCHANGE_CATEGORIES[-1][1])
        self.assertEqual(EXCHANGE_CATEGORIES[-1], ("Verisium", ""))
        self.assertEqual(snapshot.reference, "Divine Orb")

    def test_empty_price_data_is_not_a_valid_snapshot(self):
        with self.assertRaises(EconomyError):
            parse_currency_overview("Future Season", {"core": {"primary": "divine"}, "items": [], "lines": []})
        malformed = overview()
        malformed["core"]["items"] = 12
        with self.assertRaises(EconomyError):
            parse_currency_overview("Future Season", malformed)

        non_divine = overview()
        non_divine["core"] = {
            "primary": "chaos",
            "items": [{"id": "chaos", "name": "Chaos Orb"}],
        }
        with self.assertRaises(EconomyError):
            parse_currency_overview("Future Season", non_divine, require_divine=True)

    def test_empty_exchange_category_can_be_skipped_in_merged_snapshot(self):
        payload = overview()
        payload["items"] = []
        payload["lines"] = []
        snapshot = parse_currency_overview(
            "Future Season",
            payload,
            category_label="雕像",
            require_divine=True,
            allow_empty=True,
        )
        self.assertEqual(snapshot.rows, ())
        self.assertEqual(snapshot.total_count, 0)

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
                NinjaEconomyClient()._download_sync(LEAGUES_URL)

    def test_image_markup_escapes_remote_names(self):
        payload = overview()
        payload["items"][0]["name"] = '<img src=x onerror=alert(1)>{{7*7}}'
        snapshot = parse_currency_overview("Future <Season>", payload)
        markup = render_currency_html(snapshot)
        self.assertIn("Future &lt;Season&gt;", markup)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;&#123;&#123;7*7&#125;&#125;", markup)
        self.assertNotIn("<img src=x", markup)
        self.assertIn("Divine Orb", markup)
        self.assertIn("7 天趋势", markup)
        self.assertIn(
            '<img class="item-icon" src="https://poe.ninja/gen/image/mirror.webp" alt="">',
            markup,
        )
        self.assertLess(markup.index('src="https://poe.ninja/gen/image/mirror.webp"'), markup.index("&#123;"))

    def test_item_icon_urls_only_allow_the_price_source_image_hosts(self):
        from astrbot_plugin_poe2db.economy import safe_currency_icon_url

        self.assertEqual(
            safe_currency_icon_url("/gen/image/currency.webp"),
            "https://poe.ninja/gen/image/currency.webp",
        )
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

    def test_currency_image_uses_chinese_names_and_units(self):
        snapshot = parse_currency_overview("Forbidden Rites", overview())
        localized = localize_currency_snapshot(
            snapshot,
            {"mirrorofkalandra": "卡兰德的魔镜", "chaosorb": "混沌石", "divineorb": "神圣石"},
            "禁断仪式",
        )
        markup = render_currency_html(localized)
        for term in ("卡兰德的魔镜", "混沌石", "神圣石", "禁断仪式", "5.00千"):
            self.assertIn(term, markup)
        for term in ("Mirror of Kalandra", "Chaos Orb", "Divine Orb", "Forbidden Rites", "5.00k"):
            self.assertNotIn(term, markup)

    def test_localizes_live_currency_names_and_categories(self):
        payload = overview()
        payload["items"][0]["detailsId"] = "mirror-of-kalandra"
        snapshot = parse_currency_overview("Forbidden Rites", payload)
        localized = localize_currency_snapshot(
            snapshot,
            {
                "divineorb": ("神圣石", "通货"),
                "mirrorofkalandra": ("卡兰德的魔镜", "通货"),
                "chaosorb": ("混沌石", "通货"),
            },
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
