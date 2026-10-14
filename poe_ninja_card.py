"""Resolve poe.ninja's public build preview image for an analyzed build."""

from __future__ import annotations

import asyncio
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener

from .analysis_input import classify_analysis_input
from .poe_ninja_pob import _PoeNinjaRedirectHandler, _is_poe_ninja_https_url


def public_build_card_url(source_url: str) -> str | None:
    """Build the card endpoint for a validated public profile or PoB share."""
    try:
        parsed = classify_analysis_input(source_url)
    except ValueError:
        return None
    if parsed.kind not in {"poe_ninja", "poe_ninja_pob"}:
        return None
    path = urlsplit(parsed.value).path.rstrip("/") + "/card/current.png"
    return urlunsplit(("https", "poe.ninja", path, "", ""))


class PoeNinjaCardResolver:
    """Check that an official public card is a PNG before sending its URL."""

    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = max(5, min(int(timeout_seconds), 30))

    async def resolve(self, source_url: str) -> str | None:
        card_url = public_build_card_url(source_url)
        if card_url is None:
            return None
        try:
            return await asyncio.to_thread(self._probe_card, card_url)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            return None

    def _probe_card(self, card_url: str) -> str | None:
        request = Request(
            card_url,
            method="HEAD",
            headers={
                "Accept": "image/png",
                "User-Agent": "AstrBot-PoE2DB (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)",
            },
        )
        opener = build_opener(_PoeNinjaRedirectHandler())
        with opener.open(request, timeout=self.timeout_seconds) as response:
            final_url = response.geturl()
            card_prefix = urlsplit(card_url).path.removesuffix("current.png")
            final_path = urlsplit(final_url).path
            if (
                not _is_poe_ninja_https_url(final_url)
                or not final_path.startswith(card_prefix)
                or not final_path.endswith(".png")
                or getattr(response, "status", 200) != 200
                or response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                != "image/png"
            ):
                return None
            return final_url
