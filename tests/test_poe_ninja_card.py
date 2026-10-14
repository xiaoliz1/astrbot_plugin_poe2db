"""Tests for public poe.ninja build preview images."""

import asyncio
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.poe_ninja_card import (
    PoeNinjaCardResolver,
    public_build_card_url,
)


PROFILE_URL = (
    "https://poe.ninja/poe2/builds/forbiddenrites/character/"
    "heygyus-0416/ResurrectForbidden?i=0&search=class%3DGemling"
)
PROFILE_CARD = (
    "https://poe.ninja/poe2/builds/forbiddenrites/character/"
    "heygyus-0416/ResurrectForbidden/card/current.png"
)


class _FakeResponse:
    def __init__(self, url: str, content_type: str = "image/png", status: int = 200):
        self.url = url
        self.headers = {"Content-Type": content_type}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self):
        return self.url


class _FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class PoeNinjaCardTests(unittest.TestCase):
    def test_card_urls_strip_query_and_only_accept_public_ninja_builds(self):
        self.assertEqual(public_build_card_url(PROFILE_URL), PROFILE_CARD)
        self.assertEqual(
            public_build_card_url("https://poe.ninja/poe2/pob/2b2ef?foo=bar"),
            "https://poe.ninja/poe2/pob/2b2ef/card/current.png",
        )
        self.assertIsNone(public_build_card_url("https://example.com/poe2/pob/2b2ef"))
        self.assertIsNone(public_build_card_url("https://www.wegame.com.cn/helper/poe2/#/share/" + "A" * 20))
        self.assertIsNone(public_build_card_url("ResurrectForbidden"))

    def test_accepts_an_official_png_after_same_profile_redirect(self):
        final_url = PROFILE_CARD.replace("current.png", "1788861427.png")
        opener = _FakeOpener(_FakeResponse(final_url))
        with patch("astrbot_plugin_poe2db.poe_ninja_card.build_opener", return_value=opener):
            actual = asyncio.run(PoeNinjaCardResolver(11).resolve(PROFILE_URL))

        self.assertEqual(actual, final_url)
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, PROFILE_CARD)
        self.assertEqual(request.get_method(), "HEAD")
        self.assertEqual(timeout, 11)

    def test_skips_non_image_or_unrelated_redirect(self):
        for response in (
            _FakeResponse(PROFILE_CARD, "text/html"),
            _FakeResponse("https://poe.ninja/other/card/current.png"),
            _FakeResponse("https://example.com/card/current.png"),
        ):
            with self.subTest(url=response.url, content_type=response.headers["Content-Type"]):
                with patch(
                    "astrbot_plugin_poe2db.poe_ninja_card.build_opener",
                    return_value=_FakeOpener(response),
                ):
                    self.assertIsNone(asyncio.run(PoeNinjaCardResolver().resolve(PROFILE_URL)))

    def test_missing_card_is_non_fatal(self):
        opener = _FakeOpener(HTTPError(PROFILE_CARD, 404, "Not Found", {}, None))
        with patch("astrbot_plugin_poe2db.poe_ninja_card.build_opener", return_value=opener):
            self.assertIsNone(asyncio.run(PoeNinjaCardResolver().resolve(PROFILE_URL)))


if __name__ == "__main__":
    unittest.main()
