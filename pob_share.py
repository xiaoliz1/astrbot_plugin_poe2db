"""Export a public poe.ninja character build to a Chinese PoE2DB PoB page."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


POE2DB_POB_IMPORT_URL = "https://poe2db.tw/cn/pob"
POE2DB_POB_API_URL = "https://poe2db.tw/pob/api/gen"
POE2DB_POB_URL_PREFIX = "https://poe2db.tw/cn/pob/"
_POB_CODE_RE = re.compile(r"^[A-Za-z0-9_+/=-]{80,1000000}$")
_SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def format_character_build_links(
    profile_url: str,
    share_url: str | None,
    source_label: str = "角色详情",
) -> str:
    """Format a build source link followed by its Chinese PoB share page."""
    result = f"{source_label}：{profile_url}\nPoe2DB 简体中文 Build："
    if share_url:
        return f"{result}\n{share_url}"
    return (
        f"{result}\n本次未能自动生成；可稍后重试或手动导入 PoB：\n"
        f"{POE2DB_POB_IMPORT_URL}"
    )


class PobShareError(RuntimeError):
    """A character PoB code could not be exported or shared."""


def character_identity_from_profile_url(profile_url: str) -> tuple[str, str]:
    """Extract the public account and character path segments from poe.ninja."""
    parsed = urlsplit(profile_url)
    if parsed.scheme != "https" or parsed.hostname != "poe.ninja":
        raise PobShareError("角色详情链接不是有效的 poe.ninja 地址。")

    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    try:
        index = segments.index("character")
    except ValueError as exc:
        raise PobShareError("角色详情链接中没有账号和角色名。") from exc

    identity = segments[index + 1 :]
    if len(identity) != 2:
        raise PobShareError("角色详情链接中的账号或角色名不完整。")
    account, character = identity
    if any(not value or len(value) > 100 or any(ord(ch) < 32 for ch in value) for value in identity):
        raise PobShareError("角色详情链接中的账号或角色名无效。")
    return account, character


def _result_text(result: object) -> str:
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content", [])
    parts: list[str] = []
    for item in content or []:
        if isinstance(item, str):
            parts.append(item)
            continue
        if isinstance(item, dict):
            text = item.get("text") if item.get("type", "text") == "text" else None
        else:
            text = getattr(item, "text", None) if getattr(item, "type", "text") == "text" else None
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def compact_import_pob_result(text: str) -> str:
    """Drop duplicated item text fields while keeping all parsed build sections."""
    json_start = text.find("{")
    if json_start < 0:
        return text
    try:
        payload = json.loads(text[json_start:])
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict):
        return text

    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                item.pop("raw_text", None)
                item.pop("full_text", None)
    return text[:json_start].rstrip() + "\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )


def _valid_pob_code(value: str) -> bool:
    compact = "".join(value.split()).strip("`")
    if not _POB_CODE_RE.fullmatch(compact):
        return False
    padded = compact + "=" * (-len(compact) % 4)
    try:
        return bool(base64.b64decode(padded, altchars=b"-_", validate=True))
    except (ValueError, binascii.Error):
        return False


def extract_pob_code(result: object) -> str:
    """Return the base64 PoB share code from get_pob_code's MCP response."""
    text = _result_text(result)
    marker = re.search(r"Path of Building Code(?: for [^:\r\n]*)?:", text, re.I)
    if marker:
        for line in text[marker.end() :].splitlines():
            candidate = "".join(line.strip().strip("`").split())
            if not candidate:
                continue
            if _valid_pob_code(candidate):
                return candidate
            break

    for line in text.splitlines():
        candidate = "".join(line.strip().strip("`").split())
        if _valid_pob_code(candidate):
            return candidate
    raise PobShareError("poe2-mcp 没有返回有效的 Path of Building 导出码。")


async def fetch_pob_code_from_tool(
    tool: object,
    event: object,
    account: str,
    character: str,
    timeout_seconds: int = 120,
) -> str:
    """Invoke the permission-guarded AstrBot MCP tool without an LLM round trip."""
    from types import SimpleNamespace

    runtime_context = SimpleNamespace(
        context=SimpleNamespace(event=event),
        tool_call_timeout=timeout_seconds,
    )
    call = getattr(tool, "call", None)
    if call is None:
        raise PobShareError("AstrBot 的 get_pob_code 工具不可调用。")
    result = await call(runtime_context, account=account, character=character)
    return extract_pob_code(result)


