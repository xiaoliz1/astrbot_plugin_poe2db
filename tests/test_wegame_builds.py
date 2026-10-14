"""Tests for the isolated PoBR browser conversion boundary."""

import asyncio
import json
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
    def __init__(self, url, resource_type="document", method="GET", response=None):
        self.request = SimpleNamespace(
            url=url, resource_type=resource_type, method=method
        )
        self.response = response
        self.action = None
        self.fulfilled = None

    async def abort(self):
        self.action = "abort"

    async def continue_(self):
        self.action = "continue"

    async def fetch(self):
        return self.response

    async def fulfill(self, **kwargs):
        self.action = "fulfill"
        self.fulfilled = kwargs


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def json(self):
        return self.payload

    async def all_headers(self):
        return {
            "content-type": "application/json",
            "content-length": "99",
            "content-encoding": "gzip",
        }


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
        allowed = _FakeRoute("https://pobr-web.pages.dev/assets/app.js", "script")
        image = _FakeRoute("https://pobr-web.pages.dev/image.png", "image")
        external = _FakeRoute("https://example.com/script.js", "script")
        insecure = _FakeRoute("http://pobr-web.pages.dev/script.js", "script")

        for route in (allowed, image, external, insecure):
            asyncio.run(WeGameBuildCodeResolver._route_pobr_page(route))

        self.assertEqual(allowed.action, "continue")
        self.assertEqual(image.action, "abort")
        self.assertEqual(external.action, "abort")
        self.assertEqual(insecure.action, "abort")

    def test_import_normalizes_empty_jewel_data_but_preserves_other_fields(self):
        response = _FakeResponse(
            {"format": "wegame", "version": 1, "jewel_data": "", "role": {"level": 100}}
        )
        route = _FakeRoute(
            "https://pobr-web.pages.dev/api/import/wegame",
            "fetch",
            "POST",
            response,
        )

        asyncio.run(WeGameBuildCodeResolver._route_pobr_page(route))

        self.assertEqual(route.action, "fulfill")
        self.assertEqual(json.loads(route.fulfilled["body"]), {
            "format": "wegame",
            "version": 1,
            "jewel_data": "[]",
            "role": {"level": 100},
        })
        self.assertNotIn("content-length", route.fulfilled["headers"])
        self.assertNotIn("content-encoding", route.fulfilled["headers"])

    def test_import_does_not_rewrite_nonempty_or_error_payloads(self):
        payloads = (
            {"format": "wegame", "jewel_data": "[{\"jewel\":null}]"},
            {"error": "share expired"},
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                response = _FakeResponse(payload, status=502 if "error" in payload else 200)
                route = _FakeRoute(
                    "https://pobr-web.pages.dev/api/import/wegame",
                    "fetch",
                    "POST",
                    response,
                )

                asyncio.run(WeGameBuildCodeResolver._route_pobr_page(route))

                self.assertEqual(route.action, "fulfill")
                self.assertIs(route.fulfilled["response"], response)

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
