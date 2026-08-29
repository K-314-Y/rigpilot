"""Explicit workflow transition rules, including stop and human-review boundaries."""

from __future__ import annotations

from .models import WorkflowState


class InvalidTransition(ValueError):
    pass


_ALLOWED: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.CREATED: {WorkflowState.CONNECTING, WorkflowState.CANCELED},
    WorkflowState.CONNECTING: {WorkflowState.IDENTITY_CHECK, WorkflowState.FAILED},
    WorkflowState.IDENTITY_CHECK: {WorkflowState.ANALYZING, WorkflowState.NEEDS_HUMAN_REVIEW, WorkflowState.FAILED},
    WorkflowState.ANALYZING: {WorkflowState.VALIDATING, WorkflowState.NEEDS_HUMAN_REVIEW, WorkflowState.FAILED},
    WorkflowState.GENERATING: {WorkflowState.VALIDATING, WorkflowState.REPAIRING, WorkflowState.FAILED},
    WorkflowState.VALIDATING: {WorkflowState.ANALYZING, WorkflowState.FINAL_REVIEW, WorkflowState.NEEDS_HUMAN_REVIEW, WorkflowState.FAILED},
    WorkflowState.REPAIRING: {WorkflowState.VALIDATING, WorkflowState.NEEDS_HUMAN_REVIEW, WorkflowState.FAILED},
    WorkflowState.FINAL_REVIEW: {WorkflowState.COMPLETED, WorkflowState.REPAIRING, WorkflowState.CANCELED},
    WorkflowState.PAUSED: {WorkflowState.CONNECTING, WorkflowState.CANCELED},
    WorkflowState.FAILED: {WorkflowState.PAUSED},
    WorkflowState.NEEDS_HUMAN_REVIEW: {WorkflowState.PAUSED, WorkflowState.CANCELED},
    WorkflowState.CANCELED: set(),
    WorkflowState.EMERGENCY_STOPPED: set(),
    WorkflowState.COMPLETED: set(),
}


def transition(current: WorkflowState, target: WorkflowState) -> WorkflowState:
    if target == WorkflowState.EMERGENCY_STOPPED:
        return target
    if target not in _ALLOWED[current]:
        raise InvalidTransition(f"{current} cannot transition to {target}")
    return target
