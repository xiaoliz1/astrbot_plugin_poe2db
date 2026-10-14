"""Tests for the isolated PoBR browser conversion boundary."""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.wegame_builds import (
    WeGameBuildCodeResolver,
    WeGameBuildError,
)


class _FakeRoute:
    def __init__(self, url, resource_type="document"):
        self.request = SimpleNamespace(url=url, resource_type=resource_type)
        self.action = None

    async def abort(self):
        self.action = "abort"

    async def continue_(self):
        self.action = "continue"


class WeGameBuildResolverTests(unittest.TestCase):
    def test_rejects_non_wegame_urls_before_starting_chromium(self):
        resolver = WeGameBuildCodeResolver()

        with self.assertRaises(WeGameBuildError):
            asyncio.run(resolver.convert("https://example.com/share/abc"))

        self.assertIsNone(resolver._playwright)
        self.assertIsNone(resolver._browser)

    def test_browser_only_allows_secure_pobr_origin_and_needed_text_resources(self):
        allowed = _FakeRoute("https://pobr-web.pages.dev/api/import/wegame", "fetch")
        image = _FakeRoute("https://pobr-web.pages.dev/image.png", "image")
        external = _FakeRoute("https://example.com/script.js", "script")
        insecure = _FakeRoute("http://pobr-web.pages.dev/script.js", "script")

        for route in (allowed, image, external, insecure):
            asyncio.run(WeGameBuildCodeResolver._route_pobr_page(route))

        self.assertEqual(allowed.action, "continue")
        self.assertEqual(image.action, "abort")
        self.assertEqual(external.action, "abort")
        self.assertEqual(insecure.action, "abort")


if __name__ == "__main__":
    unittest.main()
