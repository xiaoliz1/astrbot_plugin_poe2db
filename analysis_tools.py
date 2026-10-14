"""Helpers for selecting connected MCP tools in AstrBot."""

from __future__ import annotations


def filter_mcp_tool_set(tool_manager, server_name: str = "poe2-mcp"):
    """Keep only tools owned by one MCP server, preserving permission wrappers."""
    full_tool_set = tool_manager.get_full_tool_set()
    filtered = type(full_tool_set)()
    expected_server = server_name.casefold()

    for tool in getattr(full_tool_set, "tools", []):
        wrapped = getattr(tool, "_wrapped", tool)
        owner = getattr(wrapped, "mcp_server_name", "")
        if not isinstance(owner, str) or owner.casefold() != expected_server:
            continue
        if not getattr(tool, "active", True):
            continue
        filtered.add_tool(tool)

    return filtered
