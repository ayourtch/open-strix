"""MCP client — runs MCP servers as subprocesses and bridges their tools into LangChain."""

from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import StructuredTool, ToolException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field, create_model


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    command: str
    args: list[str]
    env: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MCPServerConfig:
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("MCP server config requires a 'name' field")
        command = str(data.get("command", "")).strip()
        if not command:
            raise ValueError(f"MCP server '{name}' requires a 'command' field")
        raw_args = data.get("args", [])
        args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []
        raw_env = data.get("env")
        env: dict[str, str] | None = None
        if isinstance(raw_env, dict):
            env = {}
            for k, v in raw_env.items():
                val = str(v)
                # Expand ${VAR} references from the process environment.
                if val.startswith("${") and val.endswith("}"):
                    var_name = val[2:-1]
                    val = os.environ.get(var_name, "")
                env[str(k)] = val
        return cls(name=name, command=command, args=args, env=env)


class MCPConnection:
    """A live connection to one MCP server subprocess."""

    def __init__(self, config: MCPServerConfig, session: ClientSession) -> None:
        self.config = config
        self.session = session
        self.tool_names: list[str] = []

    async def discover_tools(self) -> list[StructuredTool]:
        """List tools from the server and wrap each as a LangChain StructuredTool."""
        result = await self.session.list_tools()
        tools: list[StructuredTool] = []
        for mcp_tool in result.tools:
            lc_tool = _bridge_mcp_tool(
                server_name=self.config.name,
                tool_name=mcp_tool.name,
                description=mcp_tool.description or "",
                input_schema=mcp_tool.inputSchema,
                session=self.session,
            )
            tools.append(lc_tool)
            self.tool_names.append(mcp_tool.name)
        return tools


class MCPManager:
    """Manages the lifecycle of all configured MCP server connections."""

    def __init__(self) -> None:
        self._exit_stack = AsyncExitStack()
        self.connections: list[MCPConnection] = []

    async def start_servers(
        self,
        configs: list[MCPServerConfig],
        log_fn: Any | None = None,
    ) -> list[StructuredTool]:
        """Start all configured MCP servers and return their bridged tools."""
        all_tools: list[StructuredTool] = []
        for config in configs:
            try:
                conn = await self._connect(config)
                tools = await conn.discover_tools()
                self.connections.append(conn)
                all_tools.extend(tools)
                if log_fn:
                    log_fn(
                        "mcp_server_connected",
                        server=config.name,
                        tools=[t.name for t in tools],
                    )
            except Exception as exc:
                if log_fn:
                    log_fn(
                        "mcp_server_failed",
                        server=config.name,
                        error=str(exc),
                    )
                # Don't block startup — skip this server and continue.
                print(
                    f"[open-strix] MCP server '{config.name}' failed to start: {exc}",
                    flush=True,
                )
        return all_tools

    async def _connect(self, config: MCPServerConfig) -> MCPConnection:
        server_params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env,
        )
        transport = await self._exit_stack.enter_async_context(
            stdio_client(server_params),
        )
        read_stream, write_stream = transport
        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream),
        )
        await session.initialize()
        return MCPConnection(config=config, session=session)

    async def shutdown(self) -> None:
        """Gracefully close all MCP server connections."""
        await self._exit_stack.aclose()
        self.connections.clear()


_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _build_args_schema(
    tool_name: str,
    input_schema: dict[str, Any],
) -> type[BaseModel]:
    """Generate a Pydantic model from an MCP tool's JSON Schema inputSchema."""
    properties = input_schema.get("properties", {})
    required_fields = set(input_schema.get("required", []))

    field_definitions: dict[str, Any] = {}
    for prop_name, prop_info in properties.items():
        json_type = prop_info.get("type", "string")
        prop_desc = prop_info.get("description", "")
        py_type = _JSON_TYPE_MAP.get(json_type, Any)

        if json_type == "array":
            items_type = _JSON_TYPE_MAP.get(
                prop_info.get("items", {}).get("type", "string"), Any
            )
            py_type = list[items_type]  # type: ignore[valid-type]
        elif json_type == "object":
            py_type = dict[str, Any]

        if prop_name in required_fields:
            field_definitions[prop_name] = (
                py_type,
                Field(description=prop_desc),
            )
        else:
            field_definitions[prop_name] = (
                py_type | None,
                Field(default=None, description=prop_desc),
            )

    # Create the model dynamically via pydantic's create_model().  If there
    # are no properties the tool takes no arguments — return an empty model
    # so LangChain still gets a concrete schema instead of inferring **kwargs.
    model_name = "".join(
        part.capitalize() for part in tool_name.replace("-", "_").split("_")
    ) + "Input"
    return create_model(model_name, **field_definitions)  # type: ignore[call-overload]


def _bridge_mcp_tool(
    *,
    server_name: str,
    tool_name: str,
    description: str,
    input_schema: dict[str, Any],
    session: ClientSession,
) -> StructuredTool:
    """Wrap a single MCP tool as a LangChain StructuredTool."""
    properties = input_schema.get("properties", {})
    required_fields = set(input_schema.get("required", []))

    # Build a simple schema description for the tool.
    schema_desc_parts: list[str] = []
    for prop_name, prop_info in properties.items():
        prop_type = prop_info.get("type", "string")
        prop_desc = prop_info.get("description", "")
        req = " (required)" if prop_name in required_fields else ""
        schema_desc_parts.append(f"  {prop_name} ({prop_type}{req}): {prop_desc}")

    if schema_desc_parts:
        full_description = f"{description}\n\nParameters:\n" + "\n".join(schema_desc_parts)
    else:
        full_description = description

    # Namespace the tool name to avoid collisions with built-in tools.
    namespaced_name = f"mcp_{server_name}_{tool_name}"

    # Generate a Pydantic model so LangChain passes parameters by name
    # instead of wrapping them in a kwargs dict.
    args_model = _build_args_schema(tool_name, input_schema)

    async def _call_mcp_tool(**kwargs: Any) -> str:
        try:
            result = await session.call_tool(tool_name, kwargs if kwargs else None)
        except Exception as exc:
            raise ToolException(f"MCP tool '{tool_name}' failed: {exc}") from exc
        if result.isError:
            text_parts = []
            for content in result.content:
                if hasattr(content, "text"):
                    text_parts.append(content.text)
            error_text = "\n".join(text_parts) if text_parts else "Unknown error"
            raise ToolException(f"MCP tool '{tool_name}' returned error: {error_text}")
        # Serialize content to text.
        parts: list[str] = []
        for content in result.content:
            if hasattr(content, "text"):
                parts.append(content.text)
            elif hasattr(content, "data"):
                parts.append(f"[{getattr(content, 'mimeType', 'binary')} data]")
            else:
                parts.append(json.dumps(content.model_dump(), default=str))
        return "\n".join(parts) if parts else "(empty result)"

    lc_tool = StructuredTool.from_function(
        coroutine=_call_mcp_tool,
        name=namespaced_name,
        description=full_description,
        args_schema=args_model,
        handle_tool_error=True,
    )
    return lc_tool


def parse_mcp_server_configs(raw: Any) -> list[MCPServerConfig]:
    """Parse the mcp_servers section from config.yaml."""
    if not isinstance(raw, list):
        return []
    configs: list[MCPServerConfig] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            configs.append(MCPServerConfig.from_dict(item))
        except ValueError as exc:
            print(f"[open-strix] Skipping invalid MCP server config: {exc}", flush=True)
    return configs
