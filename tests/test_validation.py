import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rigpilot.live_adapters import LiveIdentity
from rigpilot.models import ParameterRange, WorkflowState
from rigpilot.probe import EmergencyStopError
from rigpilot.validation import AutomaticModelValidator, ValidationOutcome
from rigpilot.workspace import ProjectWorkspace


class FakeCubism:
    def __init__(self, working: Path, parameters: list[ParameterRange]) -> None:
        self.identity = LiveIdentity("model-1", "document-1", "Modeling", working)
        self.parameters = parameters
        self.values = {item.parameter_id: item.default for item in parameters}
        self.set_calls: list[dict[str, float]] = []
        self.changing_uid = False

    async def verify_schema(self) -> None:
        return None

    async def get_status(self) -> dict[str, bool]:
        return {"connected": True, "registered": True, "approved": True}

    async def get_model_uid(self) -> str:
        return "model-2" if self.changing_uid and self.set_calls else self.identity.model_uid

    async def get_documents(self) -> dict[str, object]:
        return {
            "ModelingDocuments": [
                {"DocumentUID": self.identity.document_uid, "DocumentFilePath": str(self.identity.model_path), "Views": [{"ModelUID": self.identity.model_uid}]}
            ]
        }

    async def get_current_edit_mode(self) -> str:
        return self.identity.edit_mode

    async def get_parameters(self, model_uid: str) -> list[ParameterRange]:
        return self.parameters

    async def get_parameter_value_map(self, model_uid: str, parameter_ids: list[str]) -> dict[str, float]:
        return {parameter_id: self.values[parameter_id] for parameter_id in parameter_ids}

    async def get_part_structure(self, model_uid: str) -> dict[str, object]:
        return {"PartStructure": {}}

    async def set_parameter_previews(self, model_uid: str, values: dict[str, float]) -> None:
        self.set_calls.append(dict(values))
        self.values.update(values)


class FakePcControl:
    def __init__(self, *, fail_screenshot: bool = False, stop_after: int | None = None) -> None:
        self.fail_screenshot = fail_screenshot
        self.stop_after = stop_after
        self.stop_checks = 0
        self.screenshots = 0
        self.focuses = 0

    async def verify_schema(self) -> None:
        return None

    async def is_emergency_stopped(self) -> bool:
        self.stop_checks += 1
        return self.stop_after == self.stop_checks

    async def focus_cubism(self) -> None:
        self.focuses += 1

    async def wait_for_screenshot_ready(self) -> None:
        return None

    async def take_screenshot(self) -> None:
        self.screenshots += 1
        if self.fail_screenshot:
            raise RuntimeError("screenshot failed")


class DelayedReadbackCubism(FakeCubism):
    def __init__(self, working: Path, entries: list[ParameterRange]) -> None:
        super().__init__(working, entries)
        self.pending: dict[str, float] | None = None
        self.readbacks_after_set = 0

    async def get_parameter_value_map(self, model_uid: str, parameter_ids: list[str]) -> dict[str, float]:
        if self.pending is not None:
            self.readbacks_after_set += 1
            if self.readbacks_after_set >= 2:
                self.values.update(self.pending)
                self.pending = None
        return await super().get_parameter_value_map(model_uid, parameter_ids)

    async def set_parameter_previews(self, model_uid: str, values: dict[str, float]) -> None:
        self.set_calls.append(dict(values))
        self.pending = dict(values)
        self.readbacks_after_set = 0


def parameters() -> list[ParameterRange]:
    return [
        ParameterRange("ParamAngleX", -30, 0, 30, "Angle X"),
        ParameterRange("ParamEyeLOpen", 0, 1, 1, "Left Eye"),
        ParameterRange("ParamEyeROpen", 0, 1, 1, "Right Eye"),
        ParameterRange("ParamMouthOpenY", 0, 0, 1, "Mouth Open"),
    ]


