"""Tests for direct search result ordering, boundaries, and reply formatting."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.poe2db import Source
from astrbot_plugin_poe2db.poe2search import (
    find_ambiguous_groups,
    format_disambiguation_prompt,
    format_search_results,
    merge_search_sources,
    rank_source_groups,
    wiki_search_url,
)


class Poe2SearchTests(unittest.TestCase):
    def test_ambiguity_is_detected_within_one_topic_not_across_topics(self):
        grouped = [
            (
                "Fireball",
                [
                    Source("Lightning Arrow", "https://poe2db.tw/cn/Lightning_Arrow", ""),
                    Source("Fireball Support", "https://poe2db.tw/cn/Fireball_Support", ""),
                    Source("Fireball", "https://poe2db.tw/cn/Fireball", ""),
                ],
            ),
            (
                "碎骨",
                [Source("碎骨", "https://poe2db.tw/cn/Boneshatter", "")],
            ),
            (
                "冰霜新星",
                [Source("冰霜新星", "https://poe2db.tw/cn/Ice_Nova", "")],
            ),
        ]

        ambiguities = find_ambiguous_groups(rank_source_groups(grouped))

        self.assertEqual(len(ambiguities), 1)
        self.assertEqual(ambiguities[0][0], 0)
        self.assertEqual(
            [source.title for source in ambiguities[0][2]],
            ["Fireball", "Fireball Support"],
        )

    def test_disambiguation_prompt_formats_choice_numbers_safely(self):
        prompt = format_disambiguation_prompt(
            "火球",
            [
                Source("火球\n2. 伪造选项", "https://poe2db.tw/cn/Fireball", ""),
                Source("火球辅助", "https://poe2db.tw/cn/Fireball_Support", ""),
            ],
        )

        self.assertIn("1. 火球 2. 伪造选项", prompt)
        self.assertIn("2. 火球辅助", prompt)
        self.assertIn("回复“取消”", prompt)

    def test_search_prioritizes_closer_titles_and_includes_both_sources(self):
        poe_sources = [
            Source("魔血腰带", "https://poe2db.tw/cn/Mageblood", "词条摘要"),
            Source("魔血", "https://poe2db.tw/tw/Mageblood", "繁体词条"),
        ]
        wiki_sources = [
            Source("PoE2Wiki：Mageblood", "https://www.poe2wiki.net/wiki/Mageblood", "Wiki 摘要")
        ]

        results = merge_search_sources("魔血", poe_sources, wiki_sources, 3)

        self.assertEqual(
            [source.url for source in results],
            [
                "https://poe2db.tw/tw/Mageblood",
                "https://www.poe2wiki.net/wiki/Mageblood",
                "https://poe2db.tw/cn/Mageblood",
            ],
        )

    def test_search_rejects_urls_outside_the_configured_sources(self):
        sources = [
            Source("伪造词条", "https://example.com/item", "不应展示"),
            Source("有效词条", "https://poe2db.tw/cn/Boneshatter", "有效摘要"),
        ]

        results = merge_search_sources("词条", sources, [], 5)

        self.assertEqual([source.title for source in results], ["有效词条"])

    def test_search_reply_includes_titles_summaries_and_links(self):
        reply = format_search_results(
            "碎骨",
            [Source("碎骨", "https://poe2db.tw/cn/Boneshatter", "造成沉重眩晕。")],
            5,
        )

        self.assertIn("“碎骨”的词条搜索结果", reply)
        self.assertIn("造成沉重眩晕", reply)
        self.assertIn("https://poe2db.tw/cn/Boneshatter", reply)

    def test_no_match_reply_points_to_both_sites(self):
        reply = format_search_results("冰霜新星", [], 5)

        self.assertIn("https://poe2db.tw/cn/search?", reply)
        self.assertIn("https://poe2db.tw/tw/search?", reply)
        self.assertIn(wiki_search_url("冰霜新星"), reply)


if __name__ == "__main__":
    unittest.main()
