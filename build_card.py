"""Compact, source-bound Chinese build card for analysis replies."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


_SECTION_ALIASES = {
    "summary": ("构筑结论", "整体结论", "结论"),
    "defense": ("防御属性", "防御概况", "防御"),
    "skills": ("主要技能", "技能与辅助", "技能配置", "主技能"),
    "gear": ("装备与词缀", "装备与珠宝", "关键装备", "装备"),
    "passives": ("被动技能树", "天赋树", "天赋"),
    "comparison": ("与天梯顶尖构筑对比", "天梯对比", "构筑对比"),
    "advice": ("改进建议", "建议"),
    "sources": ("Poe2DB 资料来源", "资料来源", "市集链接"),
}
_ORDINAL_RE = re.compile(r"^(?:[一二三四五六七八九十]+|\d+)[、.．)]\s*")
_CITATION_RE = re.compile(r"\[(?:\d+|来源\d+)\]")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(https?://[^)]+\)")
_BULLET_RE = re.compile(r"^(?:[-*•]\s+|\d+[.)、]\s+)")
_ALLOWED_LINK_HOSTS = {"poe.ninja", "poe2db.tw", "www.wegame.com.cn"}
_MISSING_PREFIXES = ("未读取到", "无法读取", "无法确认", "未提供", "没有读取到", "暂无数据", "无数据")


@dataclass(frozen=True)
class BuildCard:
    title: str
    context: str
    summary: str
    skills: tuple[str, ...]
    gear: tuple[str, ...]
    defense: tuple[str, ...]
    build_url: str


def _heading(line: str) -> tuple[str, str] | None:
    candidate = re.sub(r"^#{1,6}\s*", "", line.strip()).strip(" *_")
    candidate = _ORDINAL_RE.sub("", candidate).strip(" *_")
    for section, aliases in _SECTION_ALIASES.items():
        for alias in aliases:
            if candidate == alias:
                return section, ""
            for separator in ("：", ":"):
                if candidate.startswith(alias + separator):
                    return section, candidate[len(alias) + 1 :].strip(" *_")
    return None


def _clean_line(line: str) -> str:
    line = _BULLET_RE.sub("", line.strip())
    line = _LINK_RE.sub(r"\1", line)
    line = _CITATION_RE.sub("", line)
    line = line.replace("**", "").replace("__", "").replace("`", "")
    line = " ".join(line.split())
    return "".join(character for character in line if ord(character) >= 32)


def _shorten(value: str, limit: int) -> str:
    value = _clean_line(value)
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _section_lines(lines: list[str], count: int, width: int) -> tuple[str, ...]:
    result: list[str] = []
    for line in lines:
        clean = _clean_line(line)
        if not clean or clean.startswith(("http://", "https://") + _MISSING_PREFIXES):
            continue
        for sentence in re.split(r"(?<=。)\s*", clean):
            if sentence.strip():
                result.append(_shorten(sentence, width))
            if len(result) >= count:
                return tuple(result)
    return tuple(result)


def _safe_build_url(primary: str | None, fallback: str) -> str:
    for candidate in (primary, fallback):
        if not candidate:
            continue
        try:
            parsed = urlsplit(candidate)
            if (
                parsed.scheme == "https"
                and parsed.hostname in _ALLOWED_LINK_HOSTS
                and parsed.port in (None, 443)
                and parsed.username is None
                and parsed.password is None
            ):
                return candidate
        except ValueError:
            pass
    return ""


def build_card_from_analysis(
    analysis: str,
    *,
    title: str,
    league: str | None,
    source_url: str,
    chinese_build_url: str | None,
) -> BuildCard | None:
    """Excerpt the existing Chinese analysis; never infer missing build values."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in analysis.splitlines():
        heading = _heading(line)
        if heading:
            current, inline = heading
            sections.setdefault(current, [])
            if inline:
                sections[current].append(inline)
        elif current:
            sections[current].append(line)

    skills = _section_lines(sections.get("skills", []), 2, 76)
    gear = _section_lines(sections.get("gear", []), 3, 76)
    defense = _section_lines(sections.get("defense", []), 3, 76)
    if not any((skills, gear, defense)):
        return None
    conclusion = _section_lines(sections.get("summary", []), 1, 112)
    return BuildCard(
        title=_shorten(title, 54) or "公开构筑",
        context=_shorten(f"当前赛季：{league}" if league else "公开构筑", 48),
        summary=conclusion[0] if conclusion else "以下仅摘录本次分析中已读取到的构筑信息。",
        skills=skills,
        gear=gear,
        defense=defense,
        build_url=_safe_build_url(chinese_build_url, source_url),
    )


