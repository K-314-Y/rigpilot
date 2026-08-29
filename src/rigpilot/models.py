"""Domain data for the safety-first Phase 0 workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class WorkflowState(StrEnum):
    CREATED = "created"
    CONNECTING = "connecting"
    IDENTITY_CHECK = "identity_check"
    ANALYZING = "analyzing"
    PROBING = "probing"
    CAPTURING = "capturing"
    RESTORING = "restoring"
    GENERATING = "generating"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    FINAL_REVIEW = "final_review"
    COMPLETED = "completed"
    PAUSED = "paused"
    FAILED = "failed"
    CANCELED = "canceled"
    EMERGENCY_STOPPED = "emergency_stopped"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


@dataclass(frozen=True)
class ModelIdentity:
    model_uid: str
    document_uid: str
    model_path: Path


@dataclass(frozen=True)
class ParameterRange:
    parameter_id: str
    minimum: float
    default: float
    maximum: float

    def accepts(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum


@dataclass(frozen=True)
class Checkpoint:
    name: str
    path: Path
    sha256: str
    created_at: str


@dataclass
class ProjectRecord:
    project_id: str
    root: Path
    source_model: Path
    working_model: Path
    source_sha256: str
    working_sha256: str
    state: WorkflowState = WorkflowState.CREATED
    model_uid: str | None = None
    document_uid: str | None = None
    current_step: str | None = None
    completed_steps: list[str] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    validation_results: list[dict[str, Any]] = field(default_factory=list)
    retry_count: int = 0
    final_review: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("root", "source_model", "working_model"):
            data[key] = str(data[key])
        data["state"] = self.state.value
        data["checkpoints"] = [
            {**asdict(item), "path": str(item.path)} for item in self.checkpoints
        ]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectRecord:
        copied = dict(data)
        copied["root"] = Path(copied["root"])
        copied["source_model"] = Path(copied["source_model"])
        copied["working_model"] = Path(copied["working_model"])
        copied["state"] = WorkflowState(copied["state"])
        copied["checkpoints"] = [
            Checkpoint(
                name=item["name"],
                path=Path(item["path"]),
                sha256=item["sha256"],
                created_at=item["created_at"],
            )
            for item in copied.get("checkpoints", [])
        ]
        return cls(**copied)
