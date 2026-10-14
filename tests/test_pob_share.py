"""Tests for generating Poe2DB Chinese PoB share links."""

import asyncio
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.pob_share import (
    POE2DB_POB_URL_PREFIX,
    PobShareError,
    Poe2DBPobClient,
    format_character_build_links,
    _share_id_from_response,
    character_identity_from_profile_url,
    compact_import_pob_result,
    extract_pob_code,
    fetch_pob_code_from_tool,
    import_pob_from_tool,
)


def sample_pob_code() -> str:
    payload = b"PathOfBuilding PoE2 public build data " * 8
    return base64.b64encode(payload).decode("ascii")


class Poe2DBPobShareTests(unittest.TestCase):
    def test_formats_profile_link_followed_by_chinese_build_share_link(self):
        text = format_character_build_links(
            "https://poe.ninja/poe2/builds/league/character/account/name",
            "https://poe2db.tw/cn/pob/AbCdEf123456",
        )

        self.assertEqual(
            text,
            "角色详情：https://poe.ninja/poe2/builds/league/character/account/name\n"
            "Poe2DB 简体中文 Build：\nhttps://poe2db.tw/cn/pob/AbCdEf123456",
        )

    def test_manual_import_link_is_given_when_share_page_is_unavailable(self):
        text = format_character_build_links(
            "https://poe.ninja/poe2/builds/league/character/account/name", None
        )

        self.assertIn("Poe2DB 简体中文 Build：", text)
        self.assertIn("https://poe2db.tw/cn/pob", text)

    def test_formats_wegame_source_link_without_calling_it_a_character_detail(self):
        text = format_character_build_links(
            "https://www.wegame.com.cn/helper/poe2/#/share/abc123456789012345",
            "https://poe2db.tw/cn/pob/AbCdEf123456",
            "WeGame 构筑来源",
        )

        self.assertIn("WeGame 构筑来源：", text)
        self.assertNotIn("角色详情：", text)
        self.assertIn("Poe2DB 简体中文 Build：", text)

    def test_extracts_account_and_character_from_detail_path(self):
        account, character = character_identity_from_profile_url(
            "https://poe.ninja/poe2/builds/forbiddenrites/character/"
            "heygyus-0416/ResurrectForbidden?i=0&search=name%3DResurrectForbidden"
        )

        self.assertEqual((account, character), ("heygyus-0416", "ResurrectForbidden"))

    def test_rejects_non_poe_ninja_or_incomplete_profile_urls(self):
        with self.assertRaises(PobShareError):
            character_identity_from_profile_url(
                "https://example.com/poe2/builds/league/character/account/name"
            )
        with self.assertRaises(PobShareError):
            character_identity_from_profile_url(
                "https://poe.ninja/poe2/builds/league/character/account"
            )

    def test_extracts_code_from_get_pob_code_tool_result(self):
        code = sample_pob_code()
        result = SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text=f"Path of Building Code for Example:\n\n{code}\n\nCopy this code and import it.",
                )
            ]
        )

        self.assertEqual(extract_pob_code(result), code)

    def test_reports_tool_response_without_a_valid_code(self):
        with self.assertRaises(PobShareError):
            extract_pob_code("PoB code unavailable for this character")

    def test_invokes_permission_guarded_tool_with_identity(self):
        code = sample_pob_code()
        event = object()

        class FakeTool:
            async def call(self, runtime_context, **kwargs):
                self.runtime_context = runtime_context
                self.kwargs = kwargs
                return f"Path of Building Code:\n\n{code}"

        tool = FakeTool()
        actual = asyncio.run(
            fetch_pob_code_from_tool(tool, event, "account-1234", "Character", 45)
        )

        self.assertEqual(actual, code)
        self.assertIs(tool.runtime_context.context.event, event)
        self.assertEqual(tool.runtime_context.tool_call_timeout, 45)
        self.assertEqual(
            tool.kwargs, {"account": "account-1234", "character": "Character"}
        )

    def test_imports_valid_code_directly_without_a_model_round_trip(self):
        code = sample_pob_code()
        event = object()

        class FakeTool:
            async def call(self, runtime_context, **kwargs):
                self.runtime_context = runtime_context
                self.kwargs = kwargs
                return SimpleNamespace(
                    isError=False,
                    content=[SimpleNamespace(type="text", text="Imported parsed build data")],
                )

        tool = FakeTool()
        result = asyncio.run(import_pob_from_tool(tool, event, f"\n{code}\n", 45))

        self.assertEqual(result, "Imported parsed build data")
        self.assertIs(tool.runtime_context.context.event, event)
        self.assertEqual(tool.runtime_context.tool_call_timeout, 45)
        self.assertEqual(tool.kwargs, {"pob_code": code})

    def test_reports_direct_mcp_import_errors(self):
        code = sample_pob_code()

        class FakeTool:
            async def call(self, _runtime_context, **_kwargs):
                return SimpleNamespace(
                    isError=True,
                    content=[SimpleNamespace(type="text", text="Base64 decode failed")],
                )

        with self.assertRaisesRegex(PobShareError, "Base64 decode failed"):
            asyncio.run(import_pob_from_tool(FakeTool(), object(), code))

    def test_compacts_duplicate_item_text_and_keeps_parsed_build_sections(self):
        source = (
            'Successfully imported build:\n'
            '{"items":[{"name":"Example","mods":["+10 Life"],'
            '"raw_text":"duplicate","full_text":"duplicate"}],'
            '"skills":[{"name":"Fireball"}],"tree":{"nodes":[1,2]}}'
        )

        compacted = compact_import_pob_result(source)

        self.assertNotIn("raw_text", compacted)
        self.assertNotIn("full_text", compacted)
        self.assertIn('"mods":["+10 Life"]', compacted)
        self.assertIn('"skills":[{"name":"Fireball"}]', compacted)
        self.assertIn('"tree":{"nodes":[1,2]}', compacted)

    def test_rejects_invalid_code_before_calling_mcp(self):
        class FakeTool:
            async def call(self, *_args, **_kwargs):
                raise AssertionError("invalid PoB code should not be sent to MCP")

        with self.assertRaisesRegex(PobShareError, "导入码格式无效"):
            asyncio.run(import_pob_from_tool(FakeTool(), object(), "not-base64"))

    def test_accepts_documented_raw_id_json_and_share_url_responses(self):
        self.assertEqual(_share_id_from_response("WbdBLfSqPxVN\n"), "WbdBLfSqPxVN")
        self.assertEqual(
            _share_id_from_response(json.dumps({"id": "AbCdEf123456"})),
            "AbCdEf123456",
        )
        self.assertEqual(
            _share_id_from_response("https://poe2db.tw/pob/AbCdEf123456"),
            "AbCdEf123456",
        )
        with self.assertRaises(PobShareError):
            _share_id_from_response("https://example.com/pob/AbCdEf123456")

    def test_posts_once_and_reuses_cached_chinese_share_page(self):
        code = sample_pob_code()

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self, _limit):
                return b"AbCdEf123456"

        with tempfile.TemporaryDirectory() as data_dir:
            client = Poe2DBPobClient(10, data_dir)
            with patch("astrbot_plugin_poe2db.pob_share.urlopen", return_value=FakeResponse()) as post:
                first = asyncio.run(client.create_share_url(code))
                second = asyncio.run(client.create_share_url(code))

            self.assertEqual(first, POE2DB_POB_URL_PREFIX + "AbCdEf123456")
            self.assertEqual(second, first)
            self.assertEqual(post.call_count, 1)
            request = post.call_args.args[0]
            self.assertEqual(request.data, code.encode("ascii"))
            self.assertEqual(request.full_url, "https://poe2db.tw/pob/api/gen")

            restored = Poe2DBPobClient(10, data_dir)
            self.assertEqual(asyncio.run(restored.create_share_url(code)), first)


if __name__ == "__main__":
    unittest.main()
