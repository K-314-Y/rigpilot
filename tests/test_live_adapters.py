import asyncio
import unittest

from rigpilot.live_adapters import CubismExternalEditAdapter


class EmptySuccessClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_json(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, arguments))
        return {}


class LiveAdapterTests(unittest.TestCase):
    def test_empty_cubism_set_response_is_accepted(self) -> None:
        client = EmptySuccessClient()
        asyncio.run(CubismExternalEditAdapter(client).set_parameter_preview("model", "ParamAngleX", 15.0))
        self.assertEqual(client.calls[0][0], "cubism_set_parameter_values")
