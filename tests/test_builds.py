"""Tests for public poe.ninja Builds search links."""

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.builds import character_search_page_url


class CharacterSearchUrlTests(unittest.TestCase):
    def test_builds_current_league_filter_and_encodes_chinese_name(self):
        url = character_search_page_url(" 九球天后莉莉丝 ", "Forbidden Rites")
        parsed = urlsplit(url)

        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "poe.ninja")
        self.assertEqual(parsed.path, "/poe2/builds/forbiddenrites")
        self.assertEqual(
            parse_qs(parsed.query),
            {
                "i": ["0"],
                "search": ["name=九球天后莉莉丝"],
                "name": ["九球天后莉莉丝"],
            },
        )

    def test_rejects_empty_or_overlong_names(self):
        with self.assertRaises(ValueError):
            character_search_page_url("  ", "Forbidden Rites")
        with self.assertRaises(ValueError):
            character_search_page_url("名" * 101, "Forbidden Rites")

    def test_rejects_invalid_league_name(self):
        with self.assertRaises(ValueError):
            character_search_page_url("角色名", "!!!")


if __name__ == "__main__":
    unittest.main()
