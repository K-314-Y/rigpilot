import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rigpilot.edit_transaction import (
    EditPermissionError,
    EditReadbackError,
    RollbackError,
    SafeEditTransaction,
)
from rigpilot.live_adapters import CubismExternalEditAdapter, LiveIdentity
from rigpilot.models import WorkflowState
from rigpilot.probe import EmergencyStopError, IdentityMismatchError
from rigpilot.workspace import ProjectWorkspace


class FakeEditCubism:
    def __init__(self, working: Path) -> None:
        self.identity = LiveIdentity("model-1", "document-1", "Modeling", working)
        self.status = {"connected": True, "registered": True, "approved": True, "edit_approved": True}
        self.object = {
            "Id": "PartA", "Name": "Part A", "LabelColorType": "undefined", "LabelCustomColor": "#FFFFFF",
            "Timestamp": "session-value",
        }
        self.edit_calls: list[tuple[str, str]] = []
        self.apply_temporary = True
        self.rollback_mode = "success"
        self.mutate_path: Path | None = None
        self.changed_identity = False

    async def verify_edit_schema(self) -> None:
        return None

    async def verify_schema(self) -> None:
        return None

    async def get_status(self) -> dict[str, bool]:
        return self.status

    async def get_model_uid(self) -> str:
        return "other-model" if self.changed_identity else self.identity.model_uid

    async def get_documents(self) -> dict[str, object]:
        return {"ModelingDocuments": [{"DocumentUID": self.identity.document_uid, "DocumentFilePath": str(self.identity.model_path), "Views": [{"ModelUID": self.identity.model_uid}]}]}

    async def get_current_edit_mode(self) -> str:
        return self.identity.edit_mode

    async def get_part_structure(self, _model_uid: str) -> dict[str, object]:
        return {"PartStructure": {"Id": "%Root", "Type": "Part", "Children": [{"Id": "PartA", "Type": "Part"}]}}

    async def get_object(self, _model_uid: str, object_id: str) -> dict[str, object]:
        if object_id != "PartA":
            raise RuntimeError("not editable")
        return {"Result": True, "Type": "Part", "Data": dict(self.object)}

    async def edit_part_label_color(self, _model_uid: str, part_id: str, color: str) -> dict[str, object]:
        self.edit_calls.append((part_id, color))
        if color in {"blue", "green"}:
            if self.apply_temporary:
                self.object["LabelColorType"] = color
        elif self.rollback_mode == "success" or self.rollback_mode == "retry" and self.edit_calls.count((part_id, color)) >= 2:
            self.object["LabelColorType"] = color
        if self.mutate_path is not None:
            self.mutate_path.write_bytes(b"changed")
        return {"action": "cubism_edit_part"}

    async def get_parameters(self, _model_uid: str) -> list[object]:
        return []

    async def get_parameter_value_map(self, _model_uid: str, _parameter_ids: list[str]) -> dict[str, float]:
        return {}

    async def set_parameter_previews(self, _model_uid: str, _values: dict[str, float]) -> None:
        raise AssertionError("empty Phase 1 plan must not set parameters")


class FakePcControl:
    def __init__(self, *, stop_after: int | None = None) -> None:
        self.stop_after = stop_after
        self.stop_checks = 0
        self.focuses = 0
        self.screenshots = 0

    async def verify_schema(self) -> None:
        return None

    async def is_emergency_stopped(self) -> bool:
        self.stop_checks += 1
        return self.stop_checks == self.stop_after

    async def focus_cubism(self) -> None:
        self.focuses += 1

    async def wait_for_screenshot_ready(self) -> None:
        return None

    async def take_screenshot(self) -> None:
        self.screenshots += 1


