"""Adapters for the confirmed CubismExternalEditMCP and PC Control MCP tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from .mcp_client import McpClientError, McpToolSession
from .models import ParameterRange


class LiveAdapterError(RuntimeError):
    pass


class ScreenshotUnavailableError(LiveAdapterError):
    """Raised when PC Control's screenshot policy does not permit capture."""


class CandidateOpenDialogError(LiveAdapterError):
    """Candidate Open could not establish the expected file dialog safely."""


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
        "cubism_set_parameter_values",
    }
    _EDIT_TOOLS: ClassVar[set[str]] = {"cubism_get_object", "cubism_edit_part"}

    def __init__(self, client: McpToolSession) -> None:
        self.client = client

    async def verify_schema(self) -> None:
        await self.client.require_tools(self._TOOLS)

    async def verify_edit_schema(self) -> None:
        """Verify only the two additional tools permitted for Phase 2A."""
        await self.client.require_tools(self._TOOLS | self._EDIT_TOOLS)

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
            ParameterRange(
                str(item["Id"]), float(item["Min"]), float(item["Default"]), float(item["Max"]),
                str(item["Name"]) if item.get("Name") is not None else None,
            )
            for item in _value(data, "Parameters")
        ]

    async def get_parameter_values(self, model_uid: str, parameter_id: str) -> float:
        values = await self.get_parameter_value_map(model_uid, [parameter_id])
        try:
            return values[parameter_id]
        except KeyError as error:
            raise LiveAdapterError(f"現在値が見つかりません: {parameter_id}") from error

    async def get_parameter_value_map(self, model_uid: str, parameter_ids: list[str]) -> dict[str, float]:
        data = await self.client.call_json("cubism_get_parameter_values", {"model_uid": model_uid, "ids": parameter_ids})
        values = _value(data, "Parameters")
        result = {str(item["Id"]): float(item["Value"]) for item in values if "Id" in item and "Value" in item}
        missing = set(parameter_ids) - result.keys()
        if missing:
            raise LiveAdapterError(f"現在値が見つかりません: {', '.join(sorted(missing))}")
        return result

    async def get_part_structure(self, model_uid: str) -> dict[str, Any]:
        return await self.client.call_json("cubism_get_part_structure", {"model_uid": model_uid})

    async def get_object(self, model_uid: str, object_id: str) -> dict[str, Any]:
        result = await self.client.call_json("cubism_get_object", {"model_uid": model_uid, "id": object_id})
        if result.get("Result") is not True or not isinstance(result.get("Data"), dict):
            raise LiveAdapterError(f"CubismのObjectを取得できません: {object_id}")
        return result

    async def edit_part_label_color(self, model_uid: str, part_id: str, label_color_type: str) -> dict[str, Any]:
        """The sole durable-edit request allowed in Phase 2A.

        This deliberately exposes no generic edit API, batch edit, save, or
        structural property.  The transaction engine verifies the resulting
        object separately and always requests the original value on rollback.
        """
        if label_color_type not in {"undefined", "custom", "red", "orange", "yellow", "green", "blue", "purple", "gray"}:
            raise LiveAdapterError("Phase 2Aでは許可されていないLabelColorです")
        return await self.client.call_json(
            "cubism_edit_part",
            {"model_uid": model_uid, "id": part_id, "label_color_type": label_color_type},
        )

    async def set_parameter_preview(self, model_uid: str, parameter_id: str, value: float) -> None:
        await self.set_parameter_previews(model_uid, {parameter_id: value})

    async def set_parameter_previews(self, model_uid: str, values: dict[str, float]) -> None:
        if not values:
            raise LiveAdapterError("一時Parameter値が指定されていません")
        await self.client.call_json(
            "cubism_set_parameter_values",
            {
                "model_uid": model_uid,
                "parameters": [{"Id": parameter_id, "Value": value} for parameter_id, value in values.items()],
            },
        )

