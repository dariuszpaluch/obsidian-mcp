"""Fork: fork tools are registered only when their own flag is set."""
from __future__ import annotations

import importlib

import pytest

import obsidian_mcp.config as cfg_mod
import obsidian_mcp.server as server_mod

_FORK_TOOLS = {
    "ENABLE_LINK_SAFE_MOVE": {"move_tool"},
    "ENABLE_ARCHIVE": {"archive_tool"},
    "ENABLE_CHUNKED_UPLOAD": {"upload_attachment_chunk_tool", "finish_attachment_upload_tool"},
}


async def _tool_names(monkeypatch, enabled: set[str]) -> set[str]:
    for flag in _FORK_TOOLS:
        monkeypatch.setenv(flag, "true" if flag in enabled else "false")
    cfg_mod._config = None
    server = importlib.reload(server_mod)
    return {tool.name for tool in await server.mcp.list_tools()}


@pytest.mark.asyncio
async def test_fork_tools_hidden_by_default(monkeypatch):
    names = await _tool_names(monkeypatch, set())
    for tools in _FORK_TOOLS.values():
        assert names.isdisjoint(tools)


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", sorted(_FORK_TOOLS))
async def test_each_flag_registers_only_its_tools(monkeypatch, flag):
    names = await _tool_names(monkeypatch, {flag})
    assert _FORK_TOOLS[flag] <= names
    for other, tools in _FORK_TOOLS.items():
        if other != flag:
            assert names.isdisjoint(tools)
