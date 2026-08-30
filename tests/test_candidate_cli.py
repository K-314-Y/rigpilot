import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from rigpilot.cli import main
from rigpilot.storage import JsonProjectStore
from rigpilot.workspace import ProjectWorkspace


class CandidateCliTests(unittest.TestCase):
    def test_dry_run_writes_no_candidate_files(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "official.cmo3"
            original.write_bytes(b"model")
            record = ProjectWorkspace(root / "projects").create_project("sample", original)
            project_file = JsonProjectStore().path_for(record)
            JsonProjectStore().save(record)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["candidate-test", "--project", str(project_file), "--dry-run", "--json"])
            payload = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["filesystem_writes"], 0)
            self.assertEqual(list((record.root / "candidates").iterdir()), [])

    def test_live_candidate_test_is_blocked_without_emergency_confirmation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "official.cmo3"
            original.write_bytes(b"model")
            record = ProjectWorkspace(root / "projects").create_project("sample", original)
            project_file = JsonProjectStore().path_for(record)
            JsonProjectStore().save(record)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = main(["candidate-test", "--project", str(project_file)])
            self.assertEqual(result, 3)
            self.assertIn("Emergency Stop", output.getvalue())
            self.assertEqual(list((record.root / "candidates").iterdir()), [])
