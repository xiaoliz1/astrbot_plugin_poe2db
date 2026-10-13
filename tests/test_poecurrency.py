"""National Divine prices, native Chinese names, and item icons."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.economy import CurrencyRow, EconomyError, render_currency_html
from astrbot_plugin_poe2db.poecurrency import (
    PoECurrencyClient,
    SUMMARY_URL,
    parse_national_divine_summary,
    parse_national_chinese_names,
    sort_national_snapshot,
)


def summary_payload():
    return [
        {
            "category_label": "通货仓库",
            "items": [
                {
                    "item_name": "卡兰德的魔镜",
                    "engname": "Mirror of Kalandra",
                    "buy_avg": 3200.0,
                    "sell_avg": 3100.0,
                    "buy_avg_ratio": 2.5,
                    "sell_avg_ratio": 1.5,
                    "currency_unit": "d",
                    "item_icon": "https://cdn.poe2db.tw/image/mirror.webp",
                    "latest_datetime": "2026-10-13 06:00:00",
                },
                {
                    "item_name": "混沌石",
                    "engname": "Chaos Orb",
                    "buy_avg": 25.0,
                    "sell_avg": 24.0,
                    "currency_unit": "c",
                },
                {
                    "item_name": "完美崇高石",
                    "engname": "Perfect Exalted Orb",
                    "buy_avg": 3.0,
                    "sell_avg": 2.9,
                    "currency_unit": "e",
                },
                {
                    "item_name": "卡兰德的魔镜异常记录",
                    "engname": "Mirror of Kalandra",
                    "buy_avg": 9000.0,
                    "sell_avg": 9000.0,
                    "currency_unit": "d",
                    "error": True,
                },
                {
                    "item_name": "未知物品",
                    "engname": "Unknown Item",
                    "buy_avg": 100.0,
                    "sell_avg": 100.0,
                    "currency_unit": "d",
                },
            ],
        },
        {
            "category_label": "催化剂",
            "items": [
                {
                    "item_name": "精炼嘶语催化剂",
                    "engname": "Refined Sibilant Catalyst",
                    "buy_avg": 12.5,
                    "sell_avg": 12.0,
                    "currency_unit": "d",
                }
            ],
        },
    ]


class NationalPriceTests(unittest.IsolatedAsyncioTestCase):
    def test_filters_direct_divine_quotes_using_national_names_and_icons(self):
        snapshot = parse_national_divine_summary(summary_payload())
        self.assertEqual(snapshot.reference, "神圣石")
        self.assertEqual(snapshot.category_label, "国服物价")
        self.assertEqual(snapshot.total_count, 3)
        self.assertEqual(
            [row.name for row in snapshot.rows],
            ["卡兰德的魔镜", "未知物品", "精炼嘶语催化剂"],
        )
        self.assertEqual(snapshot.rows[0].value, 3200.0)
        self.assertEqual(snapshot.rows[0].unit_label, "神圣石")
        self.assertEqual(snapshot.rows[0].category, "通货仓库")
        self.assertEqual(snapshot.rows[0].icon_url, "https://cdn.poe2db.tw/image/mirror.webp")
        self.assertEqual(snapshot.rows[2].category, "催化剂")
        markup = render_currency_html(snapshot)
        icon = '<img class="item-icon" src="https://cdn.poe2db.tw/image/mirror.webp" alt="">'
        self.assertIn(icon, markup)
        self.assertLess(markup.index(icon), markup.index("卡兰德的魔镜"))

    def test_empty_divine_matches_are_reported(self):
        with self.assertRaises(EconomyError):
            parse_national_divine_summary([])

    def test_national_api_supplies_chinese_names_for_all_price_units(self):
        names = parse_national_chinese_names(summary_payload())
        self.assertEqual(names["mirrorofkalandra"], "卡兰德的魔镜")
        self.assertEqual(names["chaosorb"], "混沌石")
        self.assertEqual(names["divineorb"], "神圣石")

    def test_national_prices_follow_the_live_international_order(self):
        national = parse_national_divine_summary(summary_payload())
        international_order = (
            CurrencyRow("精炼嘶语催化剂", 2.0, None, (), sort_key="refinedsibilantcatalyst"),
            CurrencyRow("卡兰德的魔镜", 1.0, None, (), sort_key="mirrorofkalandra"),
            CurrencyRow("未知物品", 0.5, None, (), sort_key="unknownitem"),
        )
        reordered = sort_national_snapshot(national, international_order)
        self.assertEqual(
            [row.name for row in reordered.rows],
            ["精炼嘶语催化剂", "卡兰德的魔镜", "未知物品"],
        )

    async def test_scheduled_fetch_reads_the_summary_once_for_prices_and_names(self):
        class FixtureClient(PoECurrencyClient):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def _download_payload_sync(self):
                self.calls += 1
                return summary_payload()

        client = FixtureClient()
        snapshot, names = await client.current_divine_data()
        self.assertEqual(client.calls, 1)
        self.assertEqual(snapshot.total_count, 3)
        self.assertEqual(names["mirrorofkalandra"], "卡兰德的魔镜")

    def test_api_url_allowlist_is_exact(self):
        self.assertTrue(PoECurrencyClient._allowed(SUMMARY_URL))
        self.assertFalse(
            PoECurrencyClient._allowed(
                "https://poecurrency.top.evil.test/api/summary?version=2"
            )
        )


if __name__ == "__main__":
    unittest.main()
