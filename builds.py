"""Public poe.ninja PoE2 character-search links."""

from __future__ import annotations

import re
from urllib.parse import urlencode


def character_search_page_url(character_name: str, league_name: str) -> str:
    """Build a public Builds-page link filtered by the supplied character name."""
    query = character_name.strip()
    league_slug = re.sub(r"[^a-z0-9]", "", league_name.casefold())
    if not query or len(query) > 100:
        raise ValueError("角色名为空或超过 100 个字符")
    if not league_slug:
        raise ValueError("当前赛季名称无效")
    # The public Builds page uses both search state and a name filter in shared links.
    params = urlencode({"i": "0", "search": f"name={query}", "name": query})
    return f"https://poe.ninja/poe2/builds/{league_slug}?{params}"
