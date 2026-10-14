"""Resolve public poe.ninja PoB share links to importable PoB2 codes."""

from __future__ import annotations

import asyncio
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .analysis_input import classify_analysis_input
from .builds import normalize_visible_pob_code

POE_NINJA_POB_RAW_URL = "https://poe.ninja/poe2/pob/raw/{}"
_MAX_POB_CODE_BYTES = 1_000_000


class PoeNinjaPobError(RuntimeError):
    """A public poe.ninja PoB share could not be resolved."""


def _is_poe_ninja_https_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except (AttributeError, ValueError):
        return False
    return (
        parts.scheme == "https"
        and parts.hostname == "poe.ninja"
        and port in (None, 443)
        and parts.username is None
        and parts.password is None
    )


class _PoeNinjaRedirectHandler(HTTPRedirectHandler):
    """Follow redirects only while they remain on poe.ninja HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _is_poe_ninja_https_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PoeNinjaPobResolver:
    """Read the public raw PoB payload used by poe.ninja's share page."""

    def __init__(self, timeout_seconds: int = 15):
        self.timeout_seconds = max(5, min(int(timeout_seconds), 30))

    async def resolve(self, share_url: str) -> str:
        try:
            parsed = classify_analysis_input(share_url)
        except ValueError as exc:
            raise PoeNinjaPobError(str(exc)) from exc
        if parsed.kind != "poe_ninja_pob" or not parsed.pob_id:
            raise PoeNinjaPobError("请提供有效的 poe.ninja PoB 分享链接。")

        try:
            return await asyncio.to_thread(self._fetch_code, parsed.pob_id)
        except PoeNinjaPobError:
            raise
        except HTTPError as exc:
            if exc.code == 404:
                raise PoeNinjaPobError(
                    "poe.ninja 没有找到这条 PoB 分享构筑，链接可能已失效。"
                ) from exc
            raise PoeNinjaPobError(
                f"poe.ninja PoB 导出接口返回 HTTP {exc.code}。"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise PoeNinjaPobError(
                "暂时无法连接 poe.ninja PoB 导出接口，请稍后重试。"
            ) from exc

    def _fetch_code(self, pob_id: str) -> str:
        request = Request(
            POE_NINJA_POB_RAW_URL.format(pob_id),
            headers={
                "Accept": "text/plain,*/*",
                "User-Agent": "AstrBot-PoE2DB/1.22.1 (+https://github.com/xiaoliz1/astrbot_plugin_poe2db)",
            },
        )
        opener = build_opener(_PoeNinjaRedirectHandler())
        with opener.open(request, timeout=self.timeout_seconds) as response:
            if not _is_poe_ninja_https_url(response.geturl()):
                raise PoeNinjaPobError("poe.ninja 请求跳转到了不受支持的站点。")
            status = getattr(response, "status", 200)
            if status != 200:
                raise PoeNinjaPobError(
                    f"poe.ninja PoB 导出接口返回 HTTP {status}。"
                )
            body = response.read(_MAX_POB_CODE_BYTES + 1)
        if len(body) > _MAX_POB_CODE_BYTES:
            raise PoeNinjaPobError("poe.ninja 返回的 PoB2 导入码过大，已停止读取。")
        try:
            raw_code = body.decode("ascii")
        except UnicodeDecodeError as exc:
            raise PoeNinjaPobError("poe.ninja 没有返回有效的 PoB2 导入码。") from exc
        code = normalize_visible_pob_code(raw_code)
        if not code:
            raise PoeNinjaPobError("poe.ninja 没有返回有效的 PoB2 导入码。")
        return code
