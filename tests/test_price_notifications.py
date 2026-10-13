"""Tests for price notification recipient selection."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.price_notifications import (
    group_umo_parts,
    parse_notification_targets,
    ready_price_requests,
    select_notification_targets,
)


class PriceNotificationTargetTests(unittest.TestCase):
    def test_pending_price_requests_become_ready_per_market(self):
        pending = {
            ("qqbot:GroupMessage:100", "international"),
            ("qqbot:GroupMessage:100", "national"),
            ("qqbot:FriendMessage:200", "international"),
        }
        self.assertEqual(
            ready_price_requests(pending, {"international"}),
            [
                ("qqbot:FriendMessage:200", "international"),
                ("qqbot:GroupMessage:100", "international"),
            ],
        )

    def test_targets_accept_newlines_commas_and_deduplicate(self):
        self.assertEqual(
            parse_notification_targets("123, 456\n123"),
            ("123", "456"),
        )

    def test_empty_whitelist_sends_nothing_but_empty_blacklist_keeps_known_groups(self):
        known = {"qqbot:GroupMessage:100"}
        self.assertEqual(select_notification_targets("whitelist", "", known), [])
        self.assertEqual(
            select_notification_targets("blacklist", "", known),
            ["qqbot:GroupMessage:100"],
        )

    def test_whitelist_selects_full_and_observed_numeric_group_targets(self):
        selected = select_notification_targets(
            "whitelist",
            "qqbot:group:100\n200",
            {"qqbot:group:200", "qqbot:private:300"},
        )
        self.assertEqual(
            selected,
            ["qqbot:GroupMessage:100", "qqbot:GroupMessage:200"],
        )

    def test_bare_group_id_resolves_only_with_one_platform_when_unseen(self):
        self.assertEqual(
            select_notification_targets("whitelist", "123", set(), ["qqbot"]),
            ["qqbot:GroupMessage:123"],
        )
        self.assertEqual(
            select_notification_targets(
                "whitelist", "123", set(), ["qqbot", "telegram"]
            ),
            [],
        )

    def test_bare_group_id_does_not_choose_between_multiple_observed_sessions(self):
        self.assertEqual(
            select_notification_targets(
                "whitelist",
                "123",
                {"qqbot-a:GroupMessage:123", "qqbot-b:GroupMessage:123"},
                ["qqbot-a", "qqbot-b"],
            ),
            [],
        )

    def test_blacklist_only_sends_to_known_groups_and_excludes_matching_ids(self):
        self.assertEqual(
            select_notification_targets(
                "blacklist",
                "qqbot:group:200\n300",
                {
                    "qqbot:group:100",
                    "qqbot:group:200",
                    "telegram:GroupMessage:300",
                    "qqbot:private:400",
                },
            ),
            ["qqbot:GroupMessage:100"],
        )

    def test_private_sessions_are_not_accepted_as_notification_targets(self):
        self.assertIsNone(group_umo_parts("qqbot:private:123"))
        self.assertEqual(
            select_notification_targets(
                "whitelist", "qqbot:private:123", set(), ["qqbot"]
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
