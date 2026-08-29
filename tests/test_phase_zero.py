import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from rigpilot.adapters import (
    InMemoryCubismAdapter,
    InMemoryPcControlAdapter,
    SafetyViolation,
)
from rigpilot.audit import AuditLogger
from rigpilot.cli import _resume_after_review, _validate, _working_model_for_open, main
from rigpilot.engine import PhaseZeroEngine
from rigpilot.models import ModelIdentity, ParameterRange, WorkflowState
from rigpilot.validation import ValidationPlan, ValidationReport
from rigpilot.workspace import ProjectWorkspace, WorkspaceError, sha256_file


def engine_for(workspace: Path, working_path: Path, *, stopped: bool = False) -> PhaseZeroEngine:
    cubism = InMemoryCubismAdapter(
        ModelIdentity("model-1", "document-1", working_path),
        [ParameterRange("ParamAngleX", -30, 0, 30)],
    )
    return PhaseZeroEngine(
        workspace_root=workspace,
        cubism=cubism,
        pc_control=InMemoryPcControlAdapter(emergency_stop=stopped),
    )


class PhaseZeroTests(unittest.TestCase):
    def test_audit_log_redacts_secret_like_metadata(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit.jsonl"
            AuditLogger(path).record(
                project_id="mia",
                step="test",
                adapter="test",
                operation="test",
                outcome="success",
                model_uid=None,
                document_uid=None,
                metadata={"api_token": "must-not-appear", "screenshot_hash": "also-hidden"},
            )
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("must-not-appear", text)
            self.assertNotIn("also-hidden", text)

    def test_project_id_cannot_escape_the_workspace(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.cmo3"
            original.write_bytes(b"model")
            with self.assertRaisesRegex(WorkspaceError, "single folder name"):
                ProjectWorkspace(root / "projects").create_project("../outside", original)
            self.assertFalse((root / "outside").exists())

    def test_status_is_explicit_about_unverified_live_connections(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["status"]), 0)
        self.assertIn("Live Verification: 公式サンプルで確認済み", output.getvalue())
        self.assertIn("Safe Parameter Probe: 実装済み（公式サンプルで確認済み）", output.getvalue())

    def test_validate_uses_a_single_validation_session_without_doctor(self) -> None:
        plan = ValidationPlan(discovered_parameters=(), checks=())
        report = ValidationReport(
            phase="1", dry_run=True, model_uid="model-1", document_uid="document-1", plan=plan,
            checks=(), screenshots_captured=0, all_restored=True, restore_readback=True,
            source_hash_unchanged=True, working_hash_unchanged=True, original_hash_unchanged=True,
        )

        async def run_validation(*_args: object, **_kwargs: object) -> ValidationReport:
            return report

        output = StringIO()
        with (
            patch("rigpilot.cli._run_validation", run_validation),
            patch("rigpilot.cli._print_doctor", side_effect=AssertionError("Doctor must not start another MCP session")),
            redirect_stdout(output),
        ):
            self.assertEqual(_validate(Path("project.json"), Path("config.json"), dry_run=True, as_json=False), 0)
        self.assertIn("モデル検査の予定", output.getvalue())

    def test_init_copies_a_model_without_touching_the_original(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.cmo3"
            original.write_bytes(b"original-model")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    main(
                        [
                            "init",
                            "--workspace",
                            str(root / "projects"),
                            "--project-id",
                            "mia",
                            "--model",
                            str(original),
                        ]
                    ),
                    0,
                )
            self.assertEqual(original.read_bytes(), b"original-model")
            self.assertTrue((root / "projects" / "mia" / "project.json").is_file())

    def test_init_records_original_sample_hash(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "official-sample.cmo3"
            original.write_bytes(b"official-sample")
            record = ProjectWorkspace(root / "projects").create_project("official", original)
            self.assertEqual(record.original_model, original.resolve())
            self.assertEqual(record.original_sha256, sha256_file(original))

    def test_doctor_marks_missing_configuration_as_awaiting_setup(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "official-sample.cmo3"
            original.write_bytes(b"official-sample")
            record = ProjectWorkspace(root / "projects").create_project("official", original)
            from rigpilot.storage import JsonProjectStore

            JsonProjectStore().save(record)
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["doctor", "--project", str(root / "projects" / "official" / "project.json"), "--config", str(root / "missing.json")]), 0)
            self.assertIn("Cubism MCP: AWAITING USER ACTION（未設定）", output.getvalue())
            self.assertIn("次の操作:", output.getvalue())

    def test_verify_live_does_not_probe_when_doctor_is_not_ready(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "official-sample.cmo3"
            original.write_bytes(b"official-sample")
            record = ProjectWorkspace(root / "projects").create_project("official", original)
            from rigpilot.storage import JsonProjectStore

            JsonProjectStore().save(record)
            output = StringIO()
            with redirect_stdout(output):
                result = main(["verify-live", "--project", str(root / "projects" / "official" / "project.json"), "--config", str(root / "missing.json")])
            self.assertEqual(result, 3)
            self.assertIn("実機Probeは開始していません", output.getvalue())

    def test_open_working_rejects_source_copy(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "official-sample.cmo3"
            original.write_bytes(b"official-sample")
            record = ProjectWorkspace(root / "projects").create_project("official", original)
            record.working_model = record.source_model
            with self.assertRaisesRegex(WorkspaceError, "workingコピー"):
                _working_model_for_open(record)

    def test_resume_review_requires_unchanged_copies(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "official-sample.cmo3"
            original.write_bytes(b"official-sample")
            record = ProjectWorkspace(root / "projects").create_project("official", original)
            from rigpilot.storage import JsonProjectStore

            record.state = WorkflowState.NEEDS_HUMAN_REVIEW
            JsonProjectStore().save(record)
            _resume_after_review(root / "projects" / "official" / "project.json")
            self.assertEqual(JsonProjectStore().load(root / "projects" / "official" / "project.json").state, WorkflowState.PAUSED)

    def test_resume_review_allows_verified_failed_state(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "official-sample.cmo3"
            original.write_bytes(b"official-sample")
            record = ProjectWorkspace(root / "projects").create_project("official", original)
            from rigpilot.storage import JsonProjectStore

            record.state = WorkflowState.FAILED
            JsonProjectStore().save(record)
            _resume_after_review(root / "projects" / "official" / "project.json")
            self.assertEqual(JsonProjectStore().load(root / "projects" / "official" / "project.json").state, WorkflowState.PAUSED)

    def test_phase_zero_copies_original_and_restores_preview(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.cmo3"
            original.write_bytes(b"original-model")
            expected_working = root / "projects" / "mia" / "working" / "original.cmo3"
            engine = engine_for(root / "projects", expected_working)

            record = engine.initialize("mia", original)
            self.assertEqual(record.source_model.read_bytes(), b"original-model")
            self.assertEqual(record.working_model.read_bytes(), b"original-model")
            self.assertEqual(sha256_file(original), record.source_sha256)

            engine.verify_identity(record)
            screenshot = engine.preview_and_restore(record, "ParamAngleX", 15)

            self.assertEqual(screenshot, b"preview")
            self.assertIsNone(engine.cubism.preview)
            self.assertEqual(sha256_file(record.source_model), record.source_sha256)
            self.assertNotIn('"screenshot":', (record.root / "logs" / "audit.jsonl").read_text(encoding="utf-8"))

    def test_identity_mismatch_stops_for_human_review(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.cmo3"
            original.write_bytes(b"model")
            engine = engine_for(root / "projects", root / "wrong.cmo3")
            record = engine.initialize("mia", original)

            with self.assertRaisesRegex(SafetyViolation, "does not match"):
                engine.verify_identity(record)
            self.assertEqual(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)

    def test_emergency_stop_prevents_identity_check(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.cmo3"
            original.write_bytes(b"model")
            expected_working = root / "projects" / "mia" / "working" / "original.cmo3"
            engine = engine_for(root / "projects", expected_working, stopped=True)
            record = engine.initialize("mia", original)

            with self.assertRaisesRegex(SafetyViolation, "Emergency Stop"):
                engine.verify_identity(record)
            self.assertEqual(record.state, WorkflowState.EMERGENCY_STOPPED)
