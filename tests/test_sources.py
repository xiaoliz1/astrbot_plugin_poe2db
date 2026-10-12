"""Focused tests for matching and safe fallback across the new sources."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.poe2db import parse_multilingual_terms
from astrbot_plugin_poe2db.sources import (
    ExternalSourceError,
    ExternalSourcesClient,
    GAME_DATA_PATHS,
    GAME_DATA_RAW,
    REPOE_BASE,
    REPOE_FILES,
    WIKI_API_URLS,
)


class FixtureClient(ExternalSourcesClient):
    def __init__(self, pages):
        super().__init__()
        self.pages = pages

    async def _fetch(self, url, kind):
        result = self.pages[url]
        if isinstance(result, Exception):
            raise result
        return result


class ExternalSourceTests(unittest.IsolatedAsyncioTestCase):
    def test_multilingual_terms_are_bounded_and_not_invented(self):
        primary, names = parse_multilingual_terms(
            '```json\n{"zh":["冰霜新星"],"tw":["冰霜新星"],"en":["Ice Nova"]}\n```',
            "冰霜新星是什么？",
        )
        self.assertEqual(primary, ["冰霜新星"])
        self.assertEqual(names, ["冰霜新星", "Ice Nova"])
        self.assertEqual(
            parse_multilingual_terms("bad JSON", "「冰霜新星」有什么效果？"),
            (["冰霜新星"], ["冰霜新星"]),
        )

    def test_only_configured_https_sources_are_allowed(self):
        allowed = ExternalSourcesClient._allowed
        self.assertTrue(allowed(REPOE_BASE + "skill_gems.min.json"))
        self.assertTrue(allowed(GAME_DATA_RAW + GAME_DATA_PATHS[0]))
        self.assertTrue(allowed("https://www.poe2wiki.net/wiki/Ice_Nova"))
        self.assertFalse(allowed("https://repoe-fork.github.io.evil.test/poe2/skill_gems.min.json"))
        self.assertFalse(allowed("https://repoe-fork.github.io/poe2/../../private"))
        self.assertFalse(allowed("http://www.poe2wiki.net/wiki/Ice_Nova"))
        self.assertFalse(allowed("https://www.poe2wiki.net/wiki/%2E%2E/private"))

    async def test_wiki_api_error_tries_second_endpoint(self):
        class ApiFixture(ExternalSourcesClient):
            async def _fetch(self, url, kind):
                if url.startswith(WIKI_API_URLS[0]):
                    return {"error": {"code": "ratelimited"}}
                return {"query": {"search": []}}

        result = await ApiFixture()._wiki_api({"action": "query", "list": "search"})
        self.assertEqual(result, {"query": {"search": []}})

    async def test_repoe_matches_exact_records_without_implying_unique_mods(self):
        pages = {
            REPOE_BASE + REPOE_FILES[0]: {
                "Metadata/Items/Gems/SkillGemIceNova": {
                    "base_item": {"display_name": "Ice Nova", "release_state": "released"},
                    "gem_type": "active",
                    "tags": ["cold", "nova"],
                    "grants_skills": ["IceNovaPlayer"],
                }
            },
            REPOE_BASE + REPOE_FILES[1]: {
                "Fire": {"term": "Fire", "definition": "Fire is an elemental damage type."}
            },
            REPOE_BASE + REPOE_FILES[2]: {
                "1": {"id": "Mageblood", "name": "Mageblood", "item_class": "Belt"}
            },
        }
        client = FixtureClient(pages)
        records = await client.find_repoe(["Ice Nova", "Mageblood"], max_sources=3)
        self.assertEqual(len(records), 2)
        self.assertIn("IceNovaPlayer", records[0].text)
        self.assertIn("此索引不提供词缀", records[1].text)
        self.assertEqual(records[1].url, REPOE_BASE + "uniques.min.json")
        self.assertEqual(await client.find_repoe(["Ice"], max_sources=2), [])

    async def test_game_data_uses_record_names_and_real_descriptions(self):
        pages = {
            GAME_DATA_RAW + GAME_DATA_PATHS[0]: [
                {
                    "id": "ice-nova",
                    "grantedEffect": "IceNovaPlayer",
                    "name": {"en": "Ice Nova", "tw": "冰霜新星"},
                    "description": {"en": "Conjure a wave of ice.", "tw": "施展寒冰之潮。"},
                }
            ],
            GAME_DATA_RAW + GAME_DATA_PATHS[1]: [],
            GAME_DATA_RAW + GAME_DATA_PATHS[2]: [
                {
                    "id": "mageblood",
                    "name": {"en": "Mageblood", "tw": "魔血"},
                    "baseItem": {"category": {"en": "Belt", "tw": "腰帶"}},
                    "mods": None,
                }
            ],
        }
        client = FixtureClient(pages)
        records = await client.find_game_data(["冰霜新星", "魔血"])
        self.assertEqual(len(records), 2)
        self.assertIn("施展寒冰之潮", records[0].text)
        self.assertIn("未提供该暗金物品的词缀", records[1].text)
        self.assertEqual(await client.find_game_data(["冰霜"]), [])

    async def test_wiki_uses_exact_title_and_falls_back_to_api_extract(self):
        class WikiFixture(ExternalSourcesClient):
            async def _wiki_api(self, params):
                if params.get("list") == "search":
                    return {
                        "query": {
                            "search": [
                                {"title": "Other Ice Nova page"},
                                {"title": "Ice Nova"},
                            ]
                        }
                    }
                return {"query": {"pages": [{"extract": "Ice Nova creates a cold wave."}]}}

            async def _fetch(self, url, kind):
                return "<html><title>Making sure you're not a bot!</title></html>"

        client = WikiFixture()
        records = await client.find_wiki(["Ice Nova"])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].url, "https://www.poe2wiki.net/wiki/Ice_Nova")
        self.assertIn("cold wave", records[0].text)

    async def test_one_source_failure_does_not_hide_other_results(self):
        class PartialClient(ExternalSourcesClient):
            async def find_game_data(self, names, max_sources=2, max_chars=2500):
                return []

            async def find_wiki(self, names, max_sources=2, max_chars=3500):
                raise ExternalSourceError("blocked")

            async def find_repoe(self, names, max_sources=2, max_chars=2500):
                return []

        sources, failures = await PartialClient().find_sources(["Ice Nova"])
        self.assertEqual(failures, ["PoE2Wiki"])
        self.assertEqual(sources["RePoE"], [])


if __name__ == "__main__":
    unittest.main()
