"""Tests for public poe.ninja Builds search links."""

import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.builds import (
    character_profile_url_from_results,
    character_search_page_url,
)


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

    def test_returns_public_detail_link_for_one_exact_visible_match(self):
        profile_url = (
            "https://poe.ninja/poe2/builds/forbiddenrites/character/"
            "a136681423-9651/%E4%B9%9D%E7%90%83%E5%A4%A9%E5%90%8E%E8%8E%89%E8%8E%89%E4%B8%9D"
            "?i=0&search=name%3D%E4%B9%9D%E7%90%83"
        )

        result = character_profile_url_from_results(
            "九球天后莉莉丝",
            "Forbidden Rites",
            [{"text": "九球天后莉莉丝", "href": profile_url}],
        )

        self.assertEqual(result, profile_url)

    def test_does_not_guess_fuzzy_or_ambiguous_profile_links(self):
        results = [
            {
                "text": "九球天后莉莉丝2",
                "href": "https://poe.ninja/poe2/builds/forbiddenrites/character/a1/Other",
            }
        ]
        self.assertIsNone(
            character_profile_url_from_results("九球天后莉莉丝", "Forbidden Rites", results)
        )

        results = [
            {
                "text": "九球天后莉莉丝",
                "href": "https://poe.ninja/poe2/builds/forbiddenrites/character/a1/Name1",
            },
            {
                "text": "九球天后莉莉丝",
                "href": "https://poe.ninja/poe2/builds/forbiddenrites/character/a2/Name2",
            },
        ]
        self.assertIsNone(
            character_profile_url_from_results("九球天后莉莉丝", "Forbidden Rites", results)
        )

    def test_ignores_profile_links_from_other_hosts_or_leagues(self):
        results = [
            {
                "text": "九球天后莉莉丝",
                "href": "https://example.com/poe2/builds/forbiddenrites/character/a1/Name",
            },
            {
                "text": "九球天后莉莉丝",
                "href": "https://poe.ninja/poe2/builds/oldleague/character/a1/Name",
            },
        ]
        self.assertIsNone(
            character_profile_url_from_results("九球天后莉莉丝", "Forbidden Rites", results)
        )


if __name__ == "__main__":
    unittest.main()
