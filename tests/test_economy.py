"""Current league selection, price parsing, and image markup safety."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.economy import (
    EconomyError,
    LEAGUES_URL,
    NinjaEconomyClient,
    localize_currency_snapshot,
    parse_currency_overview,
    render_currency_html,
)


def overview():
    return {
        "core": {"primary": "divine", "items": [{"id": "divine", "name": "Divine Orb"}]},
        "items": [
            {"id": "mirror", "name": "Mirror of Kalandra"},
            {"id": "chaos", "name": "Chaos Orb"},
            {"id": "invalid", "name": "Invalid"},
        ],
        "lines": [
            {"id": "chaos", "primaryValue": 0.1, "sparkline": {"totalChange": -2, "data": [-1, -2]}},
            {"id": "mirror", "primaryValue": 5000, "sparkline": {"totalChange": 4, "data": [0, 4]}},
            {"id": "invalid", "primaryValue": 0},
        ],
    }


class EconomyTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_league_id_is_used_and_can_change(self):
        class FixtureClient(NinjaEconomyClient):
            def __init__(self):
                super().__init__()
                self.league = "Forbidden Rites"
                self.urls = []

            async def _get_json(self, url, cache_seconds):
                self.urls.append(url)
                if url.endswith("/leagues"):
                    return [{"id": self.league, "name": self.league}]
                return overview()

        client = FixtureClient()
        first = await client.current_currency()
        self.assertEqual(first.league, "Forbidden Rites")
        self.assertEqual(first.reference, "Divine Orb")
        self.assertEqual([row.name for row in first.rows], ["Mirror of Kalandra", "Chaos Orb"])
        self.assertEqual(parse_qs(urlsplit(client.urls[-1]).query), {"league": ["Forbidden Rites"], "type": ["Currency"]})

        client.league = "Future Season"
        second = await client.current_currency()
        self.assertEqual(second.league, "Future Season")
        self.assertEqual(parse_qs(urlsplit(client.urls[-1]).query)["league"], ["Future Season"])

    def test_only_poe_ninja_currency_api_urls_are_allowed(self):
        allowed = NinjaEconomyClient._allowed
        self.assertTrue(allowed("https://poe.ninja/poe2/api/economy/leagues"))
        self.assertTrue(allowed("https://poe.ninja/poe2/api/economy/exchange/current/overview?league=Forbidden+Rites&type=Currency"))
        self.assertFalse(allowed("https://poe.ninja.evil.test/poe2/api/economy/leagues"))
        self.assertFalse(allowed("https://poe.ninja/poe1/api/economy/leagues"))
        self.assertFalse(allowed("https://poe.ninja/poe2/api/economy/exchange/current/overview?league=Wrong&type=Fragments"))

    def test_empty_price_data_is_not_a_valid_snapshot(self):
        with self.assertRaises(EconomyError):
            parse_currency_overview("Future Season", {"core": {"primary": "divine"}, "items": [], "lines": []})
        malformed = overview()
        malformed["core"]["items"] = 12
        with self.assertRaises(EconomyError):
            parse_currency_overview("Future Season", malformed)

    def test_invalid_utf8_response_becomes_a_source_error(self):
        class Response:
            headers = type("Headers", (), {"get_content_type": lambda self: "application/json"})()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return LEAGUES_URL

            def read(self, _limit):
                return b"\xff"

        class Opener:
            def open(self, _request, timeout):
                return Response()

        with patch("astrbot_plugin_poe2db.economy.build_opener", return_value=Opener()):
            with self.assertRaises(EconomyError):
                NinjaEconomyClient()._download_sync(LEAGUES_URL)

    def test_image_markup_escapes_remote_names(self):
        payload = overview()
        payload["items"][0]["name"] = '<img src=x onerror=alert(1)>{{7*7}}'
        snapshot = parse_currency_overview("Future <Season>", payload)
        markup = render_currency_html(snapshot)
        self.assertIn("Future &lt;Season&gt;", markup)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;&#123;&#123;7*7&#125;&#125;", markup)
        self.assertNotIn("<img src=x", markup)
        self.assertIn("Divine Orb", markup)
        self.assertIn("7 天趋势", markup)

    def test_currency_image_uses_chinese_names_and_units(self):
        snapshot = parse_currency_overview("Forbidden Rites", overview())
        localized = localize_currency_snapshot(
            snapshot,
            {"mirrorofkalandra": "卡兰德的魔镜", "chaosorb": "混沌石", "divineorb": "神圣石"},
            "禁断仪式",
        )
        markup = render_currency_html(localized)
        for term in ("卡兰德的魔镜", "混沌石", "神圣石", "禁断仪式", "5.00千"):
            self.assertIn(term, markup)
        for term in ("Mirror of Kalandra", "Chaos Orb", "Divine Orb", "Forbidden Rites", "5.00k"):
            self.assertNotIn(term, markup)


if __name__ == "__main__":
    unittest.main()
