"""Tests for MCP client integration."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_strix.mcp_client import (
    MCPConnection,
    MCPManager,
    MCPServerConfig,
    _bridge_mcp_tool,
    _build_args_schema,
    parse_mcp_server_configs,
)


# ---------- MCPServerConfig ----------


class TestMCPServerConfig:
    def test_from_dict_basic(self):
        cfg = MCPServerConfig.from_dict({
            "name": "test-server",
            "command": "npx",
            "args": ["-y", "@anthropic/mcp-server-test"],
        })
        assert cfg.name == "test-server"
        assert cfg.command == "npx"
        assert cfg.args == ["-y", "@anthropic/mcp-server-test"]
        assert cfg.env is None

    def test_from_dict_with_env(self):
        cfg = MCPServerConfig.from_dict({
            "name": "test",
            "command": "python",
            "args": ["server.py"],
            "env": {"FOO": "bar", "BAZ": "qux"},
        })
        assert cfg.env == {"FOO": "bar", "BAZ": "qux"}

    def test_from_dict_env_expansion(self):
        with patch.dict(os.environ, {"MY_KEY": "secret123"}):
            cfg = MCPServerConfig.from_dict({
                "name": "test",
                "command": "npx",
                "args": [],
                "env": {"API_KEY": "${MY_KEY}"},
            })
        assert cfg.env == {"API_KEY": "secret123"}

    def test_from_dict_env_expansion_missing(self):
        cfg = MCPServerConfig.from_dict({
            "name": "test",
            "command": "npx",
            "args": [],
            "env": {"API_KEY": "${NONEXISTENT_VAR_12345}"},
        })
        assert cfg.env == {"API_KEY": ""}

    def test_from_dict_missing_name(self):
        with pytest.raises(ValueError, match="requires a 'name'"):
            MCPServerConfig.from_dict({"command": "npx"})

    def test_from_dict_missing_command(self):
        with pytest.raises(ValueError, match="requires a 'command'"):
            MCPServerConfig.from_dict({"name": "test"})

    def test_from_dict_empty_args(self):
        cfg = MCPServerConfig.from_dict({
            "name": "test",
            "command": "npx",
        })
        assert cfg.args == []

    def test_from_dict_args_coerced_to_str(self):
        cfg = MCPServerConfig.from_dict({
            "name": "test",
            "command": "npx",
            "args": [123, True],
        })
        assert cfg.args == ["123", "True"]


# ---------- parse_mcp_server_configs ----------


class TestParseMCPServerConfigs:
    def test_none(self):
        assert parse_mcp_server_configs(None) == []

    def test_empty_list(self):
        assert parse_mcp_server_configs([]) == []

    def test_not_a_list(self):
        assert parse_mcp_server_configs("bad") == []
        assert parse_mcp_server_configs(42) == []

    def test_valid_configs(self):
        raw = [
            {"name": "a", "command": "echo", "args": ["hello"]},
            {"name": "b", "command": "node", "args": ["server.js"]},
        ]
        configs = parse_mcp_server_configs(raw)
        assert len(configs) == 2
        assert configs[0].name == "a"
        assert configs[1].name == "b"

    def test_skips_invalid(self, capsys):
        raw = [
            {"name": "good", "command": "echo"},
            {"command": "missing-name"},  # invalid
            {"name": "also-good", "command": "node"},
        ]
        configs = parse_mcp_server_configs(raw)
        assert len(configs) == 2
        assert configs[0].name == "good"
        assert configs[1].name == "also-good"

    def test_skips_non_dict(self):
        raw = [
            {"name": "good", "command": "echo"},
            "not-a-dict",
            42,
        ]
        configs = parse_mcp_server_configs(raw)
        assert len(configs) == 1


# ---------- _bridge_mcp_tool ----------


class TestBridgeMCPTool:
    def test_basic_tool(self):
        session = MagicMock()
        tool = _bridge_mcp_tool(
            server_name="brave",
            tool_name="search",
            description="Search the web",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                },
                "required": ["query"],
            },
            session=session,
        )
        assert tool.name == "mcp_brave_search"
        assert "Search the web" in tool.description
        assert "query" in tool.description

    def test_empty_schema(self):
        session = MagicMock()
        tool = _bridge_mcp_tool(
            server_name="test",
            tool_name="ping",
            description="Ping the server",
            input_schema={"type": "object"},
            session=session,
        )
        assert tool.name == "mcp_test_ping"
        assert tool.description == "Ping the server"


# ---------- _build_args_schema ----------


class TestBuildArgsSchema:
    def test_string_and_int_fields(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results"},
            },
            "required": ["query"],
        }
        model = _build_args_schema("search", schema)
        assert model.__name__ == "SearchInput"
        fields = model.model_fields
        assert "query" in fields
        assert "limit" in fields
        assert fields["query"].is_required()
        assert not fields["limit"].is_required()

    def test_empty_properties(self):
        model = _build_args_schema("ping", {"type": "object"})
        assert model.__name__ == "PingInput"
        assert len(model.model_fields) == 0

    def test_array_and_object_types(self):
        schema = {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags"},
                "metadata": {"type": "object", "description": "Extra data"},
            },
            "required": ["tags"],
        }
        model = _build_args_schema("create_item", schema)
        assert model.__name__ == "CreateItemInput"
        assert "tags" in model.model_fields
        assert "metadata" in model.model_fields

    def test_boolean_and_number_types(self):
        schema = {
            "type": "object",
            "properties": {
                "verbose": {"type": "boolean", "description": "Verbose mode"},
                "threshold": {"type": "number", "description": "Score threshold"},
            },
            "required": [],
        }
        model = _build_args_schema("analyze", schema)
        assert not model.model_fields["verbose"].is_required()
        assert not model.model_fields["threshold"].is_required()

    def test_hyphenated_tool_name(self):
        model = _build_args_schema("web-search", {"type": "object", "properties": {}})
        assert model.__name__ == "WebSearchInput"


class TestBridgeMCPToolArgsSchema:
    def test_tool_has_args_schema(self):
        session = MagicMock()
        tool = _bridge_mcp_tool(
            server_name="test",
            tool_name="search",
            description="Search",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Query"},
                    "count": {"type": "integer", "description": "Count"},
                },
                "required": ["query"],
            },
            session=session,
        )
        assert tool.args_schema is not None
        fields = tool.args_schema.model_fields
        assert "query" in fields
        assert "count" in fields
        assert fields["query"].is_required()

    @pytest.mark.asyncio
    async def test_typed_params_passed_directly(self):
        """Verify that typed parameters are passed as direct kwargs, not wrapped."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.isError = False
        mock_content = MagicMock()
        mock_content.text = "result"
        mock_result.content = [mock_content]
        session.call_tool = AsyncMock(return_value=mock_result)

        tool = _bridge_mcp_tool(
            server_name="test",
            tool_name="search",
            description="Search",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Query"},
                },
                "required": ["query"],
            },
            session=session,
        )
        result = await tool.ainvoke({"query": "hello"})
        assert result == "result"
        # The critical assertion: call_tool should receive {"query": "hello"}
        # directly, NOT {"kwargs": {"query": "hello"}}
        session.call_tool.assert_called_once_with("search", {"query": "hello"})


