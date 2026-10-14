"""Validation tests for the inputs accepted by `/poe2分析`."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.analysis_input import (
    classify_analysis_input,
    wegame_share_code_from_url,
)


class AnalysisInputTests(unittest.TestCase):
    def test_keeps_character_names_as_name_searches(self):
        parsed = classify_analysis_input("  ResurrectForbidden  ")

        self.assertEqual(parsed.kind, "character")
        self.assertEqual(parsed.value, "ResurrectForbidden")

    def test_extracts_name_from_a_poe_ninja_detail_link(self):
        parsed = classify_analysis_input(
            "https://poe.ninja/poe2/builds/forbiddenrites/character/"
            "heygyus-0416/ResurrectForbidden?i=0&search=class%3DGemling%2BLegionnaire"
        )

        self.assertEqual(parsed.kind, "poe_ninja")
        self.assertEqual(parsed.character_name, "ResurrectForbidden")
        self.assertEqual(parsed.league_slug, "forbiddenrites")

    def test_decodes_percent_encoded_poe_ninja_character_name(self):
        parsed = classify_analysis_input(
            "https://poe.ninja/poe2/builds/forbiddenrites/character/account/"
            "%E4%B9%9D%E7%90%83%E5%A4%A9%E5%90%8E%E8%8E%89%E8%8E%89%E4%B8%9D"
        )

        self.assertEqual(parsed.character_name, "九球天后莉莉丝")

    def test_extracts_wegame_share_token_from_fragment_url(self):
        url = (
            "https://www.wegame.com.cn/helper/poe2/#/share/"
            "nlnV7Nn3xLiETK2ExPutBpgnF9MAzSdC-z2oMuYtqCITMxBZhFrdzBLT5_cGd6DL"
        )

        parsed = classify_analysis_input(url)

        self.assertEqual(parsed.kind, "wegame")
        self.assertEqual(
            wegame_share_code_from_url(url),
            "nlnV7Nn3xLiETK2ExPutBpgnF9MAzSdC-z2oMuYtqCITMxBZhFrdzBLT5_cGd6DL",
        )

    def test_rejects_non_detail_poe_ninja_and_unsafe_hosts(self):
        for url in (
            "https://poe.ninja/poe2/builds/forbiddenrites?search=name%3Dabc",
            "https://example.com/poe2/builds/forbiddenrites/character/a/b",
            "http://poe.ninja/poe2/builds/forbiddenrites/character/a/b",
            "https://user@poe.ninja/poe2/builds/forbiddenrites/character/a/b",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                classify_analysis_input(url)

    def test_rejects_incomplete_or_non_wegame_share_links(self):
        for url in (
            "https://www.wegame.com.cn/helper/poe2/",
            "https://www.wegame.com.cn/helper/poe2/#/share/not_valid!",
            "https://wegame.com.cn/helper/poe2/#/share/abcdefghijklmnopqrstuvwxyz",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                classify_analysis_input(url)

    def test_rejects_urls_embedded_in_other_text(self):
        with self.assertRaisesRegex(ValueError, "单独发送完整链接"):
            classify_analysis_input("角色名 https://example.com")


if __name__ == "__main__":
    unittest.main()
