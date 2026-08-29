import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from rigpilot.live_adapters import (
    LiveIdentity,
    ScreenshotUnavailableError,
    WindowsPcControlAdapter,
)
from rigpilot.mcp_client import McpClientError
from rigpilot.models import ParameterRange, WorkflowState
from rigpilot.probe import (
    EmergencyStopError,
    IdentityMismatchError,
    ProbeError,
    SafeParameterProbe,
)
from rigpilot.workspace import ProjectWorkspace


class FakeCubism:
    def __init__(
        self,
        working: Path,
        *,
        fail_restore: bool = False,
        changing_uid: bool = False,
        readback_value: float | None = None,
        mutate_path: Path | None = None,
    ) -> None:
        self.identity = LiveIdentity("model-1", "document-1", "Modeling", working)
        self.fail_restore = fail_restore
        self.changing_uid = changing_uid
        self.readback_value = readback_value
        self.mutate_path = mutate_path
        self.current_value = 7.0
        self.get_uid_calls = 0
        self.set_calls: list[float] = []
        self.clear_calls = 0
        self.called_tools: list[str] = []

    async def verify_schema(self) -> None:
        self.called_tools.append("schema")

    async def get_status(self) -> dict[str, bool]:
        self.called_tools.append("status")
        return {"connected": True, "registered": True, "approved": True}

    async def get_model_uid(self) -> str:
        self.get_uid_calls += 1
        if self.changing_uid and self.get_uid_calls >= 3:
            return "model-2"
        return self.identity.model_uid

    async def get_documents(self) -> dict[str, list[dict[str, object]]]:
        return {"ModelingDocuments": [{"DocumentUID": "document-1", "DocumentFilePath": str(self.identity.model_path), "Views": [{"ModelUID": "model-1"}]}]}

    async def get_current_edit_mode(self) -> str:
        return self.identity.edit_mode

    async def get_parameters(self, model_uid: str) -> list[ParameterRange]:
        return [ParameterRange("ParamAngleX", -30, 0, 30)]

    async def get_parameter_values(self, model_uid: str, parameter_id: str) -> float:
        if self.readback_value is not None and self.set_calls and self.set_calls[-1] == 7:
            return self.readback_value
        return self.current_value

    async def get_part_structure(self, model_uid: str) -> dict[str, object]:
        return {"PartStructure": {}}

    async def set_parameter_preview(self, model_uid: str, parameter_id: str, value: float) -> None:
        self.called_tools.append("set_parameter_preview")
        self.set_calls.append(value)
        self.current_value = value
        if self.mutate_path is not None:
            self.mutate_path.write_bytes(b"changed")
            self.mutate_path = None
        if self.fail_restore and value == 7:
            raise RuntimeError("restore unavailable")

    async def clear_parameter_preview(self, model_uid: str) -> None:
        self.called_tools.append("clear_parameter_preview")
        self.clear_calls += 1


class FakePcControl:
    def __init__(self, *, screenshot_failure: bool = False, stop_on_check: int | None = None) -> None:
        self.screenshot_failure = screenshot_failure
        self.stop_on_check = stop_on_check
        self.stop_checks = 0
        self.screenshots = 0
        self.focuses = 0

    async def verify_schema(self) -> None:
        return None

    async def is_emergency_stopped(self) -> bool:
        self.stop_checks += 1
        return self.stop_on_check == self.stop_checks

    async def focus_cubism(self) -> None:
        self.focuses += 1

    async def take_screenshot(self) -> None:
        self.screenshots += 1
        if self.screenshot_failure:
            raise RuntimeError("capture failed")

    async def wait_for_screenshot_ready(self) -> None:
        return None


class FakeMcpClient:
    def __init__(
        self,
        statuses: list[dict[str, object]],
        image_results: list[Exception | None] | None = None,
    ) -> None:
        self.statuses = iter(statuses)
        self.image_results = list(image_results or [])
        self.json_calls: list[str] = []
        self.image_calls = 0

    async def call_json(self, name: str, arguments: object = None) -> dict[str, object]:
        self.json_calls.append(name)
        if name == "get_pc_status":
            return next(self.statuses)
        return {"control_stopped": False}

    async def call_image(self, name: str, arguments: object = None) -> None:
        self.image_calls += 1
        if self.image_results:
            result = self.image_results.pop(0)
            if result is not None:
                raise result


def screenshot_status(
    *, cooldown: float = 0, blocked: float = 0, busy: bool = False, failures: int = 0
) -> dict[str, object]:
    return {
        "screenshot_policy": {
            "health": {
                "cooldown_seconds_remaining": cooldown,
                "blocked_seconds_remaining": blocked,
                "busy": busy,
                "recent_failures": failures,
            }
        }
    }


def make_probe(cubism: FakeCubism, pc_control: FakePcControl) -> SafeParameterProbe:
    return SafeParameterProbe(cubism, pc_control, focus_settle_seconds=0)