# ---------- MCPConnection ----------


class TestMCPConnection:
    @pytest.mark.asyncio
    async def test_discover_tools(self):
        mock_session = AsyncMock()
        mock_tool = MagicMock()
        mock_tool.name = "search"
        mock_tool.description = "Search things"
        mock_tool.inputSchema = {
            "type": "object",
            "properties": {"q": {"type": "string"}},
        }

        mock_result = MagicMock()
        mock_result.tools = [mock_tool]
        mock_session.list_tools = AsyncMock(return_value=mock_result)

        config = MCPServerConfig(name="test", command="echo", args=[])
        conn = MCPConnection(config=config, session=mock_session)
        tools = await conn.discover_tools()

        assert len(tools) == 1
        assert tools[0].name == "mcp_test_search"
        assert conn.tool_names == ["search"]


# ---------- MCPManager ----------


class TestMCPManager:
    @pytest.mark.asyncio
    async def test_start_servers_empty(self):
        manager = MCPManager()
        tools = await manager.start_servers([])
        assert tools == []
        assert manager.connections == []
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_clears_connections(self):
        manager = MCPManager()
        # Simulate having a connection.
        mock_conn = MagicMock()
        manager.connections.append(mock_conn)
        await manager.shutdown()
        assert manager.connections == []

    @pytest.mark.asyncio
    async def test_failed_server_skipped(self):
        manager = MCPManager()
        log_calls: list[tuple[str, dict[str, Any]]] = []

        def fake_log(event_type: str, **kwargs: Any) -> None:
            log_calls.append((event_type, kwargs))

        # This will fail because "nonexistent-binary-xyz" doesn't exist.
        configs = [
            MCPServerConfig(
                name="bad-server",
                command="nonexistent-binary-xyz",
                args=[],
            ),
        ]
        tools = await manager.start_servers(configs, log_fn=fake_log)
        assert tools == []
        assert manager.connections == []
        # Should have logged the failure.
        assert any(et == "mcp_server_failed" for et, _ in log_calls)
        await manager.shutdown()


# ---------- Config integration ----------


class TestConfigIntegration:
    def test_appconfig_has_mcp_servers(self):
        from open_strix.config import AppConfig
        config = AppConfig()
        assert config.mcp_servers == []

    def test_load_config_with_mcp_servers(self, tmp_path):
        from open_strix.config import RepoLayout, bootstrap_home_repo, load_config

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        bootstrap_home_repo(layout, checkpoint_text="test")

        # Add mcp_servers to config.
        config_data = {
            "model": "test-model",
            "mcp_servers": [
                {"name": "test", "command": "echo", "args": ["hello"]},
            ],
        }
        layout.config_file.write_text(
            __import__("yaml").safe_dump(config_data, sort_keys=False),
            encoding="utf-8",
        )
        config = load_config(layout)
        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0].name == "test"

    def test_load_config_without_mcp_servers(self, tmp_path):
        from open_strix.config import RepoLayout, bootstrap_home_repo, load_config

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        bootstrap_home_repo(layout, checkpoint_text="test")
        config = load_config(layout)
        assert config.mcp_servers == []
