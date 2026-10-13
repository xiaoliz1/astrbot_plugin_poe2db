"""Public poe.ninja PoE2 character-search links."""

from __future__ import annotations

import re
from urllib.parse import urlencode


def character_search_url(character_id: str, league_name: str) -> str:
    """Build a public Builds-page link filtered by the supplied character name."""
    query = character_id.strip()
    league_slug = re.sub(r"[^a-z0-9]", "", league_name.casefold())
    if not query or len(query) > 100:
        raise ValueError("角色名或 ID 为空或过长")
    if not league_slug:
        raise ValueError("当前赛季名称无效")
    params = urlencode({"i": "0", "search": f"name={query}"})
    return f"https://poe.ninja/poe2/builds/{league_slug}?{params}"
