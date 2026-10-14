"""Validation and presentation for native character Trade links."""

import json
import sys
import unittest
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.poe_ninja_trade import (
    NativeTradeItem,
    NativeTradeResult,
    format_native_trade_messages,
    valid_native_trade_url,
)


def trade_url(name: str) -> str:
    payload = {"query": {"status": {"option": "securable"}, "type": name}}
    return (
        "https://www.pathofexile.com/trade2/search/Forbidden%20Rites?q="
        + quote(json.dumps(payload, separators=(",", ":")))
    )


class NativeTradeTests(unittest.TestCase):
    def test_accepts_the_official_trade_query_for_the_matching_league(self):
        url = trade_url("Rite of Passage")
        self.assertTrue(valid_native_trade_url(url, "Forbidden Rites"))
        self.assertFalse(valid_native_trade_url(url, "Standard"))
        self.assertFalse(valid_native_trade_url(url.replace("https://", "http://"), "Forbidden Rites"))
        self.assertFalse(valid_native_trade_url(url.replace("pathofexile.com", "example.com"), "Forbidden Rites"))
        self.assertFalse(valid_native_trade_url(url.split("?", 1)[0], "Forbidden Rites"))

    def test_formats_every_item_without_cutting_a_trade_url(self):
        items = (
            NativeTradeItem("Rite of Passage", trade_url("Rite of Passage"), False),
            NativeTradeItem("Brood Cut", trade_url("Brood Cut"), True),
        )
        messages = format_native_trade_messages(
            NativeTradeResult(items, equipped_count=3),
            "https://poe.ninja/poe2/builds/forbiddenrites/character/a/Name",
            max_chars=180,
        )
        combined = "\n".join(messages)
        self.assertEqual(len(messages), 2)
        self.assertIn("已取得 2/3 件", combined)
        self.assertIn("部分装备未生成链接", combined)
        for item in items:
            self.assertIn(item.url, combined)
        self.assertIn("珠宝 · Brood Cut", combined)

    def test_reports_when_character_page_has_no_native_links(self):
        messages = format_native_trade_messages(None, "https://poe.ninja/example")
        self.assertEqual(len(messages), 1)
        self.assertIn("暂时无法", messages[0])


if __name__ == "__main__":
    unittest.main()
