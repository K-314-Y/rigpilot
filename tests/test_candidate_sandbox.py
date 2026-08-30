import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rigpilot.candidates import (
    CandidateManager,
    CandidateSandbox,
    CandidateSandboxError,
)
from rigpilot.live_adapters import LiveIdentity
from rigpilot.models import CandidateStatus
from rigpilot.probe import EmergencyStopError
from rigpilot.validation import ValidationPlan, ValidationReport
from rigpilot.workspace import ProjectWorkspace, WorkspaceError


class FakeCubism:
    def __init__(self, model_path: Path) -> None:
        self.identity = LiveIdentity("model-1", "document-1", "Modeling", model_path)
        self._pending_model_path: Path | None = None
        self._pending_open_reads = 0
        self.label_color = "undefined"
        self.edits: list[tuple[str, str]] = []

    async def verify_edit_schema(self) -> None:
        return None

    async def get_status(self) -> dict[str, bool]:
        return {"connected": True, "registered": True, "approved": True, "edit_approved": True}

    async def get_model_uid(self) -> str:
        return self.identity.model_uid

    async def get_documents(self) -> dict[str, object]:
        if self._pending_model_path is not None:
            self._pending_open_reads -= 1
            if self._pending_open_reads <= 0:
                self.identity = LiveIdentity("model-1", "document-2", "Modeling", self._pending_model_path)
                self._pending_model_path = None
        return {
            "ModelingDocuments": [
                {
                    "DocumentUID": self.identity.document_uid,
                    "DocumentFilePath": str(self.identity.model_path),
                    "Views": [{"ModelUID": self.identity.model_uid}],
                }
            ]
        }

    async def get_current_edit_mode(self) -> str:
        return self.identity.edit_mode

    async def get_part_structure(self, _model_uid: str) -> dict[str, object]:
        return {"PartStructure": {"Type": "Part", "Id": "PartA"}}

    async def get_object(self, _model_uid: str, _part_id: str) -> dict[str, object]:
        return {"Type": "Part", "Data": {"LabelColorType": self.label_color}}

    async def edit_part_label_color(self, _model_uid: str, part_id: str, color: str) -> None:
        self.edits.append((part_id, color))
        self.label_color = color

    def open_candidate(self, path: Path, *, delay_reads: int) -> None:
        self._pending_model_path = path
        self._pending_open_reads = delay_reads


class FakePcControl:
    def __init__(
        self,
        cubism: FakeCubism,
        *,
        open_changes_document: bool = True,
        open_delay_reads: int = 0,
        stop_after: int | None = None,
        control_stopped: bool = False,
        emergency_stop_file_exists: bool = False,
    ) -> None:
        self.cubism = cubism
        self.open_changes_document = open_changes_document
        self.open_delay_reads = open_delay_reads
        self.stop_after = stop_after
        self.control_stopped = control_stopped
        self.emergency_stop_file_exists = emergency_stop_file_exists
        self.stop_checks = 0
        self.opened: Path | None = None
        self.saves = 0

    async def verify_candidate_save_schema(self) -> None:
        return None

    async def get_status(self) -> dict[str, object]:
        self.stop_checks += 1
        return {
            "control_stopped": self.control_stopped or self.stop_after == self.stop_checks,
            "emergency_stop_file_exists": self.emergency_stop_file_exists,
        }

    async def is_emergency_stopped(self) -> bool:
        return self.control_stopped or self.emergency_stop_file_exists or self.stop_after == self.stop_checks

    async def open_allowed_candidate_model(self, path: Path) -> None:
        self.opened = path
        if self.open_changes_document:
            self.cubism.open_candidate(path, delay_reads=self.open_delay_reads)

    async def focus_cubism(self) -> None:
        return None

    async def save_current_candidate(self) -> None:
        self.saves += 1
        assert self.opened is not None
        self.opened.write_bytes(b"saved candidate")


class FakeValidator:
    def __init__(self) -> None:
        self.target = None
        self.calls = 0

    async def run(self, _record: object, *, target: object) -> ValidationReport:
        self.calls += 1
        self.target = target
        return ValidationReport(
            phase="1",
            dry_run=False,
            model_uid="model-1",
            document_uid="document-2",
            plan=ValidationPlan((), ()),
            checks=(),
            screenshots_captured=0,
            all_restored=True,
            restore_readback=True,
            source_hash_unchanged=True,
            working_hash_unchanged=True,
            original_hash_unchanged=True,
            target_role="candidate",
            target_hash_unchanged=True,
        )


