"""Read and track new official Path of Exile 2 announcements and patch notes."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


POE_FORUM_BASE_URL = "https://www.pathofexile.com"
OFFICIAL_UPDATE_CATEGORIES = (
    ("announcements", f"{POE_FORUM_BASE_URL}/forum/view-forum/2211"),
    ("patch_notes", f"{POE_FORUM_BASE_URL}/forum/view-forum/2212"),
)
MAX_REMEMBERED_UPDATE_IDS = 500
_THREAD_PATH = re.compile(r"^/forum/view-thread/(\d+)(?:/|$)")
_ANNOUNCEMENT_TITLE_HINT = re.compile(
    r"\b(?:league|season|patch(?:\s+notes)?|hotfix|update|"
    r"content\s+update|expansion|roadmap)\b|"
    r"\b0\.\d+(?:\.\d+)?[a-z]?\b|"
    r":\s*[^:]{1,80}\bfaq\b",
    re.IGNORECASE,
)
_POE2_NAMED_ANNOUNCEMENT_TITLE_HINT = re.compile(
    r"\bpath\s+of\s+exile\s+2\s*:",
    re.IGNORECASE,
)
_NON_UPDATE_ANNOUNCEMENT_HINT = re.compile(
    r"\b(?:registration|armou?r\s+effect|supporter\s+pack|"
    r"microtransactions?|hideout\s+decoration)\b",
    re.IGNORECASE,
)
_PATCH_NOTE_TITLE_HINT = re.compile(
    r"\bpatch(?:\s+notes)?\b|\bhotfix\b",
    re.IGNORECASE,
)
_CATEGORY_LABELS = {
    "announcements": "官方公告",
    "patch_notes": "Patch Notes",
}


@dataclass(frozen=True)
class OfficialUpdate:
    """A single thread listed in one of GGG's official PoE2 forums."""

    id: str
    title: str
    url: str
    category: str


class OfficialUpdateError(RuntimeError):
    """Raised when an official forum category cannot be read reliably."""


def parse_official_update_page(html: str, category: str) -> list[OfficialUpdate]:
    """Extract relevant thread links from a GGG PoE2 forum category page."""
    if category not in _CATEGORY_LABELS:
        raise ValueError(f"Unsupported official update category: {category}")

    soup = BeautifulSoup(html, "html.parser")
    results: dict[str, OfficialUpdate] = {}
    for anchor in soup.find_all("a", href=True):
        href = unescape(str(anchor.get("href", ""))).strip()
        path = urlsplit(href).path
        match = _THREAD_PATH.match(path)
        if not match:
            continue

        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title or title.isdecimal():
            continue
        if category == "announcements":
            is_update = bool(_ANNOUNCEMENT_TITLE_HINT.search(title))
            is_named_poe2_post = bool(
                _POE2_NAMED_ANNOUNCEMENT_TITLE_HINT.search(title)
            )
            if not is_update and not is_named_poe2_post:
                continue
            if (
                is_named_poe2_post
                and not is_update
                and _NON_UPDATE_ANNOUNCEMENT_HINT.search(title)
            ):
                continue
        elif not _PATCH_NOTE_TITLE_HINT.search(title):
            continue

        thread_id = match.group(1)
        update = OfficialUpdate(
            id=f"{category}:{thread_id}",
            title=title,
            url=urljoin(POE_FORUM_BASE_URL, f"/forum/view-thread/{thread_id}"),
            category=category,
        )
        results.setdefault(update.id, update)

    return list(results.values())