class SafeProbeTests(unittest.TestCase):
    def test_screenshot_readiness_uses_pc_status_and_waits_for_cooldown(self) -> None:
        client = FakeMcpClient([screenshot_status(cooldown=1.5), screenshot_status()])
        adapter = WindowsPcControlAdapter(client)  # type: ignore[arg-type]
        with patch("rigpilot.live_adapters.asyncio.sleep", new=AsyncMock()) as sleep:
            asyncio.run(adapter.wait_for_screenshot_ready())
        self.assertEqual(client.json_calls, ["get_pc_status", "get_pc_status"])
        sleep.assert_awaited_once_with(1.6)

    def test_empty_screenshot_response_retries_once_after_health_check(self) -> None:
        client = FakeMcpClient(
            [screenshot_status(), screenshot_status()],
            [McpClientError("take_screenshot が画像を返しませんでした"), None],
        )
        adapter = WindowsPcControlAdapter(client)  # type: ignore[arg-type]
        asyncio.run(adapter.take_screenshot())
        self.assertEqual(client.image_calls, 2)
        self.assertEqual(client.json_calls, ["get_pc_status", "get_pc_status"])

    def test_suspended_screenshot_does_not_retry(self) -> None:
        client = FakeMcpClient(
            [screenshot_status(blocked=30)],
            [McpClientError("take_screenshot がエラーを返しました")],
        )
        adapter = WindowsPcControlAdapter(client)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ScreenshotUnavailableError, "TEMPORARILY_SUSPENDED"):
            asyncio.run(adapter.take_screenshot())
        self.assertEqual(client.image_calls, 1)

    def make_record(self) -> tuple[TemporaryDirectory[str], object]:
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        original = root / "original.cmo3"
        original.write_bytes(b"model")
        record = ProjectWorkspace(root / "projects").create_project("mia", original)
        return temporary, record

    def test_success_restores_original_value_and_uses_no_edit_operation(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            pc_control = FakePcControl()
            report = asyncio.run(make_probe(cubism, pc_control).run(record))
            self.assertEqual(report.screenshots_captured, 4)
            self.assertTrue(report.restored)
            self.assertTrue(report.restore_readback)
            self.assertTrue(report.source_hash_unchanged)
            self.assertTrue(report.working_hash_unchanged)
            self.assertEqual(cubism.set_calls, [15, 30, 7])
            self.assertEqual(cubism.clear_calls, 0)
            self.assertEqual(pc_control.focuses, 1)
            self.assertEqual(record.state, WorkflowState.COMPLETED)
            self.assertNotIn("cubism_edit", cubism.called_tools)

    def test_screenshot_failure_still_restores(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            with self.assertRaises(RuntimeError):
                asyncio.run(make_probe(cubism, FakePcControl(screenshot_failure=True)).run(record))
            self.assertEqual(cubism.set_calls[-1], 7)
            self.assertEqual(cubism.clear_calls, 0)

    def test_uid_change_stops_without_more_parameter_changes(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, changing_uid=True)
            with self.assertRaises(IdentityMismatchError):
                asyncio.run(make_probe(cubism, FakePcControl()).run(record))
            self.assertEqual(cubism.set_calls, [7])
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)

    def test_emergency_stop_after_preview_restores_without_capturing(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            with self.assertRaises(EmergencyStopError):
                asyncio.run(make_probe(cubism, FakePcControl(stop_on_check=3)).run(record))
            self.assertEqual(cubism.set_calls, [7])
            self.assertEqual(cubism.clear_calls, 0)
            self.assertEqual(record.state, WorkflowState.EMERGENCY_STOPPED)

    def test_restore_failure_requires_human_review(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, fail_restore=True)
            with self.assertRaisesRegex(ProbeError, "復元できません"):
                asyncio.run(make_probe(cubism, FakePcControl()).run(record))
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)
            self.assertEqual(cubism.clear_calls, 0)

    def test_restore_readback_mismatch_retries_once_then_requires_review(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, readback_value=8.0)
            with self.assertRaisesRegex(ProbeError, "復元できません"):
                asyncio.run(make_probe(cubism, FakePcControl()).run(record))
            self.assertEqual(cubism.set_calls[-2:], [7, 7])
            self.assertEqual(cubism.clear_calls, 0)
            attempts = record.validation_results[-1]["restore_attempts"]
            self.assertEqual([item["readback_value"] for item in attempts], [8.0, 8.0])
            self.assertEqual([item["preview_clear"] for item in attempts], ["not_called_after_restore"] * 2)
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)

    def test_source_hash_change_requires_human_review(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, mutate_path=record.source_model)
            with self.assertRaisesRegex(ProbeError, "SHA-256"):
                asyncio.run(make_probe(cubism, FakePcControl()).run(record))
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)

    def test_working_hash_change_requires_human_review(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, mutate_path=record.working_model)
            with self.assertRaisesRegex(ProbeError, "SHA-256"):
                asyncio.run(make_probe(cubism, FakePcControl()).run(record))
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)

    def test_original_hash_change_requires_human_review(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, mutate_path=record.original_model)
            with self.assertRaisesRegex(ProbeError, "SHA-256"):
                asyncio.run(make_probe(cubism, FakePcControl()).run(record))
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)
