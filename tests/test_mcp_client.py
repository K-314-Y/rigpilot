import asyncio
import unittest
from types import SimpleNamespace

from rigpilot.mcp_client import McpToolSession


class FakeSession:
    async def call_tool(self, _name: str, _arguments: dict[str, object]) -> object:
        return SimpleNamespace(
            isError=False,
            content=[
                SimpleNamespace(text='{"title": "one"}'),
                SimpleNamespace(text='{"title": "two"}'),
            ],
        )


class McpToolSessionTests(unittest.TestCase):
    def test_call_data_keeps_every_json_content_block(self) -> None:
        result = asyncio.run(McpToolSession(FakeSession()).call_data("list_windows"))
        self.assertEqual(result, [{"title": "one"}, {"title": "two"}])
