"""Tests for public poe.ninja Builds search links."""

import base64
import sys
import unittest
import zlib
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.builds import (
    character_profile_url_from_results,
    character_search_page_url,
    format_build_page_text,
    is_public_character_profile_url,
    normalize_visible_pob_code,
    parse_pob_unique_items,
    PoeNinjaBuildsResolver,
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
            character_profile_url_from_results(
                "九球天后莉莉丝", "Forbidden Rites", results
            )
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
            character_profile_url_from_results(
                "九球天后莉莉丝", "Forbidden Rites", results
            )
        )


class BrowserLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_to_system_chromium_when_playwright_browser_is_missing(self):
        resolver = PoeNinjaBuildsResolver()
        browser = object()
        playwright = Mock()
        playwright.chromium.launch = AsyncMock(
            side_effect=[FileNotFoundError("bundled Chromium is missing"), browser]
        )
        playwright.stop = AsyncMock()

        startup = Mock()
        startup.start = AsyncMock(return_value=playwright)
        fake_playwright_module = ModuleType("playwright")
        fake_playwright_module.__path__ = []
        fake_async_api_module = ModuleType("playwright.async_api")
        fake_async_api_module.async_playwright = Mock(return_value=startup)

        with (
            patch.dict(
                sys.modules,
                {
                    "playwright": fake_playwright_module,
                    "playwright.async_api": fake_async_api_module,
                },
            ),
            patch(
                "astrbot_plugin_poe2db.builds._find_system_chromium",
                return_value="/usr/bin/chromium",
            ),
        ):
            result = await resolver._get_browser()

        self.assertIs(result, browser)
        self.assertEqual(
            [call.kwargs for call in playwright.chromium.launch.await_args_list],
            [
                {"headless": True},
                {"headless": True, "executable_path": "/usr/bin/chromium"},
            ],
        )
        playwright.stop.assert_not_awaited()


class PublicBuildPageTextTests(unittest.TestCase):
    def test_normalizes_visible_poe2_ninja_pob_import_code(self):
        code = "A" * 120 + "_/+=="

        self.assertEqual(
            normalize_visible_pob_code(f"  {code[:60]}\n{code[60:]}  "), code
        )
        self.assertIsNone(normalize_visible_pob_code("too short"))
        self.assertIsNone(normalize_visible_pob_code("A" * 100 + "#"))

    def test_accepts_only_https_profile_pages_for_the_requested_league(self):
        profile_url = (
            "https://poe.ninja/poe2/builds/forbiddenrites/character/"
            "a136681423-9651/%E4%B9%9D%E7%90%83%E5%A4%A9%E5%90%8E%E8%8E%89%E8%8E%89%E4%B8%9D"
            "?i=0&search=name%3D%E4%B9%9D%E7%90%83"
        )

        self.assertTrue(
            is_public_character_profile_url(profile_url, "Forbidden Rites")
        )
        self.assertFalse(
            is_public_character_profile_url(
                profile_url.replace("https://poe.ninja", "https://example.com"),
                "Forbidden Rites",
            )
        )
        self.assertFalse(
            is_public_character_profile_url(profile_url, "Standard")
        )
        self.assertFalse(
            is_public_character_profile_url(
                profile_url.replace("/character/", "/character/a/extra/"),
                "Forbidden Rites",
            )
        )

    def test_formats_page_text_and_equipment_tooltips_with_a_hard_size_limit(self):
        result = format_build_page_text(
            "  Stats  \n\n  MAIN SKILLS  ",
            ["Life: 1000\nCold Resistance: 75%", "Life: 1000\nCold Resistance: 75%"],
        )

        self.assertIn("Stats\nMAIN SKILLS", result)
        self.assertIn("Life: 1000\nCold Resistance: 75%", result)
        self.assertEqual(result.count("Life: 1000"), 1)

        truncated = format_build_page_text("page " * 500, ["item " * 500], max_chars=600)
        self.assertLessEqual(len(truncated), 600)
        self.assertIn("页面公开信息：", truncated)
        self.assertIn("装备物品悬停信息：", truncated)


class PublicBuildUniqueItemTests(unittest.TestCase):
    @staticmethod
    def _share_code(xml: str) -> str:
        return base64.urlsafe_b64encode(zlib.compress(xml.encode("utf-8"))).decode(
            "ascii"
        ).rstrip("=")

    def test_reads_only_uniques_equipped_in_the_active_itemset_and_tree(self):
        xml = """<PathOfBuilding2>
          <Tree activeSpec="1"><Spec id="1"><Sockets>
            <Socket nodeId="100" itemId="3" />
          </Sockets></Spec><Spec id="2"><Sockets>
            <Socket nodeId="200" itemId="5" />
          </Sockets></Spec></Tree>
          <Items activeItemSet="1">
            <ItemSet id="1"><Slot name="Belt" itemId="1" /></ItemSet>
            <ItemSet id="2"><Slot name="Weapon 1" itemId="4" /></ItemSet>
            <Item id="1">Item Class: Belts
Rarity: UNIQUE
Mageblood
Utility Belt</Item>
            <Item id="3">Item Class: Jewels
Rarity: UNIQUE
Prism of Belief
Diamond</Item>
            <Item id="4">Item Class: Body Armours
Rarity: UNIQUE
Inactive Unique
Leather Vest</Item>
            <Item id="5">Item Class: Jewels
Rarity: UNIQUE
Inactive Jewel
Diamond</Item>
          </Items>
        </PathOfBuilding2>"""

        items = parse_pob_unique_items(self._share_code(xml))

        self.assertEqual(
            [(item.name, item.is_jewel) for item in items or []],
            [("Mageblood", False), ("Prism of Belief", True)],
        )

    def test_distinguishes_valid_empty_build_from_invalid_export(self):
        empty_xml = (
            '<PathOfBuilding2><Items activeItemSet="1">'
            '<ItemSet id="1" /></Items></PathOfBuilding2>'
        )
        self.assertEqual(parse_pob_unique_items(self._share_code(empty_xml)), [])
        self.assertIsNone(parse_pob_unique_items("not-a-pob-code"))

if __name__ == "__main__":
    unittest.main()
