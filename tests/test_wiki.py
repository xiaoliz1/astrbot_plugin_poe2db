"""Focused tests for Wiki lookup and Poe2DB-derived English names."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.poe2db import Source, parse_multilingual_terms
from astrbot_plugin_poe2db.wiki import (
    WikiClient,
    WIKI_API_URLS,
    wiki_names_from_poe2db,
)


class WikiTests(unittest.IsolatedAsyncioTestCase):
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

    def test_only_wiki_https_urls_are_allowed(self):
        allowed = WikiClient._allowed
        self.assertTrue(allowed("https://www.poe2wiki.net/wiki/Ice_Nova"))
        self.assertTrue(allowed(WIKI_API_URLS[0] + "?action=query"))
        self.assertFalse(allowed("https://example.com/data.json"))
        self.assertFalse(allowed("http://www.poe2wiki.net/wiki/Ice_Nova"))
        self.assertFalse(allowed("https://www.poe2wiki.net/wiki/%2E%2E/private"))

    def test_matched_poe2db_slugs_supply_english_wiki_names(self):
        sources = [
            Source("魔血", "https://poe2db.tw/tw/Mageblood", "腰带资料"),
            Source("冰霜新星", "https://poe2db.tw/cn/Ice_Nova", "技能资料"),
            Source("重复", "https://poe2db.tw/cn/Mageblood", "腰带资料"),
            Source("中文网址", "https://poe2db.tw/tw/魔血", "腰带资料"),
            Source("非词条", "https://poe2db.tw/tw/search?q=Mageblood", "搜索资料"),
            Source("其他站", "https://example.com/tw/Mageblood", "不可信"),
            Source("子路径", "https://poe2db.tw/tw/Mageblood/other", "不可信"),
        ]
        self.assertEqual(wiki_names_from_poe2db(sources), ["Mageblood", "Ice Nova"])

    async def test_wiki_api_error_tries_second_endpoint(self):
        class ApiFixture(WikiClient):
            async def _fetch(self, url, kind):
                if url.startswith(WIKI_API_URLS[0]):
                    return {"error": {"code": "ratelimited"}}
                return {"query": {"search": []}}

        result = await ApiFixture()._wiki_api({"action": "query", "list": "search"})
        self.assertEqual(result, {"query": {"search": []}})

    async def test_wiki_uses_exact_title_and_falls_back_to_api_extract(self):
        class WikiFixture(WikiClient):
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
        self.assertEqual(await client.find_wiki(["魔血"]), [])
        records = await client.find_wiki(["Ice Nova"])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].url, "https://www.poe2wiki.net/wiki/Ice_Nova")
        self.assertIn("cold wave", records[0].text)


if __name__ == "__main__":
    unittest.main()