def _fetch_category(category: str, url: str, timeout_seconds: int) -> list[OfficialUpdate]:
    request = Request(
        url,
        headers={
            "User-Agent": "astrbot_plugin_poe2db official update tracker",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            html = response.read().decode(charset, errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise OfficialUpdateError(f"读取 GGG {category} 页面失败：{exc}") from exc

    if "view-thread/" not in html or "Attention Required" in html:
        raise OfficialUpdateError(
            f"GGG {category} 页面未返回帖子列表，可能暂时无法访问：{url}"
        )
    return parse_official_update_page(html, category)


def fetch_official_updates(timeout_seconds: int = 15) -> list[OfficialUpdate]:
    """Fetch current GGG PoE2 announcement and patch-note listings."""
    updates: list[OfficialUpdate] = []
    for category, url in OFFICIAL_UPDATE_CATEGORIES:
        updates.extend(_fetch_category(category, url, timeout_seconds))
    return updates


def format_official_update_notice(updates: list[OfficialUpdate]) -> str:
    lines = ["《流放之路 2》官方赛季 / Patch 更新"]
    for update in updates:
        category_label = _CATEGORY_LABELS.get(update.category, "官方公告")
        lines.extend((f"【{category_label}】{update.title}", update.url))
    return "\n".join(lines)


class OfficialUpdateStore:
    """Persist the first-seen baseline and per-group delivery state."""

    def __init__(self, data_dir: str | Path) -> None:
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / "official_updates.json"
        self.initialized = False
        self._seen_ids: list[str] = []
        self._pending: dict[str, dict[str, object]] = {}
        self._load()

    def observe(self, updates: list[OfficialUpdate]) -> list[OfficialUpdate]:
        """Queue newly listed posts; the first successful scan is a quiet baseline."""
        if not self.initialized:
            self._seen_ids = list(dict.fromkeys(item.id for item in updates))[
                -MAX_REMEMBERED_UPDATE_IDS:
            ]
            self.initialized = True
            self._save()
            return []

        seen = set(self._seen_ids)
        discovered: list[OfficialUpdate] = []
        for update in updates:
            if update.id in seen or update.id in self._pending:
                continue
            seen.add(update.id)
            self._seen_ids.append(update.id)
            self._pending[update.id] = {
                "update": update,
                "delivered_to": set(),
            }
            discovered.append(update)

        self._trim_seen_ids()
        self._save()
        return discovered

    def _trim_seen_ids(self) -> None:
        if len(self._seen_ids) <= MAX_REMEMBERED_UPDATE_IDS:
            return
        pending_ids = set(self._pending)
        recent_ids = set(self._seen_ids[-MAX_REMEMBERED_UPDATE_IDS:])
        self._seen_ids = [
            update_id
            for update_id in self._seen_ids
            if update_id in pending_ids or update_id in recent_ids
        ]

    def pending_for(self, destination: str) -> list[OfficialUpdate]:
        pending: list[OfficialUpdate] = []
        for entry in self._pending.values():
            delivered_to = entry.get("delivered_to", set())
            update = entry.get("update")
            if isinstance(update, OfficialUpdate) and destination not in delivered_to:
                pending.append(update)
        return pending

    def pending_updates(self) -> list[OfficialUpdate]:
        updates: list[OfficialUpdate] = []
        for entry in self._pending.values():
            update = entry.get("update")
            if isinstance(update, OfficialUpdate):
                updates.append(update)
        return updates

    def mark_delivered(self, destination: str, update_ids: list[str]) -> None:
        for update_id in update_ids:
            entry = self._pending.get(update_id)
            if entry is not None:
                delivered_to = entry.setdefault("delivered_to", set())
                if isinstance(delivered_to, set):
                    delivered_to.add(destination)
        self._save()

    def prune_delivered(self, destinations: list[str]) -> None:
        if not destinations:
            return
        destination_set = set(destinations)
        completed = [
            update_id
            for update_id, entry in self._pending.items()
            if destination_set.issubset(entry.get("delivered_to", set()))
        ]
        if not completed:
            return
        for update_id in completed:
            self._pending.pop(update_id, None)
        self._save()

    def _load(self) -> None:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return
        if not isinstance(state, dict) or state.get("version") != 1:
            return

        seen_ids = state.get("seen_ids")
        if isinstance(seen_ids, list):
            self._seen_ids = [
                item for item in seen_ids if isinstance(item, str)
            ][-MAX_REMEMBERED_UPDATE_IDS:]
        self.initialized = bool(state.get("initialized", False))

        pending = state.get("pending")
        if not isinstance(pending, list):
            return
        for entry in pending:
            if not isinstance(entry, dict):
                continue
            update_data = entry.get("update")
            if not isinstance(update_data, dict):
                continue
            try:
                update = OfficialUpdate(**update_data)
            except TypeError:
                continue
            delivered_to = entry.get("delivered_to", [])
            self._pending[update.id] = {
                "update": update,
                "delivered_to": {
                    item for item in delivered_to if isinstance(item, str)
                }
                if isinstance(delivered_to, list)
                else set(),
            }

    def _save(self) -> None:
        state = {
            "version": 1,
            "initialized": self.initialized,
            "seen_ids": self._seen_ids,
            "pending": [
                {
                    "update": asdict(entry["update"]),
                    "delivered_to": sorted(entry.get("delivered_to", set())),
                }
                for entry in self._pending.values()
                if isinstance(entry.get("update"), OfficialUpdate)
            ],
        }
        temporary_path = self.state_path.with_name(
            f".{self.state_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary_path, self.state_path)
        finally:
            temporary_path.unlink(missing_ok=True)
