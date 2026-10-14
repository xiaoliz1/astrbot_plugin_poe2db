"""Checks for safe, source-bound build card summaries."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.build_card import (
    build_card_from_analysis,
    render_build_card_html,
)


class BuildCardTests(unittest.TestCase):
    def test_extracts_chinese_analysis_sections_and_prefers_chinese_build_link(self):
        analysis = """## 结论
        这是以旋风为主的构筑，已读取到装备和防御数据。
        ## 防御属性
        - 生命：1,839；能量护盾：3,031。[1]
        - 火焰、冰霜、闪电抗性均为 75%。
        ## 主要技能
        - 旋风：主要输出技能。
        - 回旋斩：清图技能。
        ## 装备与词缀
        - 武器：长杖，提供法术等级。
        - 胸甲：提高能量护盾。
        ## 被动技能树
        - 已分配关键天赋。
        """
        card = build_card_from_analysis(
            analysis,
            title="测试角色",
            league="当前赛季",
            source_url="https://poe.ninja/poe2/builds/league/character/account/name",
            chinese_build_url="https://poe2db.tw/cn/pob/AbCdEf123",
        )
        self.assertIsNotNone(card)
        self.assertEqual(card.skills, ("旋风：主要输出技能。", "回旋斩：清图技能。"))
        self.assertEqual(card.defense[0], "生命：1,839；能量护盾：3,031。")
        self.assertEqual(card.gear[0], "武器：长杖，提供法术等级。")
        self.assertEqual(card.build_url, "https://poe2db.tw/cn/pob/AbCdEf123")
        page = render_build_card_html(card)
        self.assertIn("旋风：主要输出技能。", page)
        self.assertIn("https://poe2db.tw/cn/pob/AbCdEf123", page)
        self.assertNotIn("[1]", page)

    def test_missing_fields_are_marked_and_unstructured_failure_has_no_card(self):
        self.assertIsNone(
            build_card_from_analysis(
                "角色构筑数据已读取，但当前模型未能完成中文整理。",
                title="角色",
                league=None,
                source_url="https://poe.ninja/poe2/pob/abc123",
                chinese_build_url=None,
            )
        )
        self.assertIsNone(
            build_card_from_analysis(
                "## 防御属性\n- 未读取到防御数值。\n## 主要技能\n- 无数据。\n## 装备与词缀\n- 未提供装备。",
                title="角色",
                league=None,
                source_url="https://poe.ninja/poe2/pob/abc123",
                chinese_build_url=None,
            )
        )
        card = build_card_from_analysis(
            "**主要技能：** 火球是主要技能。",
            title="火球角色",
            league=None,
            source_url="https://poe.ninja/poe2/pob/abc123",
            chinese_build_url=None,
        )
        self.assertIsNotNone(card)
        page = render_build_card_html(card)
        self.assertIn("火球是主要技能。", page)
        self.assertIn("本次资料未读取到", page)

    def test_escapes_untrusted_text_and_rejects_unrelated_link(self):
        card = build_card_from_analysis(
            "## 主要技能\n- <img src=x onerror=alert(1)> 火球",
            title="<script>角色</script>",
            league="当前赛季",
            source_url="https://example.com/fake",
            chinese_build_url="javascript:alert(1)",
        )
        self.assertIsNotNone(card)
        page = render_build_card_html(card)
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("javascript:alert", page)
        self.assertIn("&lt;script&gt;角色&lt;/script&gt;", page)
        self.assertIn("本次未能生成构筑链接", page)


if __name__ == "__main__":
    unittest.main()