class WindowsPcControlAdapter:
    _TOOLS: ClassVar[set[str]] = {
        "get_control_status", "get_pc_status", "list_windows", "activate_window", "take_screenshot",
    }
    _SCREENSHOT_MAX_WAIT_SECONDS = 35.0
    _SCREENSHOT_MAX_STATUS_CHECKS = 8
    _CANDIDATE_OPEN_MAX_POLLS = 10
    _CANDIDATE_OPEN_POLL_SECONDS = 0.25

    def __init__(self, client: McpToolSession) -> None:
        self.client = client

    async def verify_schema(self) -> None:
        await self.client.require_tools(self._TOOLS)

    async def verify_candidate_open_schema(self) -> None:
        """Confirm only the coordinate-free GUI actions used to open a Candidate."""
        await self.client.require_tools(self._TOOLS | {"hotkey", "type_text", "press_key"})

    async def verify_candidate_save_schema(self) -> None:
        """Confirm only the guarded GUI actions used by the Phase 2B path."""
        await self.verify_candidate_open_schema()

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
        for attempt in range(2):
            try:
                await self.client.call_image("take_screenshot", {"monitor": 1, "max_width": 1280})
                return
            except McpClientError as error:
                state = await self._screenshot_state()
                if attempt == 0 and state in {"RATE_LIMITED", "CAPTURE_BUSY", "CAPTURE_FAILED", "EMPTY_IMAGE_RESPONSE"}:
                    await self.wait_for_screenshot_ready()
                    continue
                raise ScreenshotUnavailableError(
                    f"スクリーンショットを取得できませんでした ({state})。PC Controlの状態を確認してください"
                ) from error

    async def wait_for_screenshot_ready(
        self,
        *,
        max_wait_seconds: float = _SCREENSHOT_MAX_WAIT_SECONDS,
        max_status_checks: int = _SCREENSHOT_MAX_STATUS_CHECKS,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + max_wait_seconds
        for _check in range(max_status_checks):
            status = await self.client.call_json("get_pc_status")
            health = status.get("screenshot_policy", {}).get("health", {})
            if not isinstance(health, dict):
                raise ScreenshotUnavailableError("PC Controlのスクリーンショット状態を確認できません")
            blocked = float(health.get("blocked_seconds_remaining", 0) or 0)
            cooldown = float(health.get("cooldown_seconds_remaining", 0) or 0)
            busy = bool(health.get("busy"))
            wait_seconds = max(blocked, cooldown, 0.1 if busy else 0.0)
            if wait_seconds <= 0:
                return
            if asyncio.get_running_loop().time() + wait_seconds + 0.1 > deadline:
                raise ScreenshotUnavailableError(
                    "PC Controlのスクリーンショットが一時停止中です。復帰を待ってから再実行してください"
                )
            await asyncio.sleep(wait_seconds + 0.1)
        raise ScreenshotUnavailableError("PC Controlのスクリーンショット待機回数の上限に達しました")

    async def _screenshot_state(self) -> str:
        status = await self.client.call_json("get_pc_status")
        health = status.get("screenshot_policy", {}).get("health", {})
        if not isinstance(health, dict):
            return "PC_CONTROL_DISCONNECTED"
        if float(health.get("blocked_seconds_remaining", 0) or 0) > 0:
            return "TEMPORARILY_SUSPENDED"
        if bool(health.get("busy")):
            return "CAPTURE_BUSY"
        if float(health.get("cooldown_seconds_remaining", 0) or 0) > 0:
            return "RATE_LIMITED"
        if int(health.get("recent_failures", 0) or 0) > 0:
            return "CAPTURE_FAILED"
        return "EMPTY_IMAGE_RESPONSE"

    async def open_allowed_working_model(self, model_path: Path) -> None:
        await self._open_allowed_path(model_path)

    async def open_allowed_candidate_model(self, model_path: Path) -> None:
        """Open a path already accepted by CandidateManager's project boundary."""
        await self._open_allowed_path(model_path)

    async def open_candidate_in_cubism(self, candidate_path: Path) -> None:
        """Open a verified Candidate through Cubism's own Open dialog.

        This deliberately avoids ``open_allowed_path`` because Shell opening a
        second .cmo3 did not switch CubismExternalEditMCP's active document.
        The method never uses screen coordinates and sends no save/edit key.
        """
        await self.verify_candidate_open_schema()
        if not candidate_path.is_absolute() or candidate_path.suffix.casefold() != ".cmo3":
            raise CandidateOpenDialogError("Candidate Openの対象パスが不正です")
        baseline = await self._windows()
        await self.focus_cubism()
        result = await self.client.call_json("hotkey", {"keys": ["ctrl", "o"]})
        if result.get("ok") is not True:
            raise CandidateOpenDialogError("CubismへCtrl+Oを送信できませんでした")
        dialog = await self._wait_for_open_dialog(baseline)
        typed = await self.client.call_json(
            "type_text", {"text": str(candidate_path), "use_clipboard": False}
        )
        if typed.get("ok") is not True:
            raise CandidateOpenDialogError("Candidateパスをファイル選択ダイアログへ入力できませんでした")
        entered = await self.client.call_json("press_key", {"key": "enter"})
        if entered.get("ok") is not True:
            raise CandidateOpenDialogError("Candidate OpenのEnterを送信できませんでした")
        await self._wait_for_open_dialog_to_close(baseline, dialog)

    async def _windows(self) -> list[dict[str, Any]]:
        data = await self.client.call_data("list_windows", {"limit": 80})
        windows = data.get("windows", data) if isinstance(data, dict) else data
        if not isinstance(windows, list):
            raise CandidateOpenDialogError("Windows一覧の形式が正しくありません")
        return [item for item in windows if isinstance(item, dict)]

    async def _wait_for_open_dialog(self, baseline: list[dict[str, Any]]) -> dict[str, Any]:
        baseline_handles = {self._window_handle(item) for item in baseline}
        for attempt in range(self._CANDIDATE_OPEN_MAX_POLLS):
            current = await self._windows()
            new_windows = [item for item in current if self._window_handle(item) not in baseline_handles]
            dialogs = [item for item in new_windows if self._is_open_dialog(item)]
            unexpected = [item for item in new_windows if not self._is_open_dialog(item)]
            if unexpected:
                raise CandidateOpenDialogError("Candidate Open中に想定外のダイアログが表示されました")
            if len(dialogs) == 1:
                return dialogs[0]
            if len(dialogs) > 1:
                raise CandidateOpenDialogError("Candidate Openのファイル選択ダイアログを一意に特定できません")
            if attempt + 1 < self._CANDIDATE_OPEN_MAX_POLLS:
                await asyncio.sleep(self._CANDIDATE_OPEN_POLL_SECONDS)
        raise CandidateOpenDialogError("Candidate Openのファイル選択ダイアログを確認できません")

    async def _wait_for_open_dialog_to_close(
        self, baseline: list[dict[str, Any]], dialog: dict[str, Any]
    ) -> None:
        baseline_handles = {self._window_handle(item) for item in baseline}
        dialog_handle = self._window_handle(dialog)
        for attempt in range(self._CANDIDATE_OPEN_MAX_POLLS):
            current = await self._windows()
            current_handles = {self._window_handle(item) for item in current}
            new_windows = [
                item for item in current
                if self._window_handle(item) not in baseline_handles and self._window_handle(item) != dialog_handle
            ]
            if new_windows:
                raise CandidateOpenDialogError("Candidate Open後に想定外のダイアログが表示されました")
            if dialog_handle not in current_handles:
                return
            if attempt + 1 < self._CANDIDATE_OPEN_MAX_POLLS:
                await asyncio.sleep(self._CANDIDATE_OPEN_POLL_SECONDS)
        raise CandidateOpenDialogError("Candidate Openのファイル選択ダイアログが閉じませんでした")

    @staticmethod
    def _window_handle(window: dict[str, Any]) -> int:
        value = window.get("hwnd")
        return int(value) if isinstance(value, int) else -1

    @staticmethod
    def _is_open_dialog(window: dict[str, Any]) -> bool:
        return str(window.get("title", "")).strip().casefold() in {"open", "開く"}

    async def save_current_candidate(self) -> None:
        """Send the single guarded save gesture permitted by Phase 2B.

        CandidateSandbox confirms Cubism foreground, document identity, path,
        emergency-stop state and base hashes immediately before this call.  A
        successful response is still insufficient; the sandbox also requires
        the candidate file hash to change and stabilize.
        """
        result = await self.client.call_json("hotkey", {"keys": ["ctrl", "s"]})
        if result.get("ok") is False:
            raise LiveAdapterError("Candidate保存ショートカットを送信できませんでした")

    async def _open_allowed_path(self, model_path: Path) -> None:
        await self.client.require_tools({"open_allowed_path"})
        result = await self.client.call_json("open_allowed_path", {"path": str(model_path)})
        if result.get("ok") is not True:
            raise LiveAdapterError("許可済みのモデルコピーをCubismで開けませんでした")
