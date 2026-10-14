"""Tests for resolving WeGame builds through Poe2DB's account API."""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.wegame_builds import (
    WeGameBuildCodeResolver,
    WeGameBuildError,
    _poe2db_detail_url,
)


SHARE_URL = (
    "https://www.wegame.com.cn/helper/poe2/#/share/"
    "nlnV7Nn3xLiETK2ExPutBpgnF9MAzSdC-z2oMuYtqCITMxBZhFrdzBLT5_cGd6DL"
)
POB_CODE = "eJz" + "A" * 100


class _FakeResponse:
    def __init__(self, body: bytes, url: str):
        self.body = body
        self.url = url
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self):
        return self.url

    def read(self, _limit):
        return self.body


class _FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


class WeGameBuildResolverTests(unittest.TestCase):
    def _api_response(self, payload):
        return _FakeResponse(
            json.dumps(payload).encode("utf-8"),
            "https://poe2db.tw/api/requestShareCode?share_code=test",
        )

    def test_rejects_non_wegame_urls_without_network_access(self):
        resolver = WeGameBuildCodeResolver()
        with patch("astrbot_plugin_poe2db.wegame_builds.build_opener") as build_opener:
            with self.assertRaises(WeGameBuildError):
                asyncio.run(resolver.convert("https://example.com/share/abc"))

        build_opener.assert_not_called()

    def test_resolves_share_code_and_reads_pob_code_from_chinese_build_page(self):
        opener = _FakeOpener(
            [
                self._api_response(
                    {"code": 200, "location": "qq_build?name=%E5%86%AF%E8%90%8C%E8%90%8C"}
                ),
                _FakeResponse(
                    f'<html><input id="pobCode" value="{POB_CODE}"></html>'.encode(),
                    "https://poe2db.tw/cn/qq_build?name=%E5%86%AF%E8%90%8C%E8%90%8C",
                ),
            ]
        )
        with patch(
            "astrbot_plugin_poe2db.wegame_builds.build_opener", return_value=opener
        ):
            actual = asyncio.run(WeGameBuildCodeResolver().convert(SHARE_URL))

        self.assertEqual(actual, POB_CODE)
        self.assertEqual(len(opener.requests), 2)
        api_request, api_timeout = opener.requests[0]
        page_request, page_timeout = opener.requests[1]
        self.assertIn("/api/requestShareCode?share_code=", api_request.full_url)
        self.assertIn("share_code=nlnV7Nn3xLiETK2ExPutBpgnF9MAzSdC-z2oMuYtqCITMxBZhFrdzBLT5_cGd6DL", api_request.full_url)
        self.assertEqual(
            page_request.full_url,
            "https://poe2db.tw/cn/qq_build?name=%E5%86%AF%E8%90%8C%E8%90%8C",
        )
        self.assertEqual(api_timeout, page_timeout)

    def test_rejects_api_locations_outside_poe2db_chinese_pages(self):
        opener = _FakeOpener(
            [self._api_response({"code": 200, "location": "https://example.com/x"})]
        )
        with patch(
            "astrbot_plugin_poe2db.wegame_builds.build_opener", return_value=opener
        ):
            with self.assertRaises(WeGameBuildError):
                asyncio.run(WeGameBuildCodeResolver().convert(SHARE_URL))

        self.assertEqual(len(opener.requests), 1)

    def test_reports_invalid_or_expired_shares_without_loading_a_page(self):
        opener = _FakeOpener([self._api_response({"code": 404})])
        with patch(
            "astrbot_plugin_poe2db.wegame_builds.build_opener", return_value=opener
        ):
            with self.assertRaisesRegex(WeGameBuildError, "链接仍有效"):
                asyncio.run(WeGameBuildCodeResolver().convert(SHARE_URL))

        self.assertEqual(len(opener.requests), 1)

    def test_requires_a_valid_pob_code_in_the_page(self):
        opener = _FakeOpener(
            [
                self._api_response({"code": 200, "location": "qq_build?name=test"}),
                _FakeResponse(
                    b'<html><input id="pobCode" value="invalid"></html>',
                    "https://poe2db.tw/cn/qq_build?name=test",
                ),
            ]
        )
        with patch(
            "astrbot_plugin_poe2db.wegame_builds.build_opener", return_value=opener
        ):
            with self.assertRaisesRegex(WeGameBuildError, "没有包含有效的 PoB2"):
                asyncio.run(WeGameBuildCodeResolver().convert(SHARE_URL))

    def test_detail_urls_must_stay_within_poe2db_cn(self):
        self.assertEqual(
            _poe2db_detail_url("qq_build?name=test"),
            "https://poe2db.tw/cn/qq_build?name=test",
        )
        for location in (
            "https://example.com/cn/qq_build",
            "//example.com/cn/qq_build",
            "../outside",
            "https://poe2db.tw/us/item",
        ):
            with self.subTest(location=location), self.assertRaises(WeGameBuildError):
                _poe2db_detail_url(location)


if __name__ == "__main__":
    unittest.main()
