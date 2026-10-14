"""Tests for selecting MCP tools for build analysis."""

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_poe2db.analysis_tools import filter_mcp_tool_set


@dataclass
class FakeToolSet:
    tools: list = field(default_factory=list)

    def add_tool(self, tool):
        self.tools.append(tool)

    def empty(self):
        return not self.tools


@dataclass
class FakeTool:
    name: str
    active: bool = True
    mcp_server_name: str | None = None


@dataclass
class GuardedTool:
    name: str
    _wrapped: FakeTool
    active: bool = True


class FakeToolManager:
    def __init__(self, tools):
        self.tools = FakeToolSet(tools)

    def get_full_tool_set(self):
        return self.tools


class MCPAnalysisToolSelectionTests(unittest.TestCase):
    def test_filters_by_server_and_preserves_permission_guard(self):
        selected = GuardedTool(
            "analyze_character", FakeTool("analyze_character", mcp_server_name="poe2-mcp")
        )
        other_server = GuardedTool(
            "weather", FakeTool("weather", mcp_server_name="weather")
        )
        inactive = GuardedTool(
            "compare", FakeTool("compare", mcp_server_name="poe2-mcp"), active=False
        )

        result = filter_mcp_tool_set(
            FakeToolManager([selected, other_server, inactive]), "POE2-MCP"
        )

        self.assertEqual(result.tools, [selected])
        self.assertIs(result.tools[0], selected)

    def test_returns_empty_set_if_server_is_not_loaded(self):
        result = filter_mcp_tool_set(
            FakeToolManager([FakeTool("weather", mcp_server_name="weather")])
        )

        self.assertTrue(result.empty())


if __name__ == "__main__":
    unittest.main()
