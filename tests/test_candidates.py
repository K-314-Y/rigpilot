import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rigpilot.candidates import CandidateManager
from rigpilot.models import CandidateStatus
from rigpilot.workspace import ProjectWorkspace, WorkspaceError, sha256_file


class CandidateManagerTests(unittest.TestCase):
    def make_record(self) -> tuple[TemporaryDirectory[str], object]:
        temporary = TemporaryDirectory()
        root = Path(temporary.name)
        original = root / "official.cmo3"
        original.write_bytes(b"model")
        return temporary, ProjectWorkspace(root / "projects").create_project("sample", original)

    def test_plan_does_not_write_candidate_files(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            manager = CandidateManager()
            plan = manager.plan(record)
            self.assertTrue(str(plan.candidate_path).startswith(str(record.root / "candidates")))
            self.assertFalse(plan.candidate_path.exists())
            self.assertFalse(plan.candidate_path.parent.exists())

    def test_create_copies_working_with_matching_hash(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            candidate = CandidateManager().create(record)
            self.assertEqual(candidate.initial_sha256, record.working_sha256)
            self.assertEqual(sha256_file(candidate.model_path), record.working_sha256)
            self.assertTrue(candidate.model_path.parent.joinpath("candidate.json").is_file())
            self.assertEqual(candidate.status, CandidateStatus.CREATED)

    def test_existing_candidate_directory_is_not_overwritten(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            manager = CandidateManager()
            plan = manager.plan(record)
            plan.candidate_path.parent.mkdir(parents=True)
            with self.assertRaisesRegex(WorkspaceError, "保存先"):
                manager.create(record, plan)

    def test_path_traversal_and_non_candidate_paths_are_rejected(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            manager = CandidateManager()
            with self.assertRaisesRegex(WorkspaceError, "Candidate ID"):
                manager._candidate_model_path(record, "../working")
            candidate = manager.create(record)
            candidate.model_path = record.working_model
            with self.assertRaisesRegex(WorkspaceError, "candidates"):
                manager.mark_rejected(record, candidate)

    def test_reject_keeps_candidate_and_base_unchanged(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            manager = CandidateManager()
            candidate = manager.create(record)
            base_hash = sha256_file(record.working_model)
            manager.mark_rejected(record, candidate)
            self.assertEqual(candidate.status, CandidateStatus.REJECTED)
            self.assertTrue(candidate.model_path.is_file())
            self.assertEqual(sha256_file(record.working_model), base_hash)

    def test_promote_refuses_without_validation_or_approval(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            manager = CandidateManager()
            candidate = manager.create(record)
            with self.assertRaisesRegex(WorkspaceError, "Promotable"):
                manager.promote(record, candidate, explicit_approval=True)
            candidate.status = CandidateStatus.PROMOTABLE
            candidate.promotable = True
            with self.assertRaisesRegex(WorkspaceError, "明示承認"):
                manager.promote(record, candidate, explicit_approval=False)

    def test_promote_creates_checkpoint_before_replacing_working(self) -> None:
        temporary, record = self.make_record()
        with temporary:
            manager = CandidateManager()
            candidate = manager.create(record)
            candidate.model_path.write_bytes(b"candidate")
            candidate.current_sha256 = sha256_file(candidate.model_path)
            candidate.status = CandidateStatus.PROMOTABLE
            candidate.promotable = True
            manager.promote(record, candidate, explicit_approval=True)
            self.assertEqual(record.working_model.read_bytes(), b"candidate")
            self.assertEqual(record.checkpoints[0].path.read_bytes(), b"model")
            self.assertEqual(candidate.status, CandidateStatus.PROMOTED)
