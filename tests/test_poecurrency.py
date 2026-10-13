"""National price filtering and live Poe2DB name matching."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.economy import EconomyError
from astrbot_plugin_poe2db.poecurrency import (
    PoECurrencyClient,
    SUMMARY_URL,
    parse_national_divine_summary,
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
    def setUp(self):
        self.names = {
            "mirrorofkalandra": ("卡兰德的魔镜", "通货"),
            "refinedsibilantcatalyst": ("精炼嘶语催化剂", "裂隙"),
            "chaosorb": ("混沌石", "通货"),
        }

    def test_filters_to_direct_divine_quotes_and_poe2db_names(self):
        snapshot = parse_national_divine_summary(summary_payload(), self.names)
        self.assertEqual(snapshot.reference, "神圣石")
        self.assertEqual(snapshot.category_label, "14 类经济物品")
        self.assertEqual(snapshot.total_count, 2)
        self.assertEqual(
            [row.name for row in snapshot.rows],
            ["卡兰德的魔镜", "精炼嘶语催化剂"],
        )
        self.assertEqual(snapshot.rows[0].value, 3200.0)
        self.assertEqual(snapshot.rows[0].unit_label, "神圣石")
        self.assertEqual(snapshot.rows[1].category, "裂隙")

    def test_empty_divine_matches_are_reported(self):
        with self.assertRaises(EconomyError):
            parse_national_divine_summary([], self.names)

    async def test_scheduled_fetch_reads_the_summary_endpoint(self):
        class FixtureClient(PoECurrencyClient):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def _download_payload_sync(self):
                self.calls += 1
                return summary_payload()

        client = FixtureClient()
        snapshot = await client.current_divine_snapshot(self.names)
        self.assertEqual(client.calls, 1)
        self.assertEqual(snapshot.total_count, 2)

    def test_api_url_allowlist_is_exact(self):
        self.assertTrue(PoECurrencyClient._allowed(SUMMARY_URL))
        self.assertFalse(
            PoECurrencyClient._allowed(
                "https://poecurrency.top.evil.test/api/summary?version=2"
            )
        )


if __name__ == "__main__":
    unittest.main()
