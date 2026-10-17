"""Tests for GGG official update parsing and durable notification state."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.official_updates import (
    OfficialUpdate,
    OfficialUpdateStore,
    format_official_update_notice,
    parse_official_update_page,
)


class OfficialUpdateParsingTests(unittest.TestCase):
    def test_announcements_keep_season_or_patch_posts_and_ignore_cosmetics(self):
        html = """
        <div>
          <a href="/forum/view-thread/1001">Announcing Path of Exile 2: New Season</a>
          <a href="/forum/view-thread/1001/page/2">2</a>
          <a href="/forum/view-thread/1002">Introducing a New Armour Effect</a>
          <a href="https://www.pathofexile.com/forum/view-thread/1003">Path of Exile 2: 0.6.0 Patch Notes</a>
          <a href="/forum/view-thread/1004">Watch GGG Live for New Twitch Drops</a>
          <a href="/forum/view-thread/1005">Path of Exile 2 Full Release Registration</a>
          <a href="/forum/view-thread/1006">Path of Exile 2: Forbidden Rites FAQ</a>
          <a href="/forum/view-thread/1007">Path of Exile 2: The Third Edict</a>
          <a href="/forum/view-thread/1008">Path of Exile 2: Introducing a New Armour Effect</a>
        </div>
        """

        updates = parse_official_update_page(html, "announcements")

        self.assertEqual(
            [item.id for item in updates],
            [
                "announcements:1001",
                "announcements:1003",
                "announcements:1006",
                "announcements:1007",
            ],
        )
        self.assertEqual(
            updates[0].url,
            "https://www.pathofexile.com/forum/view-thread/1001",
        )

    def test_patch_notes_category_keeps_patch_posts(self):
        updates = parse_official_update_page(
            """
            <a href="/forum/view-thread/2001">0.6.1 Hotfix 2</a>
            <a href="/forum/view-thread/2002">0.6.1 Patch Notes</a>
            <a href="/forum/view-thread/2003">Server Maintenance</a>
            <a href="/forum/view-thread/2004">Code of Conduct</a>
            """,
            "patch_notes",
        )

        self.assertEqual(
            [item.id for item in updates],
            ["patch_notes:2001", "patch_notes:2002"],
        )
        self.assertEqual(updates[0].category, "patch_notes")

    def test_notice_identifies_announcement_type_and_links_to_source(self):
        message = format_official_update_notice(
            [
                OfficialUpdate(
                    "announcements:1001",
                    "Announcing a New League",
                    "https://www.pathofexile.com/forum/view-thread/1001",
                    "announcements",
                )
            ]
        )

        self.assertIn("【官方公告】Announcing a New League", message)
        self.assertIn("https://www.pathofexile.com/forum/view-thread/1001", message)


class OfficialUpdateStoreTests(unittest.TestCase):
    def test_first_scan_is_baseline_and_new_posts_survive_restart_until_delivered(self):
        original = OfficialUpdate(
            "patch_notes:2001",
            "0.6.0 Patch Notes",
            "https://www.pathofexile.com/forum/view-thread/2001",
            "patch_notes",
        )
        new = OfficialUpdate(
            "announcements:1001",
            "Announcing a New League",
            "https://www.pathofexile.com/forum/view-thread/1001",
            "announcements",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OfficialUpdateStore(temp_dir)
            self.assertEqual(store.observe([original]), [])

            restarted = OfficialUpdateStore(temp_dir)
            self.assertEqual(restarted.observe([new, original]), [new])
            self.assertEqual(restarted.pending_for("qq:GroupMessage:1"), [new])

            restarted.mark_delivered("qq:GroupMessage:1", [new.id])
            self.assertEqual(restarted.pending_for("qq:GroupMessage:1"), [])
            self.assertEqual(restarted.pending_for("qq:GroupMessage:2"), [new])

            restarted.prune_delivered(["qq:GroupMessage:1", "qq:GroupMessage:2"])
            self.assertEqual(
                restarted.pending_updates(),
                [new],
                "the post remains pending until every current destination receives it",
            )

            restarted.mark_delivered("qq:GroupMessage:2", [new.id])
            restarted.prune_delivered(["qq:GroupMessage:1", "qq:GroupMessage:2"])
            restored = OfficialUpdateStore(temp_dir)
            self.assertEqual(restored.pending_updates(), [])
            self.assertEqual(restored.observe([new, original]), [])

    def test_seen_id_limit_does_not_reset_partially_delivered_pending_posts(self):
        baseline = OfficialUpdate(
            "patch_notes:1",
            "0.6.0 Patch Notes",
            "https://www.pathofexile.com/forum/view-thread/1",
            "patch_notes",
        )
        pending = OfficialUpdate(
            "announcements:2",
            "Announcing a New League",
            "https://www.pathofexile.com/forum/view-thread/2",
            "announcements",
        )
        later_posts = [
            OfficialUpdate(
                f"patch_notes:{thread_id}",
                f"0.6.{thread_id} Patch Notes",
                f"https://www.pathofexile.com/forum/view-thread/{thread_id}",
                "patch_notes",
            )
            for thread_id in range(3, 504)
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            store = OfficialUpdateStore(temp_dir)
            store.observe([baseline])
            store.observe([baseline, pending])
            store.mark_delivered("qq:GroupMessage:1", [pending.id])
            store.observe([pending, *later_posts])
            store.observe([pending, *later_posts])

            sent_to_first = {
                update.id for update in store.pending_for("qq:GroupMessage:1")
            }
            pending_for_second = {
                update.id for update in store.pending_for("qq:GroupMessage:2")
            }
            self.assertNotIn(pending.id, sent_to_first)
            self.assertIn(pending.id, pending_for_second)


if __name__ == "__main__":
    unittest.main()
