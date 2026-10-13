"""Focused tests for site result parsing and source boundaries."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.poe2db import (
    CURRENCY_PAGE,
    LEAGUE_CN_PAGE,
    LEAGUE_US_PAGE,
    Poe2DBClient,
    Poe2DBError,
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
        self.assertTrue(Poe2DBClient._allowed("https://poe2db.tw/cn/Boneshatter"))
        self.assertTrue(Poe2DBClient._allowed("https://poe2db.tw/tw/Boneshatter"))
        self.assertFalse(Poe2DBClient._allowed("https://example.com/tw/Boneshatter"))
        with self.assertRaises(ValueError):
            search_url("碎骨", "en")

    async def test_currency_names_and_league_name_are_read_from_live_page_shapes(self):
        client = FixtureClient({
            CURRENCY_PAGE: (
                "<title>Currency_Exchange - 流亡2编年史</title>"
                "<div class='page-content'>"
                "<a class='item_currency StackableCurrency' href='Mirror_of_Kalandra'>"
                "<img src='mirror.png'></a>"
                "<a class='item_currency StackableCurrency' href='Mirror_of_Kalandra'>卡兰德的魔镜</a>"
                "<a class='item_currency' href='Divine_Orb'>神圣石</a>"
                "</div>"
            ),
            LEAGUE_CN_PAGE: (
                "<title>赛季 - 流亡2编年史</title><div class='page-content'>"
                "<table><tr><td>0.5.5</td><td>禁断仪式</td></tr></table></div>"
            ),
            LEAGUE_US_PAGE: (
                "<title>Leagues - PoE2DB</title><div class='page-content'>"
                "<table><tr><td>0.5.5</td><td>Forbidden Rites</td></tr></table></div>"
            ),
        })
        names = await client.currency_names()
        self.assertEqual(names["mirrorofkalandra"], "卡兰德的魔镜")
        self.assertEqual(names["divineorb"], "神圣石")
        self.assertEqual(await client.chinese_league_name("Forbidden Rites"), "禁断仪式")
        self.assertEqual(await client.chinese_league_name("Unknown Season"), None)


if __name__ == "__main__":
    unittest.main()
