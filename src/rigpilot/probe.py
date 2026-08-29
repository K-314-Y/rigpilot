"""Identity-guarded, non-persistent Cubism Parameter probes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .live_adapters import (
    CubismExternalEditAdapter,
    LiveIdentity,
    WindowsPcControlAdapter,
)
from .models import ParameterRange, ProjectRecord, WorkflowState
from .state_machine import transition
from .storage import JsonProjectStore


class ProbeError(RuntimeError):
    pass


class EmergencyStopError(ProbeError):
    pass


class IdentityMismatchError(ProbeError):
    pass


@dataclass(frozen=True)
class ProbeReport:
    parameter_id: str
    original_value: float
    tested_values: list[float]
    screenshots_captured: int
    restored: bool
    saved: bool = False
    status: str = "passed"


class SafeParameterProbe:
    _PREFERRED_PARAMETERS = ("ParamAngleX", "ParamEyeLOpen", "ParamMouthOpenY")

    def __init__(self, cubism: CubismExternalEditAdapter, pc_control: WindowsPcControlAdapter) -> None:
        self.cubism = cubism
        self.pc_control = pc_control

    async def run(self, record: ProjectRecord) -> ProbeReport:
        original_value: float | None = None
        parameter: ParameterRange | None = None
        identity: LiveIdentity | None = None
        screenshots = 0
        tested: list[float] = []
        restored = False
        failure: Exception | None = None
        try:
            self._move(record, WorkflowState.CONNECTING)
            await self.cubism.verify_schema()
            await self.pc_control.verify_schema()
            await self._ensure_not_stopped()
            status = await self.cubism.get_status()
            if not status.get("connected") or not status.get("registered") or not status.get("approved"):
                raise ProbeError("Cubism MCPの接続またはAllow承認を確認できません")
            self._move(record, WorkflowState.IDENTITY_CHECK)
            identity = await self._read_identity(record)
            self._move(record, WorkflowState.ANALYZING)
            parameters = await self.cubism.get_parameters(identity.model_uid)
            await self.cubism.get_part_structure(identity.model_uid)
            parameter = self._select_parameter(parameters)
            original_value = await self.cubism.get_parameter_values(identity.model_uid, parameter.parameter_id)
            self._move(record, WorkflowState.PROBING)
            for value in self._test_values(parameter):
                await self._guard_identity(record, identity)
                await self._ensure_not_stopped()
                await self.cubism.set_parameter_preview(identity.model_uid, parameter.parameter_id, value)
                self._move(record, WorkflowState.CAPTURING)
                await self._guard_identity(record, identity)
                await self.pc_control.focus_cubism()
                await self._ensure_not_stopped()
                await self.pc_control.take_screenshot()
                screenshots += 1
                tested.append(value)
                self._move(record, WorkflowState.PROBING)
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
            if identity is not None and parameter is not None and original_value is not None:
                try:
                    record.state = WorkflowState.RESTORING
                    await self.cubism.set_parameter_preview(identity.model_uid, parameter.parameter_id, original_value)
                    await self.cubism.clear_parameter_preview(identity.model_uid)
                    restored = True
                except Exception as restore_error:  # noqa: BLE001
                    record.state = WorkflowState.NEEDS_HUMAN_REVIEW
                    failure = ProbeError(f"元値を復元できませんでした: {restore_error}")
                else:
                    record.state = failure_state
            self._record(record, parameter, original_value, tested, screenshots, restored, failure)
        if failure is not None:
            raise failure
        record.state = WorkflowState.VALIDATING
        record.state = transition(record.state, WorkflowState.FINAL_REVIEW)
        record.state = transition(record.state, WorkflowState.COMPLETED)
        self._save(record)
        return ProbeReport(parameter.parameter_id, original_value, tested, screenshots, restored)

    async def _read_identity(self, record: ProjectRecord) -> LiveIdentity:
        model_uid = await self.cubism.get_model_uid()
        documents = await self.cubism.get_documents()
        document_uid, model_path = self._document_for_model(documents, model_uid)
        if not self._same_path(model_path, record.working_model):
            raise IdentityMismatchError("Cubismで開いている文書がRigPilotのworkingコピーと一致しません")
        edit_mode = await self.cubism.get_current_edit_mode()
        record.model_uid = model_uid
        record.document_uid = document_uid
        self._save(record)
        return LiveIdentity(model_uid, document_uid, edit_mode, model_path)

    async def _guard_identity(self, record: ProjectRecord, identity: LiveIdentity) -> None:
        if await self.cubism.get_model_uid() != identity.model_uid:
            raise IdentityMismatchError("model UIDが変化しました")
        if await self.cubism.get_current_edit_mode() != identity.edit_mode:
            raise IdentityMismatchError("Cubismの編集モードが変化しました")
        document_uid, path = self._document_for_model(await self.cubism.get_documents(), identity.model_uid)
        if document_uid != identity.document_uid or not self._same_path(path, identity.model_path):
            raise IdentityMismatchError("document UIDまたは対象文書が変化しました")
        if not self._same_path(path, record.working_model):
            raise IdentityMismatchError("対象文書がworkingコピーではありません")

    async def _ensure_not_stopped(self) -> None:
        if await self.pc_control.is_emergency_stopped():
            raise EmergencyStopError("Windows PC Control MCPは緊急停止中です")

    @staticmethod
    def _document_for_model(documents: dict[str, Any], model_uid: str) -> tuple[str, Path]:
        for document in documents.get("ModelingDocuments", []):
            views = document.get("Views", [])
            if any(view.get("ModelUID") == model_uid for view in views):
                uid = document.get("DocumentUID")
                path = document.get("DocumentFilePath")
                if isinstance(uid, str) and isinstance(path, str):
                    return uid, Path(path)
        raise IdentityMismatchError("現在のmodel UIDに対応するModeling文書が見つかりません")

    @staticmethod
    def _same_path(left: Path, right: Path) -> bool:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))

    def _select_parameter(self, parameters: list[ParameterRange]) -> ParameterRange:
        by_id = {item.parameter_id: item for item in parameters if item.minimum < item.maximum}
        for parameter_id in self._PREFERRED_PARAMETERS:
            if parameter_id in by_id:
                return by_id[parameter_id]
        if by_id:
            return min(by_id.values(), key=lambda item: item.parameter_id)
        raise ProbeError("一時Probeに使える既存Parameterがありません")

    @staticmethod
    def _test_values(parameter: ParameterRange) -> list[float]:
        midpoint = (parameter.minimum + parameter.maximum) / 2
        if midpoint == parameter.default:
            midpoint = (parameter.default + parameter.maximum) / 2
            if midpoint == parameter.default:
                midpoint = (parameter.minimum + parameter.default) / 2
        return [parameter.default, midpoint, parameter.maximum]

    @staticmethod
    def _move(record: ProjectRecord, target: WorkflowState) -> None:
        record.state = transition(record.state, target)

    def _record(self, record: ProjectRecord, parameter: ParameterRange | None, original: float | None, tested: list[float], screenshots: int, restored: bool, failure: Exception | None) -> None:
        entry = {
            "parameter_id": parameter.parameter_id if parameter else None,
            "original_value": original,
            "tested_values": tested,
            "screenshots_captured": screenshots,
            "restored": restored,
            "saved": False,
            "status": "passed" if failure is None else "failed",
        }
        record.validation_results.append(entry)
        self._save(record)
        AuditLogger(record.root / "logs" / "audit.jsonl").record(
            project_id=record.project_id, step="safe_parameter_probe", adapter="probe",
            operation="temporary_parameter_values", outcome=entry["status"],
            model_uid=record.model_uid, document_uid=record.document_uid, metadata=entry,
        )

    @staticmethod
    def _save(record: ProjectRecord) -> None:
        JsonProjectStore().save(record)
