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


if __name__ == "__main__":
    unittest.main()