def _display_url(url: str) -> str:
    if len(url) <= 88:
        return url
    return url[:62].rstrip("/") + "…" + url[-22:]


def _card_section(number: str, title: str, lines: tuple[str, ...], tone: str) -> str:
    entries = lines or ("本次资料未读取到",)
    rows = "".join(f"<li>{html.escape(line, quote=True)}</li>" for line in entries)
    return (
        f'<section class="detail {tone}"><div class="section-title">'
        f'<span class="number">{number}</span><h2>{title}</h2></div><ul>{rows}</ul></section>'
    )


def render_build_card_html(card: BuildCard) -> str:
    """Return a self-contained portrait card for AstrBot's HTML renderer."""
    title = html.escape(card.title, quote=True)
    context = html.escape(card.context, quote=True)
    summary = html.escape(card.summary, quote=True)
    url = html.escape(_display_url(card.build_url), quote=True)
    link_label = "Poe2DB 简体中文构筑" if "poe2db.tw" in card.build_url else "构筑来源"
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<style>
*{box-sizing:border-box}html,body{margin:0}body{padding:20px;background:#0b121b;
color:#eef5fb;font-family:Arial,"Microsoft YaHei","Noto Sans CJK SC",sans-serif}
.card{width:760px;background:#172633;border:1px solid #314555;border-radius:24px;
overflow:hidden;box-shadow:0 18px 42px #07101a}
.hero{padding:32px 36px 28px;background:linear-gradient(125deg,#1d3849,#142633 67%,#173641)}
.brand{font-size:14px;font-weight:800;letter-spacing:2px;color:#50dec2}
.badge{display:inline-block;margin-top:16px;padding:6px 12px;border-radius:99px;
background:#2d4a56;color:#d6eef2;font-size:16px}
h1{font-size:34px;line-height:1.24;margin:13px 0 0;overflow-wrap:anywhere}
.summary{margin:24px 30px 4px;padding:20px 22px;background:#203746;
border-left:4px solid #44d4bc;border-radius:12px;font-size:19px;line-height:1.5}
.details{padding:12px 30px 12px}.detail{padding:19px 2px;border-bottom:1px solid #304452}
.section-title{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.number{display:grid;place-items:center;width:34px;height:34px;border-radius:9px;
background:#294f58;color:#70ebd4;font-size:14px;font-weight:800}
.gear .number{background:#58452c;color:#f4ce83}.defense .number{background:#2e4964;color:#97caff}
h2{font-size:23px;margin:0}ul{margin:0;padding:0 0 0 46px;list-style:none}
li{font-size:18px;line-height:1.5;margin:7px 0;overflow-wrap:anywhere}
li::before{content:"·";display:inline-block;margin-left:-18px;width:18px;color:#76d9cb}
.gear li::before{color:#e8bd71}.defense li::before{color:#8ec7f5}
.footer{padding:17px 30px 24px;background:#13222e}.link-label{color:#74d7c5;font-size:14px;font-weight:700}
.url{margin-top:6px;color:#d6eaf3;font-size:16px;line-height:1.4;overflow-wrap:anywhere}
.note{margin-top:12px;color:#9fb4c1;font-size:13px}
</style></head><body><main class="card"><header class="hero">
<div class="brand">流放之路 2 · 中文构筑卡</div>
<div class="badge">""" + context + """</div><h1>""" + title + """</h1></header>
<div class="summary">""" + summary + """</div><div class="details">""" + (
        _card_section("01", "主技能", card.skills, "skills")
        + _card_section("02", "核心装备", card.gear, "gear")
        + _card_section("03", "防御", card.defense, "defense")
    ) + """</div><footer class="footer"><div class="link-label">""" + link_label + """</div>
<div class="url">""" + (url or "本次未能生成构筑链接") + """</div>
<div class="note">仅摘录本次已读取的构筑数据 · 完整链接与分析见文字回复</div>
</footer></main></body></html>"""
