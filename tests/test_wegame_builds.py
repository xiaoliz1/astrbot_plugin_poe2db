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


class _FakeLocator:
    def __init__(self, events, selector):
        self.events = events
        self.selector = selector
        self.first = self

    async def wait_for(self, state):
        self.events.append(("wait", self.selector, state))

    async def fill(self, value):
        self.events.append(("fill", self.selector, value))

    async def click(self):
        self.events.append(("click", self.selector))


class _FakePage:
    def __init__(self):
        self.events = []

    def locator(self, selector):
        return _FakeLocator(self.events, selector)

    def get_by_role(self, role, name):
        return _FakeLocator(self.events, f"role:{role}:{name.pattern}")


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

    def test_import_waits_for_items_tab_then_returns_to_build_before_export(self):
        page = _FakePage()

        asyncio.run(WeGameBuildCodeResolver._return_to_build_panel(page))

        self.assertEqual(
            page.events,
            [
                ("wait", "role:heading:^items$", "visible"),
                ("wait", "role:button:^build$", "visible"),
                ("click", "role:button:^build$"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
