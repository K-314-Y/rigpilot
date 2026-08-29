"""Phase 2A's bounded, reversible Part metadata edit transaction."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from .audit import AuditLogger
from .live_adapters import (
    CubismExternalEditAdapter,
    LiveIdentity,
    WindowsPcControlAdapter,
)
from .models import ProjectRecord, WorkflowState
from .probe import EmergencyStopError, IdentityMismatchError, ProbeError
from .state_machine import transition
from .storage import JsonProjectStore
from .validation import AutomaticModelValidator, ValidationReport
from .workspace import sha256_file


class EditPermissionError(ProbeError):
    pass


class EditReadbackError(ProbeError):
    pass


class RollbackError(ProbeError):
    pass


@dataclass(frozen=True)
class EditSnapshot:
    model_uid: str
    document_uid: str
    model_path: str
    edit_mode: str
    target_part_id: str
    target_object_before: dict[str, Any]
    source_sha256: str
    working_sha256: str
    original_sha256: str | None
    timestamp: str


@dataclass(frozen=True)
class EditTransactionPlan:
    target_type: str
    target_id: str
    property: str
    before: str
    temporary: str
    rollback: str


@dataclass(frozen=True)
class EditOperationResult:
    requested: str | None
    readback: str | None
    matched: bool | None


@dataclass(frozen=True)
class RollbackResult:
    requested: str | None
    readback: str | None
    matched: bool | None
    attempts: int


@dataclass(frozen=True)
class EditTransactionReport:
    phase: str
    dry_run: bool
    snapshot: EditSnapshot
    plan: EditTransactionPlan
    edit: EditOperationResult
    rollback: RollbackResult
    object_before_after_identical: bool | None
    source_hash_unchanged: bool
    working_hash_unchanged: bool
    original_hash_unchanged: bool | None
    final_validation: ValidationReport | None
    emergency_stop: bool
    saved: bool = False
    exported: bool = False
    report_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "dry_run": self.dry_run,
            "snapshot": asdict(self.snapshot),
            "plan": asdict(self.plan),
            "temporary_edit": asdict(self.edit),
            "rollback": asdict(self.rollback),
            "object_before_after": "IDENTICAL" if self.object_before_after_identical else "DIFFERENT" if self.object_before_after_identical is False else "NOT_RUN",
            "source_hash_unchanged": self.source_hash_unchanged,
            "working_hash_unchanged": self.working_hash_unchanged,
            "original_hash_unchanged": self.original_hash_unchanged,
            "final_validation": self.final_validation.to_dict() if self.final_validation else None,
            "emergency_stop": self.emergency_stop,
            "saved": self.saved,
            "exported": self.exported,
            "report_path": str(self.report_path) if self.report_path else None,
        }


class SafeEditTransaction:
    """Allows exactly one reversible LabelColorType change for a Part."""

    _PROPERTY: ClassVar[str] = "LabelColorType"
    _TEMPORARY_COLORS: ClassVar[tuple[str, ...]] = ("blue", "green")
    _VOLATILE_OBJECT_KEYS: ClassVar[set[str]] = {"timestamp", "updatedat", "sessionid", "session_id"}

    def __init__(self, cubism: CubismExternalEditAdapter, pc_control: WindowsPcControlAdapter) -> None:
        self.cubism = cubism
        self.pc_control = pc_control

    async def dry_run(self, record: ProjectRecord) -> EditTransactionReport:
        original_state = record.state
        try:
            snapshot, plan = await self._preflight(record)
            return self._report(snapshot, plan, dry_run=True)
        finally:
            record.state = original_state

    async def run(self, record: ProjectRecord) -> EditTransactionReport:
        snapshot: EditSnapshot | None = None
        plan: EditTransactionPlan | None = None
        identity: LiveIdentity | None = None
        edit = EditOperationResult(None, None, None)
        rollback = RollbackResult(None, None, None, 0)
        after: dict[str, Any] | None = None
        final_validation: ValidationReport | None = None
        emergency = False
        edit_sent = False
        rollback_attempted = False
        rollback_matched = False
        failure: Exception | None = None
        hashes_before = self._hashes(record)
        try:
            snapshot, plan = await self._preflight(record)
            identity = LiveIdentity(snapshot.model_uid, snapshot.document_uid, snapshot.edit_mode, Path(snapshot.model_path))
            record.model_uid, record.document_uid = identity.model_uid, identity.document_uid
            self._move(record, WorkflowState.REPAIRING)
            await self._ensure_not_stopped()
            await self.cubism.edit_part_label_color(identity.model_uid, plan.target_id, plan.temporary)
            edit_sent = True
            await self._ensure_not_stopped()
            temporary_object = await self.cubism.get_object(identity.model_uid, plan.target_id)
            temporary_value = self._label_color(temporary_object)
            edit = EditOperationResult(plan.temporary, temporary_value, temporary_value == plan.temporary)
            if not edit.matched:
                raise EditReadbackError("一時的なLabelColorの読取り確認が一致しません")
            rollback = await self._rollback(record, identity, plan)
            rollback_attempted = True
            rollback_matched = bool(rollback.matched)
            if not rollback_matched:
                record.state = WorkflowState.NEEDS_HUMAN_REVIEW
                raise RollbackError("LabelColorを元値へ戻せませんでした")
            after = await self.cubism.get_object(identity.model_uid, plan.target_id)
            if self._canonical(after["Data"]) != snapshot.target_object_before:
                raise RollbackError("Rollback後のPartメタデータが開始時のSnapshotと一致しません")
            self._move(record, WorkflowState.VALIDATING)
            # Emergency stop means no more Cubism commands, including Parameter restore.
            final_validation = await AutomaticModelValidator(
                self.cubism, self.pc_control, restore_on_emergency=False
            ).run(record)
            if not self._validation_passed(final_validation):
                raise ProbeError("Phase 1最終検査がPASSしませんでした")
        except EmergencyStopError as error:
            emergency = True
            record.state = WorkflowState.EMERGENCY_STOPPED
            failure = error
        except IdentityMismatchError as error:
            record.state = WorkflowState.NEEDS_HUMAN_REVIEW
            failure = error
        except Exception as error:  # noqa: BLE001
            failure = error
        finally:
            if edit_sent and not emergency and not rollback_attempted and identity is not None and plan is not None:
                try:
                    rollback = await self._rollback(record, identity, plan)
                    rollback_attempted = True
                    rollback_matched = bool(rollback.matched)
                    if rollback_matched:
                        after = await self.cubism.get_object(identity.model_uid, plan.target_id)
                    else:
                        raise RollbackError("LabelColorを元値へ戻せませんでした")
                except EmergencyStopError as error:
                    emergency = True
                    record.state = WorkflowState.EMERGENCY_STOPPED
                    failure = error
                except Exception as rollback_error:  # noqa: BLE001
                    record.state = WorkflowState.NEEDS_HUMAN_REVIEW
                    failure = RollbackError(f"Rollbackに失敗しました: {rollback_error}")
            hashes = self._hashes_unchanged(record, hashes_before)
            if not all(value is not False for value in hashes.values()):
                record.state = WorkflowState.NEEDS_HUMAN_REVIEW
                failure = ProbeError("編集テスト中に監視対象の.cmo3のSHA-256が変化しました")
            if snapshot is None or plan is None:
                if failure is not None:
                    raise failure
                raise ProbeError("編集テストのSnapshotを作成できませんでした")
            report = EditTransactionReport(
                phase="2A", dry_run=False, snapshot=snapshot, plan=plan, edit=edit, rollback=rollback,
                object_before_after_identical=(self._canonical(after["Data"]) == snapshot.target_object_before) if after else None,
                source_hash_unchanged=hashes["source"], working_hash_unchanged=hashes["working"],
                original_hash_unchanged=hashes["original"], final_validation=final_validation,
                emergency_stop=emergency,
            )
            report = self._persist(record, report, failure)
        if failure is not None:
            if record.state not in {WorkflowState.EMERGENCY_STOPPED, WorkflowState.NEEDS_HUMAN_REVIEW}:
                record.state = WorkflowState.FAILED
                JsonProjectStore().save(record)
            raise failure
        return report

    async def _preflight(self, record: ProjectRecord) -> tuple[EditSnapshot, EditTransactionPlan]:
        hashes = self._hashes(record)
        if hashes["source"] != record.source_sha256 or hashes["working"] != record.working_sha256:
            raise ProbeError("編集開始前にsourceまたはworkingコピーのSHA-256が一致しません")
        if record.original_sha256 is not None and hashes["original"] != record.original_sha256:
            raise ProbeError("編集開始前に公式サンプル原本のSHA-256が一致しません")
        self._move(record, WorkflowState.CONNECTING)
        await self.cubism.verify_edit_schema()
        await self.pc_control.verify_schema()
        await self._ensure_not_stopped()
        status = await self.cubism.get_status()
        if not status.get("connected") or not status.get("registered") or not status.get("approved"):
            raise ProbeError("Cubism MCPの接続またはAllow承認を確認できません")
        if not status.get("edit_approved"):
            raise EditPermissionError("CubismのEdit承認が必要です。Phase 2A以外では要求しません")
        self._move(record, WorkflowState.IDENTITY_CHECK)
        identity = await self._read_identity(record)
        if identity.edit_mode != "Modeling":
            raise IdentityMismatchError("Phase 2AはCubismのModelingモードでだけ実行できます")
        if record.model_uid is not None and record.model_uid != identity.model_uid:
            raise IdentityMismatchError("model UIDが記録済みの対象と一致しません")
        if record.document_uid is not None and record.document_uid != identity.document_uid:
            raise IdentityMismatchError("document UIDが記録済みの対象と一致しません")
        self._move(record, WorkflowState.ANALYZING)
        part_id, object_before = await self._select_part(identity.model_uid, await self.cubism.get_part_structure(identity.model_uid))
        before = self._label_color(object_before)
        if not isinstance(before, str):
            raise ProbeError("PartのLabelColorTypeを取得できません")
        temporary = next(color for color in self._TEMPORARY_COLORS if color != before)
        snapshot = EditSnapshot(
            model_uid=identity.model_uid, document_uid=identity.document_uid, model_path=str(identity.model_path),
            edit_mode=identity.edit_mode, target_part_id=part_id,
            target_object_before=self._canonical(object_before["Data"]), source_sha256=hashes["source"],
            working_sha256=hashes["working"], original_sha256=hashes["original"],
            timestamp=datetime.now(UTC).isoformat(),
        )
        return snapshot, EditTransactionPlan("Part", part_id, self._PROPERTY, before, temporary, before)

    async def _select_part(self, model_uid: str, structure: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        for part_id in self._part_ids(structure.get("PartStructure")):
            try:
                value = await self.cubism.get_object(model_uid, part_id)
            except Exception:  # noqa: BLE001
                value = None
            if isinstance(value, dict) and value.get("Type") == "Part" and isinstance(value.get("Data"), dict) and self._PROPERTY in value["Data"]:
                return part_id, value
        raise ProbeError("LabelColorを持つ編集可能なPartが見つかりません")

    async def _rollback(self, record: ProjectRecord, identity: LiveIdentity, plan: EditTransactionPlan) -> RollbackResult:
        self._move(record, WorkflowState.ROLLING_BACK)
        attempts = 0
        for attempt in range(1, 3):
            await self._ensure_not_stopped()
            attempts = attempt
            await self.cubism.edit_part_label_color(identity.model_uid, plan.target_id, plan.rollback)
            await self._ensure_not_stopped()
            current = self._label_color(await self.cubism.get_object(identity.model_uid, plan.target_id))
            if current == plan.rollback:
                return RollbackResult(plan.rollback, current, True, attempts)
            # A retry is permitted only when the temporary value is still present.
            if attempt == 1 and current == plan.temporary:
                continue
            return RollbackResult(plan.rollback, current, False, attempts)
        return RollbackResult(plan.rollback, None, False, attempts)

    async def _read_identity(self, record: ProjectRecord) -> LiveIdentity:
        model_uid = await self.cubism.get_model_uid()
        document_uid, model_path = self._document_for_model(await self.cubism.get_documents(), model_uid)
        if not self._same_path(model_path, record.working_model):
            raise IdentityMismatchError("Cubismで開いている文書がRigPilotのworkingコピーと一致しません")
        return LiveIdentity(model_uid, document_uid, await self.cubism.get_current_edit_mode(), model_path)

    async def _ensure_not_stopped(self) -> None:
        if await self.pc_control.is_emergency_stopped():
            raise EmergencyStopError(
                "緊急停止中です。Cubism上に未保存の一時変更が残っている可能性があります。保存せずに閉じてください"
            )

    def _report(self, snapshot: EditSnapshot, plan: EditTransactionPlan, *, dry_run: bool) -> EditTransactionReport:
        return EditTransactionReport(
            phase="2A", dry_run=dry_run, snapshot=snapshot, plan=plan,
            edit=EditOperationResult(None, None, None), rollback=RollbackResult(None, None, None, 0),
            object_before_after_identical=None, source_hash_unchanged=True, working_hash_unchanged=True,
            original_hash_unchanged=True, final_validation=None, emergency_stop=False,
        )

    def _persist(self, record: ProjectRecord, report: EditTransactionReport, failure: Exception | None) -> EditTransactionReport:
        reports = record.root / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        destination = reports / f"phase-2a-edit-transaction-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        payload = report.to_dict() | {"error": str(failure) if failure else None, "status": "passed" if failure is None else "failed"}
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        stored = replace(report, report_path=destination)
        record.validation_results.append(stored.to_dict() | {"status": payload["status"]})
        JsonProjectStore().save(record)
        AuditLogger(record.root / "logs" / "audit.jsonl").record(
            project_id=record.project_id, step="phase_2a_edit_transaction", adapter="safe_edit_transaction",
            operation="part_label_color_round_trip", outcome=payload["status"],
            model_uid=report.snapshot.model_uid, document_uid=report.snapshot.document_uid,
            metadata={
                "transaction_id": destination.stem, "target": report.plan.target_id, "property": report.plan.property,
                "before": report.plan.before, "requested": report.edit.requested, "readback": report.edit.readback,
                "rollback_requested": report.rollback.requested, "rollback_readback": report.rollback.readback,
                "structure_equal": report.object_before_after_identical, "file_hashes": {
                    "source": report.source_hash_unchanged, "working": report.working_hash_unchanged,
                    "original": report.original_hash_unchanged,
                }, "saved": False,
            },
        )
        return stored

    @classmethod
    def _part_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, dict):
            return []
        result: list[str] = []
        item_id = value.get("Id")
        if value.get("Type") == "Part" and isinstance(item_id, str) and not item_id.startswith("%"):
            result.append(item_id)
        for child in value.get("Children", value.get("Entries", [])):
            result.extend(cls._part_ids(child))
        return result

    @classmethod
    def _canonical(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: cls._canonical(item) for key, item in sorted(value.items()) if key.casefold() not in cls._VOLATILE_OBJECT_KEYS}
        if isinstance(value, list):
            return [cls._canonical(item) for item in value]
        return value

    @staticmethod
    def _label_color(value: dict[str, Any]) -> str | None:
        data = value.get("Data")
        return data.get("LabelColorType") if isinstance(data, dict) and isinstance(data.get("LabelColorType"), str) else None

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
    def _hashes(record: ProjectRecord) -> dict[str, str | None]:
        return {
            "source": sha256_file(record.source_model), "working": sha256_file(record.working_model),
            "original": sha256_file(record.original_model) if record.original_model and record.original_model.is_file() else None,
        }

    @staticmethod
    def _hashes_unchanged(record: ProjectRecord, before: dict[str, str | None]) -> dict[str, bool | None]:
        now = SafeEditTransaction._hashes(record)
        return {key: before[key] == now[key] if before[key] is not None else None for key in before}

    @staticmethod
    def _validation_passed(report: ValidationReport) -> bool:
        return report.all_restored and report.restore_readback and report.source_hash_unchanged and report.working_hash_unchanged

    @staticmethod
    def _move(record: ProjectRecord, target: WorkflowState) -> None:
        record.state = transition(record.state, target)
