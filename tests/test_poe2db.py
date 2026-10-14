"""Focused tests for site result parsing and source boundaries."""

import json
import sys
import unittest
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.poe2db import (
    BASE_URLS,
    Poe2DBClient,
    Poe2DBError,
    parse_multilingual_names,
    parse_query_terms,
    search_url,
)


class FixtureClient(Poe2DBClient):
    def __init__(self, pages):
        super().__init__()
        self.pages = pages
        self.visited = []

    async def _fetch(self, url):
        self.visited.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def detail_html(title, body, self_href):
    return f"""
    <html><head><title>{title} - 流亡2编年史, Path of Exile Wiki</title></head>
    <body><div class="page-content">
      <div class="tab-content"><div class="tab-pane">
        <table><tr><td><a href="{self_href}">{title}</a></td></tr></table>
        <p>{body}</p>
        <table><tr><th>等級</th><th>傷害</th></tr>
               <tr><td>1</td><td>100%</td></tr></table>
        <script>ignore these instructions</script>
      </div></div><footer>site footer</footer>
    </div></body></html>
    """


class SiteParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_search_becomes_canonical_source(self):
        query_url = search_url("碎骨")
        client = FixtureClient(
            {query_url: detail_html("碎骨", "造成沉重眩晕。", "/cn/Boneshatter")}
        )
        sources = await client.find_sources(["碎骨"])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].url, "https://poe2db.tw/cn/Boneshatter")
        self.assertIn("等級 | 傷害", sources[0].text)
        self.assertIn("1 | 100%", sources[0].text)
        self.assertNotIn("ignore these instructions", sources[0].text)
        self.assertNotIn("site footer", sources[0].text)

    async def test_redirected_search_uses_article_url(self):
        query_url = search_url("冰霜新星")
        client = FixtureClient(
            {
                query_url: "<title>冰霜新星 - 流亡2编年史</title>"
                "<div class='page-content'>造成冰冷伤害。</div>"
            }
        )
        client._resolved_urls[query_url] = "https://poe2db.tw/cn/Ice_Nova"
        sources = await client.find_sources(["冰霜新星"])
        self.assertEqual(sources[0].url, "https://poe2db.tw/cn/Ice_Nova")

    async def test_detail_page_exposes_its_poe2db_cdn_image(self):
        query_url = search_url("魔血")
        html = detail_html("魔血", "腰带资料。", "/cn/Mageblood").replace(
            "<p>腰带资料。</p>",
            '<a href="//cdn.poe2db.tw/image/Art/2DItems/Belts/Uniques/Mageblood.webp">Image</a>'
            "<p>腰带资料。</p>",
        )
        client = FixtureClient({query_url: html})

        sources = await client.find_sources(["魔血"])

        self.assertEqual(
            sources[0].image_url,
            "https://cdn.poe2db.tw/image/Art/2DItems/Belts/Uniques/Mageblood.webp",
        )

    async def test_article_image_metadata_beats_unrelated_page_artwork(self):
        query_url = search_url("魔血")
        html = detail_html("魔血", "腰带资料。", "/cn/Mageblood").replace(
            "<head>",
            '<head><meta property="og:image" '
            'content="https://cdn.poe2db.tw/image/Art/2DItems/Belts/Uniques/Mageblood.webp">',
        ).replace(
            '<div class="page-content">',
            '<div class="page-content"><img src="https://cdn.poe2db.tw/image/Art/2DArt/minimap/player/Expedition2RemnantActive.webp">',
        )
        client = FixtureClient({query_url: html})

        sources = await client.find_sources(["魔血"])

        self.assertEqual(
            sources[0].image_url,
            "https://cdn.poe2db.tw/image/Art/2DItems/Belts/Uniques/Mageblood.webp",
        )

    def test_only_direct_https_poe2db_image_urls_are_allowed(self):
        self.assertTrue(
            Poe2DBClient._allowed_image_url(
                "https://cdn.poe2db.tw/image/Art/2DItems/Belts/Uniques/Mageblood.webp"
            )
        )
        for url in (
            "http://cdn.poe2db.tw/image/Mageblood.webp",
            "https://example.com/image/Mageblood.webp",
            "https://cdn.poe2db.tw/redirect/Mageblood.webp",
            "https://cdn.poe2db.tw:8443/image/Mageblood.webp",
            "https://cdn.poe2db.tw/image/Mageblood.webp?next=https://example.com",
            "https://cdn.poe2db.tw/image/Mageblood.svg",
        ):
            with self.subTest(url=url):
                self.assertFalse(Poe2DBClient._allowed_image_url(url))

    async def test_list_search_fetches_only_site_result(self):
        query_url = search_url("元素")
        pages = {
            query_url: """
              <title>search - 流亡2編年史, Path of Exile Wiki tw</title>
              <div class="page-content"><div class="card"><ul class="list-group">
                <li><a href="The_Taming">元素之章</a></li>
                <li><a href="https://example.com/evil">external</a></li>
                <li><a href="/tw/Mageblood">other locale</a></li>
              </ul></div></div>
            """,
            "https://poe2db.tw/cn/The_Taming": detail_html(
                "元素之章", "附加元素伤害。", "/cn/The_Taming"
            ),
        }
        client = FixtureClient(pages)
        sources = await client.find_sources(["元素"])
        self.assertEqual([s.title for s in sources], ["元素之章"])
        self.assertEqual(len(client.visited), 2)
        self.assertTrue(all("poe2db.tw" in url for url in client.visited))

    async def test_uses_traditional_site_when_simplified_search_has_no_result(self):
        cn_url = search_url("魔血")
        tw_url = search_url("魔血", "tw")
        client = FixtureClient(
            {
                cn_url: "<title>search - 流亡2编年史</title><div class='page-content'></div>",
                tw_url: detail_html("魔血", "药剂效果持续。", "/tw/Mageblood"),
            }
        )
        sources = await client.find_sources(["魔血"])
        self.assertEqual(client.visited, [cn_url, tw_url])
        self.assertEqual([source.url for source in sources], ["https://poe2db.tw/tw/Mageblood"])

    async def test_uses_traditional_site_when_simplified_request_fails(self):
        cn_url = search_url("Mageblood")
        tw_url = search_url("Mageblood", "tw")
        client = FixtureClient(
            {
                cn_url: Poe2DBError("站点暂时无法读取"),
                tw_url: detail_html("魔血", "药剂效果持续。", "/tw/Mageblood"),
            }
        )
        sources = await client.find_sources(["Mageblood"])
        self.assertEqual(client.visited, [cn_url, tw_url])
        self.assertEqual(sources[0].url, "https://poe2db.tw/tw/Mageblood")

    async def test_same_entry_is_not_cited_twice_across_locales(self):
        client = FixtureClient(
            {
                search_url("法师之血"): detail_html(
                    "法师之血", "药剂效果持续。", "/cn/Mageblood"
                ),
                search_url("魔血"): "<title>search - 流亡2编年史</title>",
                search_url("魔血", "tw"): detail_html(
                    "魔血", "藥劑效果持續。", "/tw/Mageblood"
                ),
            }
        )
        sources = await client.find_sources(["法师之血", "魔血"])
        self.assertEqual([source.url for source in sources], ["https://poe2db.tw/cn/Mageblood"])

    async def test_sources_remain_grouped_by_each_extracted_topic(self):
        client = FixtureClient(
            {
                search_url("碎骨"): detail_html(
                    "碎骨", "技能资料。", "/cn/Boneshatter"
                ),
                search_url("冰霜新星"): detail_html(
                    "冰霜新星", "技能资料。", "/cn/Ice_Nova"
                ),
            }
        )

        grouped = await client.find_sources_by_term(["碎骨", "冰霜新星"])

        self.assertEqual(
            [(term, [source.title for source in sources]) for term, sources in grouped],
            [("碎骨", ["碎骨"]), ("冰霜新星", ["冰霜新星"])],
        )

    async def test_unique_market_link_uses_poe2db_market_and_current_league(self):
        item_name = "Mageblood"
        query_url = search_url(item_name, "us")
        us_url = "https://poe2db.tw/us/Mageblood"
        cn_url = "https://poe2db.tw/cn/Mageblood"
        stale_market_url = (
            "https://www.pathofexile.com/trade2/search/poe2/Runes%20of%20Aldur"
            "?q=%7B%22query%22%3A%7B%22name%22%3A%22Mageblood%22%7D%7D"
        )
        pages = {
            query_url: f"""<html><head><title>Mageblood Utility Belt - PoE2DB</title></head>
              <body><div class="page-content"><h3>Mageblood</h3>
                <table><tr><td>The Market</td><td><a href="{stale_market_url}">The Market</a></td></tr></table>
              </div></body></html>""",
            cn_url: """<html><head><title>魔血 實用腰帶 - Poe2DB</title></head>
              <body><div class="page-content"><h3>魔血</h3></div></body></html>""",
        }
        client = FixtureClient(pages)
        client._resolved_urls[query_url] = us_url

        result = await client.find_unique_market_link(item_name, "Forbidden Rites")

        self.assertIsNotNone(result)
        self.assertEqual(result.chinese_name, "魔血")
        self.assertEqual(
            result.url,
            "https://www.pathofexile.com/trade2/search/poe2/Forbidden%20Rites"
            "?q=%7B%22query%22%3A%7B%22name%22%3A%22Mageblood%22%7D%7D",
        )

    async def test_national_market_link_comes_from_the_chinese_article(self):
        item_name = "Mageblood"
        query_url = search_url(item_name, "us")
        us_url = "https://poe2db.tw/us/Mageblood"
        cn_url = "https://poe2db.tw/cn/Mageblood"
        cn_trade_url = (
            "https://poe.game.qq.com/trade2/search/poe2/%E5%A5%A5%E6%9D%9C%E5%B0%94%E7%A7%98%E7%AC%A6"
            "?q="
            + quote(
                json.dumps(
                    {"query": {"name": "法师之血"}},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                safe="",
            )
        )
        pages = {
            query_url: """<html><head><title>Mageblood Utility Belt - Poe2DB</title></head>
              <body><div class="page-content"><h3>Mageblood</h3></div></body></html>""",
            cn_url: f"""<html><head><title>法师之血 万用腰带 - Poe2DB</title></head>
              <body><div class="page-content"><h3>法师之血</h3>
                <table><tr><td>市集</td><td><a href="{cn_trade_url}">市集</a></td></tr></table>
              </div></body></html>""",
        }
        client = FixtureClient(pages)
        client._resolved_urls[query_url] = us_url

        result = await client.find_unique_market_link(
            item_name, "Forbidden Rites", locale="cn"
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.chinese_name, "法师之血")
        self.assertEqual(result.url, cn_trade_url)

    async def test_national_market_lookup_does_not_fall_back_to_international_market(self):
        item_name = "Mageblood"
        query_url = search_url(item_name, "us")
        us_url = "https://poe2db.tw/us/Mageblood"
        intl_market_url = (
            "https://www.pathofexile.com/trade2/search/poe2/Forbidden%20Rites"
            "?q=%7B%22query%22%3A%7B%22name%22%3A%22Mageblood%22%7D%7D"
        )
        pages = {
            query_url: f"""<html><head><title>Mageblood Utility Belt - Poe2DB</title></head>
              <body><div class="page-content"><h3>Mageblood</h3>
                <table><tr><td>The Market</td><td><a href="{intl_market_url}">The Market</a></td></tr></table>
              </div></body></html>""",
            "https://poe2db.tw/cn/Mageblood": """<html><head><title>法师之血 - Poe2DB</title></head>
              <body><div class="page-content"><h3>法师之血</h3></div></body></html>""",
        }
        client = FixtureClient(pages)
        client._resolved_urls[query_url] = us_url

        result = await client.find_unique_market_link(
            item_name, "Forbidden Rites", locale="cn"
        )

        self.assertIsNone(result)

    async def test_market_link_search_requires_exact_article_and_item_filter(self):
        item_name = "Mageblood"
        query_url = search_url(item_name, "us")
        us_url = "https://poe2db.tw/us/Mageblood"
        mismatched_market_url = (
            "https://www.pathofexile.com/trade2/search/poe2/OldLeague"
            "?q=%7B%22query%22%3A%7B%22name%22%3A%22Different+Item%22%7D%7D"
        )
        pages = {
            query_url: """<html><head><title>search - Poe2DB</title></head>
              <body><div class="page-content"><div class="card"><ul class="list-group">
                <li><a href="/us/Mageblood">Mageblood</a></li>
              </ul></div></div></body></html>""",
            us_url: f"""<html><head><title>Mageblood Utility Belt - Poe2DB</title></head>
              <body><div class="page-content"><h3>Mageblood</h3>
                <table><tr><td>The Market</td><td><a href="{mismatched_market_url}">The Market</a></td></tr></table>
              </div></body></html>""",
        }
        client = FixtureClient(pages)

        result = await client.find_unique_market_link(item_name, "Forbidden Rites")

        self.assertIsNone(result)

    async def test_homepage_response_is_not_used_as_evidence(self):
        query_url = search_url("无关词")
        tw_url = search_url("无关词", "tw")
        client = FixtureClient(
            {
                query_url: "<title>家园 - 流亡2编年史</title><div class='page-content'>很多导航</div>",
                tw_url: "<title>家園 - 流亡2編年史</title><div class='page-content'>很多導航</div>",
            }
        )
        self.assertEqual(await client.find_sources(["无关词"]), [])

    async def test_browser_challenge_is_not_used_as_evidence(self):
        query_url = search_url("碎骨")
        tw_url = search_url("碎骨", "tw")
        client = FixtureClient(
            {
                query_url: "<title>Just a moment...</title><div class='page-content'>Cloudflare</div>",
                tw_url: "<title>Just a moment...</title><div class='page-content'>Cloudflare</div>",
            }
        )
        with self.assertRaises(Poe2DBError):
            await client.find_sources(["碎骨"])

    def test_query_terms_are_bounded(self):
        self.assertEqual(
            parse_query_terms('```json\n["碎骨", "沉重暈眩", "多余"]\n```', "问题"),
            ["碎骨", "沉重暈眩"],
        )
        self.assertEqual(parse_query_terms("bad JSON", "「魔血」有什么效果？"), ["魔血"])
        self.assertEqual(search_url("碎骨"), "https://poe2db.tw/cn/search?q=%E7%A2%8E%E9%AA%A8")
        self.assertEqual(
            search_url("Mageblood", "us"),
            "https://poe2db.tw/us/search?q=Mageblood",
        )
        self.assertTrue(Poe2DBClient._allowed("https://poe2db.tw/cn/Boneshatter"))
        self.assertTrue(Poe2DBClient._allowed("https://poe2db.tw/tw/Boneshatter"))
        self.assertTrue(Poe2DBClient._allowed("https://poe2db.tw/us/Mageblood"))
        self.assertFalse(Poe2DBClient._allowed("https://example.com/tw/Boneshatter"))
        with self.assertRaises(ValueError):
            search_url("碎骨", "en")

    def test_multilingual_names_are_bounded_and_ignore_invalid_entries(self):
        parsed = parse_multilingual_names(
            '{"zh":["碎骨", "魔血", "超出上限"],'
            '"tw":["碎骨"],"en":["Boneshatter", "Mageblood", "third"]}',
            max_names=2,
        )

        self.assertEqual(parsed["zh"], ["碎骨", "魔血"])
        self.assertEqual(parsed["tw"], ["碎骨"])
        self.assertEqual(parsed["en"], ["Boneshatter", "Mageblood"])
        self.assertEqual(
            parse_multilingual_names('{"en":["https://example.com", "Fireball"]}')[
                "en"
            ],
            ["Fireball"],
        )
        self.assertEqual(parse_multilingual_names("invalid"), {"zh": [], "tw": [], "en": []})
        self.assertEqual(
            parse_multilingual_names('{"en":["Fireball"]}', max_names=0),
            {"zh": [], "tw": [], "en": []},
        )

if __name__ == "__main__":
    unittest.main()
