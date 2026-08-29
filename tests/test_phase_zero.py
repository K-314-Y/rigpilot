import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rigpilot.adapters import (
    InMemoryCubismAdapter,
    InMemoryPcControlAdapter,
    SafetyViolation,
)
from rigpilot.engine import PhaseZeroEngine
from rigpilot.models import ModelIdentity, ParameterRange, WorkflowState
from rigpilot.workspace import sha256_file


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
