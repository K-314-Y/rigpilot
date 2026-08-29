"""The Phase 0 read-only verification cycle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters import CubismAdapter, PcControlAdapter, SafetyViolation
from .audit import AuditLogger
from .models import ModelIdentity, ProjectRecord, WorkflowState
from .state_machine import transition
from .storage import JsonProjectStore
from .workspace import ProjectWorkspace, sha256_file


class PhaseZeroEngine:
    def __init__(
        self,
        *,
        workspace_root: Path,
        cubism: CubismAdapter,
        pc_control: PcControlAdapter,
        store: JsonProjectStore | None = None,
    ) -> None:
        self.workspace = ProjectWorkspace(workspace_root)
        self.cubism = cubism
        self.pc_control = pc_control
        self.store = store or JsonProjectStore()

    def initialize(self, project_id: str, original_model: Path) -> ProjectRecord:
        record = self.workspace.create_project(project_id, original_model)
        self.store.save(record)
        self._audit(record).record(project_id=project_id, step="initialize", adapter="workspace", operation="copy_source", outcome="success", model_uid=None, document_uid=None)
        return record

    def verify_identity(self, record: ProjectRecord) -> ModelIdentity:
        self._check_stop(record, "identity_check")
        record.state = transition(record.state, WorkflowState.CONNECTING)
        record.state = transition(record.state, WorkflowState.IDENTITY_CHECK)
        identity = self.cubism.get_model_identity()
        if identity.model_path.resolve() != record.working_model.resolve():
            record.state = transition(record.state, WorkflowState.NEEDS_HUMAN_REVIEW)
            self._persist(record, "identity_check", "CubismAdapter", "get_model_identity", "mismatch")
            raise SafetyViolation("Open Cubism document does not match this project's working copy")
        record.model_uid = identity.model_uid
        record.document_uid = identity.document_uid
        record.completed_steps.append("identity_check")
        self._persist(record, "identity_check", "CubismAdapter", "get_model_identity", "success")
        return identity

    def preview_and_restore(self, record: ProjectRecord, parameter_id: str, value: float) -> bytes:
        self._check_stop(record, "preview")
        if not record.model_uid or not record.document_uid:
            raise SafetyViolation("Identity check must succeed before a preview")
        if sha256_file(record.source_model) != record.source_sha256:
            raise SafetyViolation("Source copy changed; refusing to continue")
        record.state = transition(record.state, WorkflowState.ANALYZING)
        record.state = transition(record.state, WorkflowState.VALIDATING)
        self.pc_control.focus_cubism()
        try:
            self.cubism.set_parameter_preview(parameter_id, value)
            screenshot = self.pc_control.take_screenshot()
            record.validation_results.append({"parameter_id": parameter_id, "value": value, "screenshot_captured": True})
            self._persist(record, "preview", "CubismAdapter", "set_parameter_preview", "success", {"parameter_id": parameter_id, "value": value})
            return screenshot
        finally:
            self.cubism.clear_parameter_preview()
            self._audit(record).record(project_id=record.project_id, step="preview", adapter="CubismAdapter", operation="clear_parameter_preview", outcome="success", model_uid=record.model_uid, document_uid=record.document_uid)

    def checkpoint(self, record: ProjectRecord, name: str) -> ProjectRecord:
        checkpoint = self.workspace.checkpoint(record, name)
        record.checkpoints.append(checkpoint)
        self._persist(record, "checkpoint", "workspace", "copy_working", "success", {"name": name})
        return record

    def _check_stop(self, record: ProjectRecord, step: str) -> None:
        if self.pc_control.emergency_stop_active():
            record.state = WorkflowState.EMERGENCY_STOPPED
            self._persist(record, step, "PcControlAdapter", "emergency_stop_status", "stopped")
            raise SafetyViolation("Emergency Stop is active")

    def _persist(self, record: ProjectRecord, step: str, adapter: str, operation: str, outcome: str, metadata: dict[str, Any] | None = None) -> None:
        self.store.save(record)
        self._audit(record).record(project_id=record.project_id, step=step, adapter=adapter, operation=operation, outcome=outcome, model_uid=record.model_uid, document_uid=record.document_uid, metadata=metadata)

    def _audit(self, record: ProjectRecord) -> AuditLogger:
        return AuditLogger(record.root / "logs" / "audit.jsonl")