async def import_pob_from_tool(
    tool: object,
    event: object,
    pob_code: str,
    timeout_seconds: int = 120,
) -> str:
    """Import a PoB code directly through AstrBot's permission-guarded MCP tool."""
    from types import SimpleNamespace

    compact_code = "".join(pob_code.split()).strip("`")
    if not _valid_pob_code(compact_code):
        raise PobShareError("Path of Building 导入码格式无效。")

    call = getattr(tool, "call", None)
    if call is None:
        raise PobShareError("AstrBot 的 import_pob 工具不可调用。")

    runtime_context = SimpleNamespace(
        context=SimpleNamespace(event=event),
        tool_call_timeout=timeout_seconds,
    )
    try:
        result = await call(runtime_context, pob_code=compact_code)
    except Exception as exc:
        raise PobShareError(f"poe2-mcp 导入 PoB2 失败：{exc}") from exc

    is_error = getattr(result, "isError", getattr(result, "is_error", None))
    if is_error is None and isinstance(result, dict):
        is_error = result.get("isError", result.get("is_error", False))
    text = _result_text(result).strip()
    if is_error:
        raise PobShareError(text or "poe2-mcp 返回导入错误。")

    if not text:
        structured = getattr(result, "structuredContent", None)
        if structured is None and isinstance(result, dict):
            structured = result.get("structuredContent", result.get("structured_content"))
        if structured is not None:
            text = json.dumps(structured, ensure_ascii=False, default=str)
    if not text:
        raise PobShareError("poe2-mcp 没有返回导入后的构筑数据。")
    return compact_import_pob_result(text)


def _share_id_from_response(response_text: str) -> str:
    """Accept the documented API's raw ID or a JSON/URL response."""
    raw = response_text.strip()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        payload = raw

    candidates: list[object] = []
    if isinstance(payload, dict):
        for key in ("id", "key", "code", "shareId", "share_id", "url", "link"):
            if key in payload:
                candidates.append(payload[key])
    else:
        candidates.append(payload)

    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        value = candidate.strip().strip('"\'')
        if value.startswith("http://") or value.startswith("https://"):
            parsed = urlsplit(value)
            if parsed.scheme != "https" or parsed.hostname != "poe2db.tw":
                continue
            segments = [segment for segment in parsed.path.split("/") if segment]
            if len(segments) >= 2 and segments[-2] == "pob":
                value = segments[-1]
            else:
                continue
        if _SHARE_ID_RE.fullmatch(value):
            return value
    raise PobShareError("Poe2DB 没有返回有效的中文 PoB 分享编号。")


class Poe2DBPobClient:
    """Create and cache Poe2DB share pages for public PoB export codes."""

    def __init__(self, timeout_seconds: int = 20, data_dir: str | Path | None = None):
        self.timeout_seconds = max(5, min(int(timeout_seconds), 60))
        self._cache_path = Path(data_dir) / "pob_share_cache.json" if data_dir else None
        self._cache = self._load_cache()
        self._lock = asyncio.Lock()

    def _load_cache(self) -> dict[str, str]:
        if not self._cache_path or not self._cache_path.is_file():
            return {}
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            key: value
            for key, value in payload.items()
            if isinstance(key, str) and re.fullmatch(r"[a-f0-9]{64}", key)
            and isinstance(value, str) and _SHARE_ID_RE.fullmatch(value)
        }

    def _save_cache(self) -> None:
        if not self._cache_path:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self._cache, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self._cache_path)
        except OSError:
            # Sharing already succeeded; a failed cache write should not hide the URL.
            pass

    def _post_code(self, code: str) -> str:
        request = Request(
            POE2DB_POB_API_URL,
            data=code.encode("ascii"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": (
                    "astrbot_plugin_poe2db/1.22.1 "
                    "(https://github.com/xiaoliz1/astrbot_plugin_poe2db; "
                    "contact: https://github.com/xiaoliz1/astrbot_plugin_poe2db/issues)"
                ),
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status != 200:
                    raise PobShareError(f"Poe2DB 分享接口返回 HTTP {response.status}。")
                return response.read(100_000).decode("utf-8", errors="replace")
        except HTTPError as exc:
            raise PobShareError(f"Poe2DB 分享接口返回 HTTP {exc.code}。") from exc
        except URLError as exc:
            raise PobShareError("无法连接 Poe2DB 分享接口。") from exc

    async def create_share_url(self, code: str) -> str:
        compact = "".join(code.split()).strip("`")
        if not _valid_pob_code(compact):
            raise PobShareError("Path of Building 导出码格式无效。")
        digest = hashlib.sha256(compact.encode("ascii")).hexdigest()
        async with self._lock:
            share_id = self._cache.get(digest)
            if not share_id:
                response_text = await asyncio.to_thread(self._post_code, compact)
                share_id = _share_id_from_response(response_text)
                self._cache[digest] = share_id
                self._save_cache()
        return POE2DB_POB_URL_PREFIX + share_id
