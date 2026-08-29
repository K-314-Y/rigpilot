import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rigpilot.live_adapters import LiveIdentity
from rigpilot.models import ParameterRange, WorkflowState
from rigpilot.probe import (
    EmergencyStopError,
    IdentityMismatchError,
    ProbeError,
    SafeParameterProbe,
)
from rigpilot.workspace import ProjectWorkspace


class FakeCubism:
    def __init__(self, working: Path, *, fail_restore: bool = False, changing_uid: bool = False) -> None:
        self.identity = LiveIdentity("model-1", "document-1", "Modeling", working)
        self.fail_restore = fail_restore
        self.changing_uid = changing_uid
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
        return 7.0

    async def get_part_structure(self, model_uid: str) -> dict[str, object]:
        return {"PartStructure": {}}

    async def set_parameter_preview(self, model_uid: str, parameter_id: str, value: float) -> None:
        self.called_tools.append("set_parameter_preview")
        self.set_calls.append(value)
        if self.fail_restore and len(self.set_calls) == 4:
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

    async def verify_schema(self) -> None:
        return None

    async def is_emergency_stopped(self) -> bool:
        self.stop_checks += 1
        return self.stop_on_check == self.stop_checks

    async def focus_cubism(self) -> None:
        return None

    async def take_screenshot(self) -> None:
        self.screenshots += 1
        if self.screenshot_failure:
            raise RuntimeError("capture failed")


class SafeProbeTests(unittest.TestCase):
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
            report = asyncio.run(SafeParameterProbe(cubism, FakePcControl()).run(record))
            self.assertEqual(report.screenshots_captured, 3)
            self.assertTrue(report.restored)
            self.assertEqual(cubism.set_calls, [0, 15, 30, 7])
            self.assertEqual(cubism.clear_calls, 1)
            self.assertEqual(record.state, WorkflowState.COMPLETED)
            self.assertNotIn("cubism_edit", cubism.called_tools)

    def test_screenshot_failure_still_restores(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            with self.assertRaises(RuntimeError):
                asyncio.run(SafeParameterProbe(cubism, FakePcControl(screenshot_failure=True)).run(record))
            self.assertEqual(cubism.set_calls[-1], 7)
            self.assertEqual(cubism.clear_calls, 1)

    def test_uid_change_stops_without_more_parameter_changes(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, changing_uid=True)
            with self.assertRaises(IdentityMismatchError):
                asyncio.run(SafeParameterProbe(cubism, FakePcControl()).run(record))
            self.assertEqual(cubism.set_calls, [0, 7])
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)

    def test_emergency_stop_after_preview_restores_without_capturing(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            with self.assertRaises(EmergencyStopError):
                asyncio.run(SafeParameterProbe(cubism, FakePcControl(stop_on_check=3)).run(record))
            self.assertEqual(cubism.set_calls, [0, 7])
            self.assertEqual(cubism.clear_calls, 1)
            self.assertEqual(record.state, WorkflowState.EMERGENCY_STOPPED)

    def test_restore_failure_requires_human_review(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, fail_restore=True)
            with self.assertRaisesRegex(ProbeError, "復元できません"):
                asyncio.run(SafeParameterProbe(cubism, FakePcControl()).run(record))
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)
            self.assertEqual(cubism.clear_calls, 0)
