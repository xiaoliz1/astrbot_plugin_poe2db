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


def exclude_tool_set_suffixes(tool_set, suffixes: tuple[str, ...]):
    """Copy a tool set without tools whose names end in any given suffix."""
    excluded = tuple(suffix.casefold() for suffix in suffixes)
    filtered = type(tool_set)()
    for tool in getattr(tool_set, "tools", []):
        name = getattr(tool, "name", "")
        if isinstance(name, str) and any(name.casefold().endswith(suffix) for suffix in excluded):
            continue
        filtered.add_tool(tool)
    return filtered
