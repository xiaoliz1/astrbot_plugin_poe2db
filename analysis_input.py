"""Validation and classification for `/poe2分析` arguments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import unquote, urlsplit


AnalysisInputKind = Literal["character", "poe_ninja", "poe_ninja_pob", "wegame"]
_WegameShareCode = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
_PoeNinjaLeagueSlug = re.compile(r"^[a-z0-9]+$")
_PoeNinjaPobId = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


@dataclass(frozen=True)
class AnalysisInput:
    kind: AnalysisInputKind
    value: str
    character_name: str | None = None
    league_slug: str | None = None
    pob_id: str | None = None


def classify_analysis_input(value: str) -> AnalysisInput:
    """Classify a character name, poe.ninja profile/PoB URL, or WeGame share URL."""
    text = value.strip()
    if not text:
        raise ValueError("请提供角色名、poe.ninja 角色详情/PoB 分享链接或 WeGame 分享链接。")
    if len(text) > 4096:
        raise ValueError("输入内容过长，请检查角色名或分享链接。")

    if not re.match(r"(?i)^https?://", text):
        if re.search(r"(?i)https?://", text):
            raise ValueError("请单独发送完整链接，不要在链接前后添加文字。")
        return AnalysisInput(kind="character", value=text)

    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise ValueError("链接格式无效，请检查后重试。") from exc
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
    ):
        raise ValueError("只接受 HTTPS 的 poe.ninja 详情链接或 WeGame 分享链接。")

    if parts.hostname == "poe.ninja":
        path_parts = parts.path.strip("/").split("/")
        if (
            len(path_parts) == 3
            and path_parts[:2] == ["poe2", "pob"]
            and _PoeNinjaPobId.fullmatch(path_parts[2])
        ):
            return AnalysisInput(
                kind="poe_ninja_pob",
                value=parts.geturl(),
                pob_id=path_parts[2],
            )
        if (
            len(path_parts) != 6
            or path_parts[0:2] != ["poe2", "builds"]
            or path_parts[3] != "character"
            or not _PoeNinjaLeagueSlug.fullmatch(path_parts[2])
        ):
            raise ValueError("请提供 poe.ninja 的角色 Builds 详情页链接。")
        account, encoded_name = path_parts[4:]
        decoded_account, character_name = unquote(account), unquote(encoded_name)
        if any(
            not segment
            or len(segment) > 200
            or any(ord(char) < 32 for char in segment)
            or "/" in segment
            or "\\" in segment
            for segment in (decoded_account, character_name)
        ):
            raise ValueError("poe.ninja 详情链接中的账号或角色名无效。")
        return AnalysisInput(
            kind="poe_ninja",
            value=parts.geturl(),
            character_name=character_name,
            league_slug=path_parts[2],
        )

    if parts.hostname == "www.wegame.com.cn":
        match = re.fullmatch(r"#/share/([A-Za-z0-9_-]{16,256})", "#" + parts.fragment)
        if (
            parts.path.rstrip("/") != "/helper/poe2"
            or parts.query
            or match is None
            or not _WegameShareCode.fullmatch(match.group(1))
        ):
            raise ValueError("请提供完整的 WeGame《流放之路2》分享链接。")
        return AnalysisInput(kind="wegame", value=parts.geturl())

    raise ValueError("目前只支持 poe.ninja 角色详情链接或 WeGame 构筑分享链接。")


def wegame_share_code_from_url(url: str) -> str:
    """Return the public share token after applying the same strict URL checks."""
    parsed = classify_analysis_input(url)
    if parsed.kind != "wegame":
        raise ValueError("这不是有效的 WeGame 构筑分享链接。")
    return urlsplit(parsed.value).fragment.removeprefix("/share/")
