"""Tests for durable price-image replacement and cleanup."""

import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.price_image_store import PriceImageStore


class PriceImageStoreTests(unittest.TestCase):
    def test_old_image_survives_until_successful_replacement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rendered = root / "rendered.png"
            rendered.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
            store = PriceImageStore(root / "plugin_data")

            first_path = Path(
                store.save_replacement("international", rendered, "first")
            )
            self.assertTrue(first_path.is_file())

            with self.assertRaises(ValueError):
                store.save_replacement(
                    "international", root / "missing.png", "failed"
                )
            self.assertTrue(first_path.is_file())
            self.assertEqual(store.images["international"], str(first_path))

            rendered.write_bytes(b"\x89PNG\r\n\x1a\nsecond")
            second_path = Path(
                store.save_replacement("international", rendered, "second")
            )

            self.assertTrue(second_path.is_file())
            self.assertFalse(first_path.exists())
            self.assertEqual(second_path.read_bytes(), rendered.read_bytes())

    def test_latest_image_and_timestamp_load_after_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rendered = root / "rendered.png"
            rendered.write_bytes(b"\x89PNG\r\n\x1a\nimage")
            store = PriceImageStore(root / "plugin_data")
            saved_path = store.save_replacement("national", rendered, "updated")

            restored = PriceImageStore(root / "plugin_data")

            self.assertEqual(restored.images["national"], saved_path)
            self.assertEqual(restored.updated_at["national"], "updated")

    def test_replacing_one_market_keeps_the_other_market_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            rendered = root / "rendered.png"
            rendered.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
            store = PriceImageStore(root / "plugin_data")
            old_international = Path(
                store.save_replacement("international", rendered, "intl-1")
            )
            national = Path(
                store.save_replacement("national", rendered, "national-1")
            )

            rendered.write_bytes(b"\x89PNG\r\n\x1a\nsecond")
            store.save_replacement("international", rendered, "intl-2")

            self.assertFalse(old_international.exists())
            self.assertTrue(national.is_file())
            self.assertEqual(Path(store.images["national"]), national)

    def test_manifest_cannot_delete_files_outside_plugin_image_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = root / "keep.png"
            outside.write_bytes(b"keep")
            data_dir = root / "plugin_data"
            image_dir = data_dir / "price_images"
            image_dir.mkdir(parents=True)
            (image_dir / "latest.json").write_text(
                '{"images":{"international":{"filename":"../../keep.png"}}}',
                encoding="utf-8",
            )
            rendered = root / "rendered.png"
            rendered.write_bytes(b"\x89PNG\r\n\x1a\nimage")

            store = PriceImageStore(data_dir)
            store.save_replacement("international", rendered, "updated")

            self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
