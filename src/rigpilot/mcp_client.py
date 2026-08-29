"""Minimal stdio MCP client; transport details do not leak into the workflow."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class McpClientError(RuntimeError):
    pass


class McpToolSession:
    def __init__(self, session: ClientSession) -> None:
        self._session = session

    async def require_tools(self, names: set[str]) -> None:
        available = {tool.name for tool in (await self._session.list_tools()).tools}
        missing = names - available
        if missing:
            raise McpClientError(f"必要なMCP Toolが見つかりません: {', '.join(sorted(missing))}")

    async def call_data(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = await self._session.call_tool(name, arguments or {})
        if result.isError:
            raise McpClientError(f"{name} がエラーを返しました")
        for item in result.content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                try:
                    data = json.loads(text)
                except json.JSONDecodeError as error:
                    raise McpClientError(f"{name} のJSON応答を読み取れません") from error
                if isinstance(data, dict) and "Error" in data:
                    raise McpClientError(f"{name}: {data['Error']}")
                return data
        raise McpClientError(f"{name} がJSONを返しませんでした")

    async def call_json(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        data = await self.call_data(name, arguments)
        if not isinstance(data, dict):
            raise McpClientError(f"{name} がJSONオブジェクトを返しませんでした")
        return data

    async def call_image(self, name: str, arguments: dict[str, Any] | None = None) -> None:
        result = await self._session.call_tool(name, arguments or {})
        if result.isError or not any(getattr(item, "data", None) for item in result.content):
            raise McpClientError(f"{name} が画像を返しませんでした")


@asynccontextmanager
async def open_mcp_session(server: StdioServerParameters) -> AsyncIterator[McpToolSession]:
    async with stdio_client(server, errlog=sys.stderr) as streams, ClientSession(*streams) as session:
        await session.initialize()
        yield McpToolSession(session)
