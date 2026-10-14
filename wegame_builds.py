"""Resolve public WeGame PoE2 shares through Poe2DB's Chinese account page."""

from __future__ import annotations

import asyncio
import json
import logging
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .analysis_input import classify_analysis_input, wegame_share_code_from_url
from .builds import normalize_visible_pob_code

LOGGER = logging.getLogger(__name__)
POE2DB_ACCOUNT_URL = "https://poe2db.tw/cn/account"
POE2DB_SHARE_API_URL = "https://poe2db.tw/api/requestShareCode"
_POE2DB_HOST = "poe2db.tw"
_MAX_API_RESPONSE_BYTES = 128_000
_MAX_BUILD_PAGE_BYTES = 5_000_000


class WeGameBuildError(RuntimeError):
    """A public WeGame share could not be resolved to a PoB2 build."""


class _PoBCodeParser(HTMLParser):
    """Extract the PoB code input from a Poe2DB build page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.value = ""

    def handle_starttag(self, tag, attrs):
        if tag.casefold() != "input":
            return
        attributes = dict(attrs)
        if attributes.get("id") == "pobCode":
            self.value = attributes.get("value", "") or ""


def _is_poe2db_https_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except (AttributeError, ValueError):
        return False
    return (
        parts.scheme == "https"
        and parts.hostname == _POE2DB_HOST
        and port in (None, 443)
        and parts.username is None
        and parts.password is None
    )


def _poe2db_detail_url(location: str) -> str:
    """Resolve Poe2DB's API location while keeping navigation on its CN pages."""
    raw_location = location.strip()
    if (
        not raw_location
        or "\\" in raw_location
        or any(ord(char) < 32 for char in raw_location)
    ):
        raise WeGameBuildError("Poe2DB 分享接口返回了无效的构筑页面地址。")

    detail_url = urljoin(POE2DB_ACCOUNT_URL, raw_location)
    parts = urlsplit(detail_url)
    decoded_segments = unquote(parts.path).split("/")
    if (
        not _is_poe2db_https_url(detail_url)
        or not parts.path.startswith("/cn/")
        or parts.fragment
        or any(segment in {".", ".."} for segment in decoded_segments)
    ):
        raise WeGameBuildError("Poe2DB 分享接口返回了站点范围以外的页面地址。")
    return detail_url


class _Poe2DBRedirectHandler(HTTPRedirectHandler):
    """Follow Poe2DB redirects only when they remain on its HTTPS origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_poe2db_https_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class WeGameBuildCodeResolver:
    """Read a WeGame share code from Poe2DB and return its PoB2 import code."""

    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = max(5, min(int(timeout_seconds), 30))

    async def convert(self, share_url: str) -> str:
        try:
            parsed = classify_analysis_input(share_url)
        except ValueError as exc:
            raise WeGameBuildError(str(exc)) from exc
        if parsed.kind != "wegame":
            raise WeGameBuildError("请提供有效的 WeGame 分享链接。")

        share_code = wegame_share_code_from_url(parsed.value)
        try:
            return await asyncio.to_thread(self._resolve_sync, share_code)
        except WeGameBuildError:
            raise
        except HTTPError as exc:
            raise WeGameBuildError(f"Poe2DB 返回 HTTP {exc.code}，暂时无法读取构筑。") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise WeGameBuildError("无法连接 Poe2DB 分享接口或读取构筑页面，请稍后重试。") from exc
        except Exception as exc:
            LOGGER.warning(
                "WeGame share resolution through Poe2DB failed (%s)",
                type(exc).__name__,
            )
            raise WeGameBuildError("Poe2DB 没有返回可用的 PoB2 构筑数据。") from exc

    def _resolve_sync(self, share_code: str) -> str:
        opener = build_opener(_Poe2DBRedirectHandler())
        api_url = f"{POE2DB_SHARE_API_URL}?{urlencode({'share_code': share_code})}"
        api_request = Request(
            api_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "AstrBot-PoE2DB/1.21.5 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)",
            },
        )
        api_body = self._read_response(
            opener,
            api_request,
            max_bytes=_MAX_API_RESPONSE_BYTES,
        )
        try:
            payload = json.loads(api_body.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WeGameBuildError("Poe2DB 分享接口返回了无法识别的数据。") from exc
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise WeGameBuildError("Poe2DB 没有找到这条 WeGame 分享构筑，请确认链接仍有效。")
        location = payload.get("location")
        if not isinstance(location, str):
            raise WeGameBuildError("Poe2DB 分享接口没有返回构筑页面地址。")

        detail_url = _poe2db_detail_url(location)
        page_request = Request(
            detail_url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": "AstrBot-PoE2DB/1.21.5 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)",
            },
        )
        page_body = self._read_response(
            opener,
            page_request,
            max_bytes=_MAX_BUILD_PAGE_BYTES,
        )
        parser = _PoBCodeParser()
        parser.feed(page_body.decode("utf-8", errors="replace"))
        parser.close()
        code = normalize_visible_pob_code(parser.value)
        if not code:
            raise WeGameBuildError(
                "Poe2DB 中文构筑页没有包含有效的 PoB2 导入码，分享数据可能不完整。"
            )
        return code

    def _read_response(self, opener, request: Request, max_bytes: int) -> bytes:
        with opener.open(request, timeout=self.timeout_seconds) as response:
            if not _is_poe2db_https_url(response.geturl()):
                raise WeGameBuildError("Poe2DB 请求跳转到了不受支持的站点。")
            status = getattr(response, "status", 200)
            if status != 200:
                raise WeGameBuildError(f"Poe2DB 返回 HTTP {status}。")
            body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise WeGameBuildError("Poe2DB 返回的构筑数据过大，已停止读取。")
        return body
