"""Tests for the plugin's dashboard configuration schema."""

import json
import unittest
from pathlib import Path


class ConfigSchemaTests(unittest.TestCase):
    def test_notification_mode_uses_internal_values_and_chinese_labels(self):
        schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        mode = schema["price_notification_mode"]

        self.assertEqual(mode["options"], ["whitelist", "blacklist"])
        self.assertEqual(mode["labels"], ["白名单", "黑名单"])
        self.assertIn(mode["default"], mode["options"])

    def test_official_update_notifications_are_separately_disabled_by_default(self):
        schema_path = Path(__file__).resolve().parents[1] / "_conf_schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        setting = schema["official_update_notification_enabled"]

        self.assertEqual(setting["type"], "bool")
        self.assertFalse(setting["default"])
        self.assertIn("群名单", setting["hint"])


if __name__ == "__main__":
    unittest.main()