class CandidateSandboxTests(unittest.TestCase):
    def make_record(self) -> tuple[TemporaryDirectory[str], object]:
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        original = root / "official.cmo3"
        original.write_bytes(b"model")
        return temporary, ProjectWorkspace(root / "projects").create_project("sample", original)

    def test_unverified_emergency_stop_blocks_before_candidate_creation(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            pc = FakePcControl(cubism)
            with self.assertRaisesRegex(CandidateSandboxError, "Emergency Stop"):
                asyncio.run(CandidateSandbox(cubism, pc, validator=FakeValidator()).run(record, emergency_stop_verified=False))
            self.assertEqual(list((record.root / "candidates").iterdir()), [])
            self.assertEqual(pc.saves, 0)

    def test_stop_file_blocks_before_candidate_creation(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            pc = FakePcControl(cubism, emergency_stop_file_exists=True)
            with self.assertRaises(EmergencyStopError):
                asyncio.run(CandidateSandbox(cubism, pc, validator=FakeValidator()).run(record, emergency_stop_verified=True))
            self.assertEqual(list((record.root / "candidates").iterdir()), [])
            self.assertEqual(pc.saves, 0)

    def test_control_stopped_blocks_before_candidate_creation(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            pc = FakePcControl(cubism, control_stopped=True)
            with self.assertRaises(EmergencyStopError):
                asyncio.run(CandidateSandbox(cubism, pc, validator=FakeValidator()).run(record, emergency_stop_verified=True))
            self.assertEqual(list((record.root / "candidates").iterdir()), [])
            self.assertEqual(pc.saves, 0)

    def test_candidate_save_changes_only_candidate_then_rejects_without_deleting(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            pc = FakePcControl(cubism)
            validator = FakeValidator()
            report = asyncio.run(CandidateSandbox(cubism, pc, validator=validator).run(record, emergency_stop_verified=True))
            self.assertEqual(report.final_status, CandidateStatus.REJECTED)
            self.assertEqual(record.working_model.read_bytes(), b"model")
            self.assertEqual(record.source_model.read_bytes(), b"model")
            self.assertEqual(report.candidate_path.read_bytes(), b"saved candidate")
            self.assertEqual(pc.opened, report.candidate_path)
            self.assertEqual(pc.saves, 1)
            self.assertEqual(validator.target.role, "candidate")
            self.assertEqual(cubism.edits, [("PartA", "blue")])

    def test_candidate_open_waits_for_identity_switch_before_editing(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            pc = FakePcControl(cubism, open_delay_reads=2)
            report = asyncio.run(
                CandidateSandbox(cubism, pc, validator=FakeValidator()).run(
                    record, emergency_stop_verified=True
                )
            )
            self.assertEqual(report.final_status, CandidateStatus.REJECTED)
            self.assertEqual(cubism.edits, [("PartA", "blue")])

    def test_identity_mismatch_prevents_save_and_keeps_candidate_for_review(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            pc = FakePcControl(cubism, open_changes_document=False)
            with self.assertRaisesRegex(Exception, "Candidateコピー"):
                asyncio.run(CandidateSandbox(cubism, pc, validator=FakeValidator()).run(record, emergency_stop_verified=True))
            candidate_json = next((record.root / "candidates").glob("candidate-*/candidate.json"))
            candidate = CandidateManager().load(record, candidate_json.parent.name)
            self.assertEqual(candidate.status, CandidateStatus.NEEDS_HUMAN_REVIEW)
            self.assertEqual(pc.saves, 0)

    def test_emergency_stop_after_edit_blocks_candidate_without_saving(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            pc = FakePcControl(cubism, stop_after=3)
            with self.assertRaises(EmergencyStopError):
                asyncio.run(CandidateSandbox(cubism, pc, validator=FakeValidator()).run(record, emergency_stop_verified=True))
            candidate_json = next((record.root / "candidates").glob("candidate-*/candidate.json"))
            candidate = CandidateManager().load(record, candidate_json.parent.name)
            self.assertEqual(candidate.status, CandidateStatus.BLOCKED)
            self.assertEqual(pc.saves, 0)
            self.assertEqual(cubism.edits, [("PartA", "blue")])

    def test_emergency_stop_after_save_blocks_validation_commands(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            cubism = FakeCubism(record.working_model)
            pc = FakePcControl(cubism, stop_after=4)
            validator = FakeValidator()
            with self.assertRaises(EmergencyStopError):
                asyncio.run(CandidateSandbox(cubism, pc, validator=validator).run(record, emergency_stop_verified=True))
            candidate_json = next((record.root / "candidates").glob("candidate-*/candidate.json"))
            candidate = CandidateManager().load(record, candidate_json.parent.name)
            self.assertEqual(candidate.status, CandidateStatus.BLOCKED)
            self.assertEqual(pc.saves, 1)
            self.assertEqual(validator.calls, 0)

    def test_changed_source_blocks_before_candidate_creation(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            record.source_model.write_bytes(b"changed source")
            cubism = FakeCubism(record.working_model)
            pc = FakePcControl(cubism)
            with self.assertRaisesRegex(WorkspaceError, "sourceコピー"):
                asyncio.run(CandidateSandbox(cubism, pc, validator=FakeValidator()).run(record, emergency_stop_verified=True))
            self.assertEqual(list((record.root / "candidates").iterdir()), [])

    def test_changed_working_blocks_before_candidate_creation(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            record.working_model.write_bytes(b"changed working")
            cubism = FakeCubism(record.working_model)
            pc = FakePcControl(cubism)
            with self.assertRaisesRegex(WorkspaceError, "workingコピー"):
                asyncio.run(CandidateSandbox(cubism, pc, validator=FakeValidator()).run(record, emergency_stop_verified=True))
            self.assertEqual(list((record.root / "candidates").iterdir()), [])
