"""Tests for the bundled item-name and link catalog generator."""

import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.tools.generate_item_name_fallback import (
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
