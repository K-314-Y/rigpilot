"""Project-local source, working copy, checkpoint, export, and log boundaries."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .models import Checkpoint, ProjectRecord, utc_now


class WorkspaceError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ProjectWorkspace:
    """Only writes below its project root; it never mutates the supplied original."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def create_project(self, project_id: str, original_model: Path) -> ProjectRecord:
        original = original_model.resolve()
        if original.suffix.lower() != ".cmo3" or not original.is_file():
            raise WorkspaceError("Phase 0 requires an existing .cmo3 file")
        project_root = self._project_root(project_id)
        if project_root.exists():
            raise WorkspaceError(f"Project already exists: {project_id}")
        source_dir = project_root / "source"
        working_dir = project_root / "working"
        for directory in (source_dir, working_dir, project_root / "candidates", project_root / "checkpoints", project_root / "exports", project_root / "logs"):
            directory.mkdir(parents=True, exist_ok=True)
        source = source_dir / original.name
        working = working_dir / original.name
        shutil.copy2(original, source)
        shutil.copy2(source, working)
        return ProjectRecord(
            project_id=project_id,
            root=project_root,
            source_model=source,
            working_model=working,
            source_sha256=sha256_file(source),
            working_sha256=sha256_file(working),
            original_model=original,
            original_sha256=sha256_file(original),
        )

    def _project_root(self, project_id: str) -> Path:
        candidate = self.root / project_id
        if not project_id or Path(project_id).name != project_id:
            raise WorkspaceError("Project ID must be a single folder name")
        try:
            candidate.resolve().relative_to(self.root)
        except ValueError as error:
            raise WorkspaceError("Project ID must stay inside the workspace") from error
        return candidate

    def checkpoint(self, record: ProjectRecord, name: str) -> Checkpoint:
        if sha256_file(record.source_model) != record.source_sha256:
            raise WorkspaceError("Source copy changed; refusing to create a checkpoint")
        destination = record.root / "checkpoints" / f"{name}.cmo3"
        if destination.exists():
            raise WorkspaceError(f"Checkpoint already exists: {name}")
        shutil.copy2(record.working_model, destination)
        return Checkpoint(name=name, path=destination, sha256=sha256_file(destination), created_at=utc_now())