class AutomaticValidationTests(unittest.TestCase):
    def make_record(self) -> tuple[TemporaryDirectory[str], object]:
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        original = root / "official.cmo3"
        original.write_bytes(b"model")
        return temporary, ProjectWorkspace(root / "projects").create_project("sample", original)

    def make_validator(self, cubism: FakeCubism, pc: FakePcControl) -> AutomaticModelValidator:
        return AutomaticModelValidator(cubism, pc, focus_settle_seconds=0)

    def test_plan_selects_existing_parameters_and_skips_missing_ones(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, parameters())
            plan = self.make_validator(cubism, FakePcControl())._build_plan(cubism.parameters, cubism.values)
            outcomes = {check.key: check.skip_reason for check in plan.checks}
            self.assertIsNone(outcomes["face_horizontal"])
            self.assertIsNone(outcomes["blink"])
            self.assertIsNone(outcomes["mouth_open"])
            self.assertEqual(outcomes["gaze_horizontal"], "ParamEyeBallXが存在しません")
            self.assertIn("ParamEyeLOpen", plan.parameter_ids)
            self.assertIn("ParamEyeROpen", plan.parameter_ids)

    def test_dry_run_discovers_plan_without_preview_or_screenshot(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, parameters())
            pc = FakePcControl()
            report = asyncio.run(self.make_validator(cubism, pc).dry_run(record))
            self.assertTrue(report.dry_run)
            self.assertEqual(cubism.set_calls, [])
            self.assertEqual(pc.screenshots, 0)
            self.assertEqual(record.state, WorkflowState.CREATED)
            self.assertEqual(report.summary[ValidationOutcome.SKIPPED.value], 8)

    def test_validation_sweeps_and_restores_all_selected_parameters(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, parameters())
            pc = FakePcControl()
            report = asyncio.run(self.make_validator(cubism, pc).run(record))
            self.assertEqual(record.state, WorkflowState.COMPLETED)
            self.assertTrue(report.all_restored)
            self.assertTrue(report.restore_readback)
            self.assertEqual(cubism.values, {item.parameter_id: item.default for item in parameters()})
            self.assertEqual(pc.focuses, 1)
            self.assertGreaterEqual(report.screenshots_captured, 10)
            self.assertEqual(report.summary[ValidationOutcome.PASS.value], 3)
            self.assertEqual(report.summary[ValidationOutcome.SKIPPED.value], 8)
            self.assertIsNotNone(report.report_path)
            payload = json.loads(report.report_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["all_restored"])
            self.assertTrue(payload["source_hash_unchanged"])

    def test_validation_waits_for_delayed_parameter_readback(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = DelayedReadbackCubism(record.working_model, parameters())
            validator = self.make_validator(cubism, FakePcControl())
            validator._READBACK_POLL_SECONDS = 0
            report = asyncio.run(validator.run(record))
            self.assertTrue(report.all_restored)
            self.assertEqual(cubism.values, {item.parameter_id: item.default for item in parameters()})

    def test_screenshot_failure_restores_every_parameter_and_stops(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, parameters())
            with self.assertRaisesRegex(RuntimeError, "screenshot failed"):
                asyncio.run(self.make_validator(cubism, FakePcControl(fail_screenshot=True)).run(record))
            self.assertEqual(cubism.values, {item.parameter_id: item.default for item in parameters()})
            self.assertEqual(record.state, WorkflowState.FAILED)

    def test_emergency_stop_restores_every_parameter_and_stops(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, parameters())
            with self.assertRaises(EmergencyStopError):
                asyncio.run(self.make_validator(cubism, FakePcControl(stop_after=3)).run(record))
            self.assertEqual(cubism.values, {item.parameter_id: item.default for item in parameters()})
            self.assertEqual(record.state, WorkflowState.EMERGENCY_STOPPED)

    def test_identity_change_restores_and_requires_human_review(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model, parameters())
            cubism.changing_uid = True
            with self.assertRaisesRegex(Exception, "model UIDが変化しました"):
                asyncio.run(self.make_validator(cubism, FakePcControl()).run(record))
            self.assertEqual(cubism.values, {item.parameter_id: item.default for item in parameters()})
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)
