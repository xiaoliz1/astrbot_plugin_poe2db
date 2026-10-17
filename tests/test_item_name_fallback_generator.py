"""Tests for the bundled item-name and link catalog generator."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.tools.generate_item_name_fallback import (
    build_rows,
    canonical_slug,
    category_pages,
    collect_poe2db_matches,
    POE2DB_BASE,
    trade_url_for_item,
    wiki_url_for_item,
)


class ItemNameFallbackGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.realm = {"RealmApiId": "poe2", "TradeApiPath": "trade2"}
        self.league = {"BaseCurrencyApiId": "exalted"}

    def test_wiki_url_uses_unique_name_and_url_encodes_it(self):
        url = wiki_url_for_item({"Name": "The Everlasting Gaze", "Text": "The Everlasting Gaze Azure Amulet"})
        self.assertEqual(
            url,
            "https://www.poe2wiki.net/wiki/The%20Everlasting%20Gaze",
        )

    def test_category_pages_and_canonical_slugs_support_traditional_chinese(self):
        pages = category_pages(
            '<a class="ItemClasses" href="/tw/Unique_item">傳奇物品</a>'
            '<a class="league_bar_icon" href="/tw/Delirium">驚悸迷霧</a>',
            "tw",
        )
        self.assertIn("/tw/Unique_item", pages)
        self.assertIn("/tw/Delirium", pages)
        self.assertNotIn("/tw/tw/Delirium", pages)
        self.assertEqual(canonical_slug("/tw/Mageblood", "tw"), "Mageblood")

    def test_build_rows_includes_traditional_name_joined_by_shared_slug(self):
        rows, unmatched = build_rows(
            [{"Text": "Mageblood Utility Belt", "Name": "Mageblood", "ApiId": "mageblood"}],
            {
                "magebloodutilitybelt": {
                    (
                        "Mageblood Utility Belt",
                        "法师之血 万用腰带",
                        "魔血 實用腰帶",
                        "Mageblood",
                    )
                }
            },
            "Forbidden Rites",
            self.realm,
            self.league,
        )

        self.assertEqual(unmatched, [])
        self.assertEqual(rows[0]["chinese"], "法师之血 万用腰带")
        self.assertEqual(rows[0]["traditional"], "魔血 實用腰帶")
        self.assertEqual(rows[0]["poe2db_url"], "https://poe2db.tw/cn/Mageblood")

    def test_collect_matches_simplified_and_traditional_labels_by_shared_slug(self):
        pages = {
            f"{POE2DB_BASE}/cn/Unique_item": '<a class="UniqueItem" href="/cn/Mageblood">法师之血 万用腰带</a>',
            f"{POE2DB_BASE}/us/Unique_item": '<a class="UniqueItem" href="/us/Mageblood">Mageblood Utility Belt</a>',
            f"{POE2DB_BASE}/tw/Unique_item": '<a class="UniqueItem" href="/tw/Mageblood">魔血 實用腰帶</a>',
        }
        with patch(
            "astrbot_plugin_poe2db.tools.generate_item_name_fallback.fetch_text",
            side_effect=lambda url, timeout: pages[url],
        ):
            matches = collect_poe2db_matches(
                ["/cn/Unique_item"], ["/tw/Unique_item"], 1, 0
            )

        self.assertEqual(
            matches["magebloodutilitybelt"],
            {
                (
                    "Mageblood Utility Belt",
                    "法师之血 万用腰带",
                    "魔血 實用腰帶",
                    "Mageblood",
                )
            },
        )

    def test_wiki_url_uses_visible_text_when_item_has_no_unique_name(self):
        url = wiki_url_for_item({"Name": None, "Text": "Orb of Augmentation"})
        self.assertEqual(url, "https://www.poe2wiki.net/wiki/Orb%20of%20Augmentation")

    def test_trade_url_builds_online_currency_exchange_query(self):
        url = trade_url_for_item(
            {"ApiId": "divine"}, "Forbidden Rites", self.realm, self.league
        )
        parsed = urlsplit(url)
        self.assertEqual(
            parsed.path,
            "/trade2/exchange/poe2/forbidden%20rites",
        )
        query = json.loads(parse_qs(parsed.query)["q"][0])
        self.assertEqual(
            query,
            {
                "exchange": {
                    "status": {"option": "online"},
                    "have": ["exalted"],
                    "want": ["divine"],
                }
            },
        )

    def test_trade_url_builds_unique_item_search(self):
        url = trade_url_for_item(
            {"ApiId": None, "Name": "The Everlasting Gaze"},
            "Forbidden Rites",
            self.realm,
            self.league,
        )
        parsed = urlsplit(url)
        self.assertEqual(parsed.path, "/trade2/search/poe2/forbidden%20rites")
        query = json.loads(parse_qs(parsed.query)["q"][0])
        self.assertEqual(query, {"query": {"filters": {}, "name": "The Everlasting Gaze"}})

    def test_unique_search_takes_precedence_over_currency_identifier(self):
        url = trade_url_for_item(
            {"ApiId": "unexpected-api-id", "Name": "The Everlasting Gaze"},
            "Forbidden Rites",
            self.realm,
            self.league,
        )
        self.assertIn("/trade2/search/", url)

    def test_trade_url_is_omitted_when_item_cannot_be_identified(self):
        self.assertIsNone(
            trade_url_for_item(
                {"ApiId": None, "Name": None},
                "Forbidden Rites",
                self.realm,
                self.league,
            )
        )


if __name__ == "__main__":
    unittest.main()
