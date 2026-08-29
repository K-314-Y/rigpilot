"""Adapters for the confirmed CubismExternalEditMCP and PC Control MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from .mcp_client import McpToolSession
from .models import ParameterRange


class LiveAdapterError(RuntimeError):
    pass


def _value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    raise LiveAdapterError(f"必要な応答項目がありません: {keys[0]}")


@dataclass(frozen=True)
class LiveIdentity:
    model_uid: str
    document_uid: str
    edit_mode: str
    model_path: Path


class CubismExternalEditAdapter:
    _TOOLS: ClassVar[set[str]] = {
        "cubism_status", "cubism_get_model_uid", "cubism_get_documents",
        "cubism_get_current_edit_mode", "cubism_get_parameters",
        "cubism_get_parameter_values", "cubism_get_part_structure",
        "cubism_set_parameter_values", "cubism_clear_parameter_values",
    }

    def __init__(self, client: McpToolSession) -> None:
        self.client = client

    async def verify_schema(self) -> None:
        await self.client.require_tools(self._TOOLS)

    async def get_status(self) -> dict[str, Any]:
        return await self.client.call_json("cubism_status")

    async def get_model_uid(self) -> str:
        return str(_value(await self.client.call_json("cubism_get_model_uid"), "ModelUID"))

    async def get_documents(self) -> dict[str, Any]:
        return await self.client.call_json("cubism_get_documents")

    async def get_current_edit_mode(self) -> str:
        return str(_value(await self.client.call_json("cubism_get_current_edit_mode"), "EditMode"))

    async def get_parameters(self, model_uid: str) -> list[ParameterRange]:
        data = await self.client.call_json("cubism_get_parameters", {"model_uid": model_uid})
        return [
            ParameterRange(str(item["Id"]), float(item["Min"]), float(item["Default"]), float(item["Max"]))
            for item in _value(data, "Parameters")
        ]

    async def get_parameter_values(self, model_uid: str, parameter_id: str) -> float:
        data = await self.client.call_json("cubism_get_parameter_values", {"model_uid": model_uid, "ids": [parameter_id]})
        values = _value(data, "Parameters")
        for item in values:
            if item.get("Id") == parameter_id:
                return float(item["Value"])
        raise LiveAdapterError(f"現在値が見つかりません: {parameter_id}")

    async def get_part_structure(self, model_uid: str) -> dict[str, Any]:
        return await self.client.call_json("cubism_get_part_structure", {"model_uid": model_uid})

    async def set_parameter_preview(self, model_uid: str, parameter_id: str, value: float) -> None:
        result = await self.client.call_json(
            "cubism_set_parameter_values",
            {"model_uid": model_uid, "parameters": [{"Id": parameter_id, "Value": value}]},
        )
        if result.get("Result") is not True:
            raise LiveAdapterError("Cubismが一時Parameter値を受け付けませんでした")

    async def clear_parameter_preview(self, model_uid: str) -> None:
        await self.client.call_json("cubism_clear_parameter_values", {"model_uid": model_uid})


class WindowsPcControlAdapter:
    _TOOLS: ClassVar[set[str]] = {"get_control_status", "list_windows", "activate_window", "take_screenshot"}

    def __init__(self, client: McpToolSession) -> None:
        self.client = client

    async def verify_schema(self) -> None:
        await self.client.require_tools(self._TOOLS)

    async def get_status(self) -> dict[str, Any]:
        return await self.client.call_json("get_control_status")

    async def is_emergency_stopped(self) -> bool:
        status = await self.get_status()
        return bool(status.get("control_stopped") or status.get("emergency_stop_file_exists"))

    async def find_cubism_window(self) -> dict[str, Any]:
        data = await self.client.call_data("list_windows", {"limit": 80})
        windows = data.get("windows", data) if isinstance(data, dict) else data
        if not isinstance(windows, list):
            raise LiveAdapterError("Windows一覧の形式が正しくありません")
        candidates = [item for item in windows if "cubism" in str(item.get("title", "")).casefold()]
        if len(candidates) != 1:
            raise LiveAdapterError("Cubismウィンドウを一意に特定できません")
        return candidates[0]

    async def focus_cubism(self) -> None:
        window = await self.find_cubism_window()
        result = await self.client.call_json("activate_window", {"title_contains": window["title"]})
        if result.get("ok") is not True:
            raise LiveAdapterError("Cubismウィンドウを前面化できませんでした")

    async def take_screenshot(self) -> None:
        await self.client.call_image("take_screenshot", {"monitor": 1, "max_width": 1280})