class SafeEditTransactionTests(unittest.TestCase):
    def make_record(self) -> tuple[TemporaryDirectory[str], object]:
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        original = root / "official.cmo3"
        original.write_bytes(b"model")
        return temporary, ProjectWorkspace(root / "projects").create_project("sample", original)

    @staticmethod
    def transaction(cubism: FakeEditCubism, pc: FakePcControl) -> SafeEditTransaction:
        return SafeEditTransaction(cubism, pc)

    def test_dry_run_creates_plan_without_edit(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeEditCubism(record.working_model)
            report = asyncio.run(self.transaction(cubism, FakePcControl()).dry_run(record))
            self.assertTrue(report.dry_run)
            self.assertEqual(report.plan.target_id, "PartA")
            self.assertEqual(report.plan.before, "undefined")
            self.assertEqual(report.plan.temporary, "blue")
            self.assertEqual(cubism.edit_calls, [])
            self.assertEqual(record.state, WorkflowState.CREATED)

    def test_edit_permission_missing_sends_no_edit(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeEditCubism(record.working_model)
            cubism.status["edit_approved"] = False
            with self.assertRaises(EditPermissionError):
                asyncio.run(self.transaction(cubism, FakePcControl()).run(record))
            self.assertEqual(cubism.edit_calls, [])

    def test_identity_mismatch_sends_no_edit(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeEditCubism(record.working_model)
            cubism.changed_identity = True
            with self.assertRaises(IdentityMismatchError):
                asyncio.run(self.transaction(cubism, FakePcControl()).run(record))
            self.assertEqual(cubism.edit_calls, [])

    def test_emergency_before_edit_sends_no_edit(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeEditCubism(record.working_model)
            with self.assertRaises(EmergencyStopError):
                asyncio.run(self.transaction(cubism, FakePcControl(stop_after=1)).run(record))
            self.assertEqual(cubism.edit_calls, [])
            self.assertEqual(record.state, WorkflowState.EMERGENCY_STOPPED)

    def test_emergency_after_edit_does_not_rollback(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeEditCubism(record.working_model)
            with self.assertRaises(EmergencyStopError):
                asyncio.run(self.transaction(cubism, FakePcControl(stop_after=3)).run(record))
            self.assertEqual(cubism.edit_calls, [("PartA", "blue")])
            self.assertEqual(cubism.object["LabelColorType"], "blue")
            self.assertEqual(record.state, WorkflowState.EMERGENCY_STOPPED)

    def test_temporary_edit_readback_mismatch_attempts_rollback(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeEditCubism(record.working_model)
            cubism.apply_temporary = False
            with self.assertRaises(EditReadbackError):
                asyncio.run(self.transaction(cubism, FakePcControl()).run(record))
            self.assertEqual(cubism.edit_calls, [("PartA", "blue"), ("PartA", "undefined")])
            self.assertEqual(cubism.object["LabelColorType"], "undefined")

    def test_rollback_retries_only_when_temporary_value_remains(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeEditCubism(record.working_model)
            cubism.rollback_mode = "retry"
            report = asyncio.run(self.transaction(cubism, FakePcControl()).run(record))
            self.assertTrue(report.rollback.matched)
            self.assertEqual(report.rollback.attempts, 2)
            self.assertEqual(cubism.edit_calls, [("PartA", "blue"), ("PartA", "undefined"), ("PartA", "undefined")])

    def test_rollback_failure_requires_human_review_without_extra_retry_cycle(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeEditCubism(record.working_model)
            cubism.rollback_mode = "always_temp"
            with self.assertRaises(RollbackError):
                asyncio.run(self.transaction(cubism, FakePcControl()).run(record))
            self.assertEqual(cubism.edit_calls, [("PartA", "blue"), ("PartA", "undefined"), ("PartA", "undefined")])
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)

    def test_success_restores_identical_object_and_reuses_final_validation(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeEditCubism(record.working_model)
            report = asyncio.run(self.transaction(cubism, FakePcControl()).run(record))
            self.assertTrue(report.edit.matched)
            self.assertTrue(report.rollback.matched)
            self.assertTrue(report.object_before_after_identical)
            self.assertIsNotNone(report.final_validation)
            self.assertEqual(record.state, WorkflowState.COMPLETED)
            self.assertEqual(cubism.object["LabelColorType"], "undefined")

    def test_hash_change_requires_human_review(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeEditCubism(record.working_model)
            cubism.mutate_path = record.working_model
            with self.assertRaisesRegex(Exception, "SHA-256"):
                asyncio.run(self.transaction(cubism, FakePcControl()).run(record))
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)


class EditWhitelistTests(unittest.TestCase):
    def test_label_color_adapter_sends_only_whitelisted_fields(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.call: tuple[str, dict[str, object]] | None = None

            async def call_json(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
                self.call = name, arguments
                return {"action": name}

        client = Client()
        asyncio.run(CubismExternalEditAdapter(client).edit_part_label_color("model", "part", "blue"))
        self.assertEqual(client.call, ("cubism_edit_part", {"model_uid": "model", "id": "part", "label_color_type": "blue"}))
