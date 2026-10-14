"""Tests for resolving public poe.ninja Path of Building share links."""

import asyncio
import sys
import unittest
from pathlib import Path
from urllib.request import Request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.poe_ninja_pob import (
    POE_NINJA_POB_RAW_URL,
    PoeNinjaPobError,
    PoeNinjaPobResolver,
    _PoeNinjaRedirectHandler,
)


SHARE_URL = "https://poe.ninja/poe2/pob/2b2ef"
POB_CODE = "eNrt" + "A" * 120


class _FakeResponse:
    def __init__(self, body: bytes, url: str, status: int = 200):
        self.body = body
        self.url = url
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.body[:limit]


class _FakeOpener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.response


class PoeNinjaPobResolverTests(unittest.TestCase):
    def test_reads_raw_code_for_a_valid_public_share_link(self):
        opener = _FakeOpener(
            _FakeResponse(
                POB_CODE.encode("ascii"),
                "https://poe.ninja/poe2/pob/raw/2b2ef",
            )
        )
        with patch(
            "astrbot_plugin_poe2db.poe_ninja_pob.build_opener", return_value=opener
        ):
            actual = asyncio.run(PoeNinjaPobResolver(timeout_seconds=11).resolve(SHARE_URL))

        self.assertEqual(actual, POB_CODE)
        self.assertEqual(len(opener.requests), 1)
        request, timeout = opener.requests[0]
        self.assertIsInstance(request, Request)
        self.assertEqual(request.full_url, POE_NINJA_POB_RAW_URL.format("2b2ef"))
        self.assertEqual(timeout, 11)

    def test_rejects_other_urls_before_network_access(self):
        with patch("astrbot_plugin_poe2db.poe_ninja_pob.build_opener") as build_opener:
            with self.assertRaises(PoeNinjaPobError):
                asyncio.run(PoeNinjaPobResolver().resolve("https://example.com/pob/2b2ef"))

        build_opener.assert_not_called()

    def test_rejects_a_non_pob_response(self):
        opener = _FakeOpener(
            _FakeResponse(b"not a share code", "https://poe.ninja/poe2/pob/raw/2b2ef")
        )
        with patch(
            "astrbot_plugin_poe2db.poe_ninja_pob.build_opener", return_value=opener
        ):
            with self.assertRaisesRegex(PoeNinjaPobError, "没有返回有效的 PoB2"):
                asyncio.run(PoeNinjaPobResolver().resolve(SHARE_URL))

    def test_redirect_handler_blocks_other_hosts(self):
        handler = _PoeNinjaRedirectHandler()
        request = Request("https://poe.ninja/poe2/pob/raw/2b2ef")

        self.assertIsNone(
            handler.redirect_request(
                request, None, 302, "Found", {}, "https://example.com/payload"
            )
        )


if __name__ == "__main__":
    unittest.main()
