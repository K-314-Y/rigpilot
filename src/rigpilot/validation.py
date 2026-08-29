"""Deterministic, non-persistent Live2D model validation for Phase 1."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from math import isclose
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .live_adapters import (
    CubismExternalEditAdapter,
    LiveIdentity,
    WindowsPcControlAdapter,
)
from .models import ParameterRange, ProjectRecord, WorkflowState
from .probe import EmergencyStopError, IdentityMismatchError, ProbeError
from .state_machine import transition
from .storage import JsonProjectStore
from .workspace import sha256_file


class ValidationOutcome(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    SKIPPED = "SKIPPED"
    FAIL = "FAIL"


@dataclass(frozen=True)
class ValidationState:
    label: str
    values: dict[str, float]


@dataclass(frozen=True)
class ValidationCheck:
    key: str
    display_name: str
    parameter_ids: tuple[str, ...]
    states: tuple[ValidationState, ...]
    skip_reason: str | None = None

    @property
    def executable(self) -> bool:
        return self.skip_reason is None


@dataclass(frozen=True)
class ValidationPlan:
    discovered_parameters: tuple[dict[str, Any], ...]
    checks: tuple[ValidationCheck, ...]

    @property
    def parameter_ids(self) -> tuple[str, ...]:
        return tuple(sorted({parameter_id for check in self.checks if check.executable for parameter_id in check.parameter_ids}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovered_parameters": list(self.discovered_parameters),
            "checks": [
                {
                    "key": check.key,
                    "display_name": check.display_name,
                    "parameter_ids": list(check.parameter_ids),
                    "states": [asdict(state) for state in check.states],
                    "skip_reason": check.skip_reason,
                }
                for check in self.checks
            ],
        }


@dataclass(frozen=True)
class ValidationReport:
    phase: str
    dry_run: bool
    model_uid: str
    document_uid: str
    plan: ValidationPlan
    checks: tuple[dict[str, Any], ...]
    screenshots_captured: int
    all_restored: bool
    restore_readback: bool
    source_hash_unchanged: bool
    working_hash_unchanged: bool
    original_hash_unchanged: bool | None
    report_path: Path | None = None

    @property
    def summary(self) -> dict[str, int]:
        return {outcome.value: sum(check["result"] == outcome.value for check in self.checks) for outcome in ValidationOutcome}

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "dry_run": self.dry_run,
            "model_uid": self.model_uid,
            "document_uid": self.document_uid,
            "plan": self.plan.to_dict(),
            "summary": self.summary,
            "checks": list(self.checks),
            "screenshots_captured": self.screenshots_captured,
            "all_restored": self.all_restored,
            "restore_readback": self.restore_readback,
            "source_hash_unchanged": self.source_hash_unchanged,
            "working_hash_unchanged": self.working_hash_unchanged,
            "original_hash_unchanged": self.original_hash_unchanged,
            "saved": False,
            "report_path": str(self.report_path) if self.report_path else None,
        }


class AutomaticModelValidator:
    """Executes a bounded Parameter plan and restores every selected value."""

    _SINGLE_CHECKS = (
        ("face_horizontal", "顔 左右", "ParamAngleX"),
        ("face_vertical", "顔 上下", "ParamAngleY"),
        ("face_tilt", "顔 傾き", "ParamAngleZ"),
        ("gaze_horizontal", "視線 左右", "ParamEyeBallX"),
        ("gaze_vertical", "視線 上下", "ParamEyeBallY"),
        ("mouth_open", "口 開閉", "ParamMouthOpenY"),
        ("mouth_form", "口 表情", "ParamMouthForm"),
        ("body_horizontal", "体 左右", "ParamBodyAngleX"),
        ("body_vertical", "体 上下", "ParamBodyAngleY"),
        ("body_tilt", "体 傾き", "ParamBodyAngleZ"),
    )
    _READBACK_POLL_SECONDS = 0.25
    _READBACK_MAX_RETRIES = 4

    def __init__(
        self,
        cubism: CubismExternalEditAdapter,
        pc_control: WindowsPcControlAdapter,
        *,
        focus_settle_seconds: float = 2.0,
        restore_on_emergency: bool = True,
    ) -> None:
        self.cubism = cubism
        self.pc_control = pc_control
        self.focus_settle_seconds = focus_settle_seconds
        self.restore_on_emergency = restore_on_emergency

    async def dry_run(self, record: ProjectRecord) -> ValidationReport:
        state_before = record.state
        try:
            identity, plan, _originals = await self._preflight(record)
            return ValidationReport(
                phase="1", dry_run=True, model_uid=identity.model_uid, document_uid=identity.document_uid,
                plan=plan, checks=tuple(self._planned_result(check) for check in plan.checks), screenshots_captured=0,
                all_restored=True, restore_readback=True, source_hash_unchanged=True,
                working_hash_unchanged=True, original_hash_unchanged=True,
            )
        finally:
            record.state = state_before

    async def run(self, record: ProjectRecord) -> ValidationReport:
        identity: LiveIdentity | None = None
        plan: ValidationPlan | None = None
        originals: dict[str, float] = {}
        checks: list[dict[str, Any]] = []
        screenshots = 0
        focused = False
        restore_attempts: list[dict[str, Any]] = []
        restored = False
        failure: Exception | None = None
        source_before = sha256_file(record.source_model)
        working_before = sha256_file(record.working_model)
        original_before = self._original_hash(record)
        try:
            identity, plan, originals = await self._preflight(record)
            restored = not originals
            self._move(record, WorkflowState.PROBING)
            await self._capture(record, identity, focus=True)
            focused = True
            screenshots += 1
            for check in plan.checks:
                if not check.executable:
                    checks.append(self._skipped_result(check))
                    continue
                try:
                    result, captured = await self._run_check(record, identity, check, originals, focus=not focused)
                except Exception as error:
                    checks.append(self._failed_result(check, error))
                    raise
                checks.append(result)
                screenshots += captured
                focused = True
        except EmergencyStopError as error:
            record.state = WorkflowState.EMERGENCY_STOPPED
            failure = error
        except IdentityMismatchError as error:
            record.state = WorkflowState.NEEDS_HUMAN_REVIEW
            failure = error
        except Exception as error:  # noqa: BLE001
            record.state = WorkflowState.FAILED
            failure = error
        finally:
            failure_state = record.state
            if identity is not None and originals and (self.restore_on_emergency or not isinstance(failure, EmergencyStopError)):
                try:
                    record.state = WorkflowState.RESTORING
                    restored, restore_attempts = await self._restore_all(identity, originals)
                    if not restored:
                        raise ProbeError("全Parameterを開始時値へ復元できませんでした")
                except Exception as restore_error:  # noqa: BLE001
                    record.state = WorkflowState.NEEDS_HUMAN_REVIEW
                    if failure is None:
                        failure = ProbeError(f"全Parameterを復元できませんでした: {restore_error}")
                else:
                    record.state = failure_state
            source_unchanged = sha256_file(record.source_model) == source_before
            working_unchanged = sha256_file(record.working_model) == working_before
            original_after = self._original_hash(record)
            original_unchanged = original_before == original_after if original_before is not None else None
            if not source_unchanged or not working_unchanged or original_unchanged is False:
                record.state = WorkflowState.NEEDS_HUMAN_REVIEW
                failure = ProbeError("検査中に監視対象の.cmo3のSHA-256が変化しました")
            if plan is None:
                plan = ValidationPlan((), ())
            report = ValidationReport(
                phase="1", dry_run=False, model_uid=identity.model_uid if identity else "",
                document_uid=identity.document_uid if identity else "", plan=plan, checks=tuple(checks),
                screenshots_captured=screenshots, all_restored=restored,
                restore_readback=restored, source_hash_unchanged=source_unchanged,
                working_hash_unchanged=working_unchanged, original_hash_unchanged=original_unchanged,
            )
            report = self._persist_report(record, report, restore_attempts, failure)
        if failure is not None:
            raise failure
        record.state = WorkflowState.VALIDATING
        self._move(record, WorkflowState.FINAL_REVIEW)
        self._move(record, WorkflowState.COMPLETED)
        JsonProjectStore().save(record)
        return report

    async def _preflight(self, record: ProjectRecord) -> tuple[LiveIdentity, ValidationPlan, dict[str, float]]:
        if sha256_file(record.source_model) != record.source_sha256 or sha256_file(record.working_model) != record.working_sha256:
            raise ProbeError("検査開始前にsourceまたはworkingコピーのSHA-256が一致しません")
        if record.original_sha256 is not None and self._original_hash(record) != record.original_sha256:
            raise ProbeError("検査開始前に公式サンプル原本のSHA-256が一致しません")
        self._move(record, WorkflowState.CONNECTING)
        await self.cubism.verify_schema()
        await self.pc_control.verify_schema()
        await self._ensure_not_stopped()
        status = await self.cubism.get_status()
        if not status.get("connected") or not status.get("registered") or not status.get("approved"):
            raise ProbeError("Cubism MCPの接続またはAllow承認を確認できません")
        self._move(record, WorkflowState.IDENTITY_CHECK)
        identity = await self._read_identity(record, persist=False)
        self._move(record, WorkflowState.ANALYZING)
        parameters = await self.cubism.get_parameters(identity.model_uid)
        await self.cubism.get_part_structure(identity.model_uid)
        values = await self.cubism.get_parameter_value_map(identity.model_uid, [item.parameter_id for item in parameters])
        plan = self._build_plan(parameters, values)
        originals = {parameter_id: values[parameter_id] for parameter_id in plan.parameter_ids}
        return identity, plan, originals

    def _build_plan(self, parameters: list[ParameterRange], current_values: dict[str, float]) -> ValidationPlan:
        by_id = {item.parameter_id: item for item in parameters}
        discovered = tuple(
            {
                "id": item.parameter_id, "name": item.name or item.parameter_id, "minimum": item.minimum,
                "default": item.default, "maximum": item.maximum, "current": current_values[item.parameter_id],
            }
            for item in parameters
        )
        checks: list[ValidationCheck] = []
        for key, display_name, parameter_id in self._SINGLE_CHECKS:
            parameter = by_id.get(parameter_id)
            if parameter is None:
                checks.append(ValidationCheck(key, display_name, (), (), f"{parameter_id}が存在しません"))
                continue
            checks.append(ValidationCheck(key, display_name, (parameter_id,), self._single_states(parameter)))
        left = by_id.get("ParamEyeLOpen")
        right = by_id.get("ParamEyeROpen")
        if left is None or right is None:
            missing = ", ".join(parameter_id for parameter_id, parameter in (("ParamEyeLOpen", left), ("ParamEyeROpen", right)) if parameter is None)
            checks.insert(3, ValidationCheck("blink", "まばたき", (), (), f"{missing}が存在しません"))
        else:
            checks.insert(3, ValidationCheck("blink", "まばたき", (left.parameter_id, right.parameter_id), self._blink_states(left, right)))
        return ValidationPlan(discovered, tuple(checks))

    @staticmethod
    def _single_states(parameter: ParameterRange) -> tuple[ValidationState, ...]:
        candidates = (("最小値", parameter.minimum), ("既定値", parameter.default), ("最大値", parameter.maximum))
        states: list[ValidationState] = []
        seen: set[float] = set()
        for label, value in candidates:
            if value not in seen:
                states.append(ValidationState(label, {parameter.parameter_id: value}))
                seen.add(value)
        return tuple(states)

    @staticmethod
    def _blink_states(left: ParameterRange, right: ParameterRange) -> tuple[ValidationState, ...]:
        candidates = (
            ("両目 最大値", {left.parameter_id: left.maximum, right.parameter_id: right.maximum}),
            ("左目 最小値", {left.parameter_id: left.minimum, right.parameter_id: right.maximum}),
            ("右目 最小値", {left.parameter_id: left.maximum, right.parameter_id: right.minimum}),
            ("両目 最小値", {left.parameter_id: left.minimum, right.parameter_id: right.minimum}),
        )
        states: list[ValidationState] = []
        seen: set[tuple[tuple[str, float], ...]] = set()
        for label, values in candidates:
            key = tuple(sorted(values.items()))
            if key not in seen:
                states.append(ValidationState(label, values))
                seen.add(key)
        return tuple(states)

    async def _run_check(
        self, record: ProjectRecord, identity: LiveIdentity, check: ValidationCheck,
        originals: dict[str, float], *, focus: bool,
    ) -> tuple[dict[str, Any], int]:
        screenshots = 0
        states: list[dict[str, Any]] = []
        for state in check.states:
            await self._guard_identity(record, identity)
            await self._ensure_not_stopped()
            target = {**originals, **state.values}
            await self.cubism.set_parameter_previews(identity.model_uid, target)
            matched, readback = await self._readback_until_matches(identity, target)
            if not matched:
                raise ProbeError(f"{check.display_name}のParameter読取りが一致しません")
            self._move(record, WorkflowState.CAPTURING)
            await self._capture(record, identity, focus=focus)
            focus = False
            screenshots += 1
            states.append({"label": state.label, "values": state.values, "readback": readback, "screenshot": True})
            self._move(record, WorkflowState.PROBING)
        restored, attempts = await self._restore_all(identity, originals)
        if not restored:
            raise ProbeError(f"{check.display_name}の復元読取りが一致しません: {attempts}")
        return {
            "key": check.key, "name": check.display_name, "parameter_ids": list(check.parameter_ids),
            "tested_values": states, "screenshots": screenshots, "readback": "MATCH",
            "restore": "VERIFIED", "result": ValidationOutcome.PASS.value, "warnings": [],
        }, screenshots

    async def _restore_all(self, identity: LiveIdentity, originals: dict[str, float]) -> tuple[bool, list[dict[str, Any]]]:
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, 3):
            requested_at = datetime.now(UTC).isoformat()
            await self.cubism.set_parameter_previews(identity.model_uid, originals)
            matched, readback = await self._readback_until_matches(identity, originals)
            attempts.append({
                "attempt": attempt, "requested_values": originals, "requested_at": requested_at,
                "readback_values": readback, "readback_at": datetime.now(UTC).isoformat(), "matched": matched,
            })
            if matched:
                return True, attempts
        return False, attempts

    async def _readback_until_matches(
        self, identity: LiveIdentity, expected: dict[str, float]
    ) -> tuple[bool, dict[str, float]]:
        for attempt in range(self._READBACK_MAX_RETRIES + 1):
            readback = await self.cubism.get_parameter_value_map(identity.model_uid, list(expected))
            matched = all(isclose(readback[key], value, rel_tol=0.0, abs_tol=1e-6) for key, value in expected.items())
            if matched:
                return True, readback
            if attempt < self._READBACK_MAX_RETRIES:
                await asyncio.sleep(self._READBACK_POLL_SECONDS)
        return False, readback

    async def _read_identity(self, record: ProjectRecord, *, persist: bool) -> LiveIdentity:
        model_uid = await self.cubism.get_model_uid()
        documents = await self.cubism.get_documents()
        document_uid, model_path = self._document_for_model(documents, model_uid)
        if not self._same_path(model_path, record.working_model):
            raise IdentityMismatchError("Cubismで開いている文書がRigPilotのworkingコピーと一致しません")
        identity = LiveIdentity(model_uid, document_uid, await self.cubism.get_current_edit_mode(), model_path)
        if persist:
            record.model_uid, record.document_uid = model_uid, document_uid
            JsonProjectStore().save(record)
        return identity

    async def _guard_identity(self, record: ProjectRecord, identity: LiveIdentity) -> None:
        if await self.cubism.get_model_uid() != identity.model_uid:
            raise IdentityMismatchError("model UIDが変化しました")
        if await self.cubism.get_current_edit_mode() != identity.edit_mode:
            raise IdentityMismatchError("Cubismの編集モードが変化しました")
        document_uid, path = self._document_for_model(await self.cubism.get_documents(), identity.model_uid)
        if document_uid != identity.document_uid or not self._same_path(path, identity.model_path):
            raise IdentityMismatchError("document UIDまたは対象文書が変化しました")

    async def _capture(self, record: ProjectRecord, identity: LiveIdentity, *, focus: bool) -> None:
        await self._guard_identity(record, identity)
        if focus:
            await self.pc_control.focus_cubism()
            await asyncio.sleep(self.focus_settle_seconds)
        await self._ensure_not_stopped()
        await self.pc_control.wait_for_screenshot_ready()
        await self.pc_control.take_screenshot()

    async def _ensure_not_stopped(self) -> None:
        if await self.pc_control.is_emergency_stopped():
            raise EmergencyStopError("Windows PC Control MCPは緊急停止中です")

    def _persist_report(
        self, record: ProjectRecord, report: ValidationReport, restore_attempts: list[dict[str, Any]], failure: Exception | None,
    ) -> ValidationReport:
        reports = record.root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        destination = reports / f"phase-1-validation-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        payload = report.to_dict() | {"restore_attempts": restore_attempts, "error": str(failure) if failure else None}
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stored = replace(report, report_path=destination)
        entry = stored.to_dict() | {"restore_attempts": restore_attempts, "status": "passed" if failure is None else "failed"}
        record.validation_results.append(entry)
        JsonProjectStore().save(record)
        AuditLogger(record.root / "logs" / "audit.jsonl").record(
            project_id=record.project_id, step="phase_1_validation", adapter="validator",
            operation="temporary_parameter_validation", outcome=entry["status"],
            model_uid=record.model_uid, document_uid=record.document_uid,
            metadata={"saved": False, "summary": stored.summary, "screenshots_captured": report.screenshots_captured,
                      "all_restored": report.all_restored, "source_hash_unchanged": report.source_hash_unchanged,
                      "working_hash_unchanged": report.working_hash_unchanged},
        )
        return stored

    @staticmethod
    def _planned_result(check: ValidationCheck) -> dict[str, Any]:
        return {
            "key": check.key, "name": check.display_name, "parameter_ids": list(check.parameter_ids),
            "result": "PLANNED" if check.executable else ValidationOutcome.SKIPPED.value,
            "reason": check.skip_reason,
            "states": [asdict(state) for state in check.states],
        }

    @staticmethod
    def _skipped_result(check: ValidationCheck) -> dict[str, Any]:
        return {
            "key": check.key, "name": check.display_name, "parameter_ids": [],
            "tested_values": [], "screenshots": 0, "readback": "NOT RUN", "restore": "NOT REQUIRED",
            "result": ValidationOutcome.SKIPPED.value, "warnings": [check.skip_reason],
        }

    @staticmethod
    def _failed_result(check: ValidationCheck, error: Exception) -> dict[str, Any]:
        return {
            "key": check.key, "name": check.display_name, "parameter_ids": list(check.parameter_ids),
            "tested_values": [], "screenshots": 0, "readback": "NOT COMPLETE", "restore": "PENDING",
            "result": ValidationOutcome.FAIL.value, "warnings": [str(error)],
        }

    @staticmethod
    def _original_hash(record: ProjectRecord) -> str | None:
        return sha256_file(record.original_model) if record.original_model and record.original_model.is_file() else None

    @staticmethod
    def _document_for_model(documents: dict[str, Any], model_uid: str) -> tuple[str, Path]:
        for document in documents.get("ModelingDocuments", []):
            if any(view.get("ModelUID") == model_uid for view in document.get("Views", [])):
                uid, path = document.get("DocumentUID"), document.get("DocumentFilePath")
                if isinstance(uid, str) and isinstance(path, str):
                    return uid, Path(path)
        raise IdentityMismatchError("現在のmodel UIDに対応するModeling文書が見つかりません")

    @staticmethod
    def _same_path(left: Path, right: Path) -> bool:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))

    @staticmethod
    def _move(record: ProjectRecord, target: WorkflowState) -> None:
        record.state = transition(record.state, target)
