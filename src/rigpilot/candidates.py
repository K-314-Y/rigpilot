"""Phase 2B candidate-only file boundaries and promotion guards."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .live_adapters import (
    CubismExternalEditAdapter,
    LiveIdentity,
    WindowsPcControlAdapter,
)
from .models import (
    CandidateRecord,
    CandidateStatus,
    ProjectRecord,
    ValidationTarget,
    utc_now,
)
from .probe import EmergencyStopError, IdentityMismatchError, ProbeError
from .validation import AutomaticModelValidator, ValidationReport
from .workspace import ProjectWorkspace, WorkspaceError, sha256_file


@dataclass(frozen=True)
class CandidatePlan:
    candidate_id: str
    candidate_path: Path
    base_path: Path
    base_sha256: str


@dataclass(frozen=True)
class CandidateSandboxReport:
    candidate_id: str
    candidate_path: Path
    candidate_sha256: str
    source_hash_unchanged: bool
    working_hash_unchanged: bool
    validation: ValidationReport
    final_status: CandidateStatus


class CandidateSandboxError(ProbeError):
    """A candidate could not safely advance to the next isolated stage."""


class CandidateManager:
    """Creates isolated candidates and refuses any unguarded base mutation."""

    def plan(self, record: ProjectRecord) -> CandidatePlan:
        self.verify_base(record)
        candidate_id = self._new_id()
        candidate_path = self._candidate_model_path(record, candidate_id)
        if candidate_path.exists():
            raise WorkspaceError("Candidate保存先が既に存在します")
        return CandidatePlan(candidate_id, candidate_path, record.working_model.resolve(), record.working_sha256)

    def create(self, record: ProjectRecord, plan: CandidatePlan | None = None) -> CandidateRecord:
        chosen = plan or self.plan(record)
        self.verify_base(record)
        destination = self._candidate_model_path(record, chosen.candidate_id)
        if destination != chosen.candidate_path.resolve() or destination.exists() or destination.parent.exists():
            raise WorkspaceError("Candidate保存先を安全に作成できません")
        destination.parent.mkdir(parents=True, exist_ok=False)
        shutil.copy2(record.working_model, destination)
        initial = sha256_file(destination)
        if initial != record.working_sha256:
            raise WorkspaceError("Candidate初期SHA-256がworkingコピーと一致しません")
        candidate = CandidateRecord(
            candidate_id=chosen.candidate_id,
            project_id=record.project_id,
            model_path=destination,
            base_model_path=record.working_model.resolve(),
            base_sha256=record.working_sha256,
            initial_sha256=initial,
            current_sha256=initial,
        )
        self.save(candidate, record)
        return candidate

    def load(self, record: ProjectRecord, candidate_id: str) -> CandidateRecord:
        path = self._candidate_metadata_path(record, candidate_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkspaceError("CandidateRecordを読み取れません") from error
        candidate = CandidateRecord.from_dict(raw)
        if candidate.candidate_id != candidate_id or candidate.project_id != record.project_id:
            raise WorkspaceError("CandidateRecordの識別情報が一致しません")
        self._assert_candidate_path(record, candidate.model_path)
        return candidate

    def save(self, candidate: CandidateRecord, record: ProjectRecord) -> None:
        self._assert_candidate_path(record, candidate.model_path)
        candidate.updated_at = utc_now()
        destination = self._candidate_metadata_path(record, candidate.candidate_id)
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(candidate.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for attempt in range(3):
            try:
                temporary.replace(destination)
                return
            except PermissionError as error:
                if attempt == 2:
                    raise WorkspaceError("CandidateRecordを安全に保存できません") from error
                time.sleep(0.05 * (attempt + 1))

    def mark_rejected(self, record: ProjectRecord, candidate: CandidateRecord) -> CandidateRecord:
        self._assert_candidate_path(record, candidate.model_path)
        candidate.status = CandidateStatus.REJECTED
        candidate.promotable = False
        candidate.current_sha256 = sha256_file(candidate.model_path)
        self.save(candidate, record)
        return candidate

    def validation_target(self, record: ProjectRecord, candidate: CandidateRecord) -> ValidationTarget:
        self._assert_candidate_path(record, candidate.model_path)
        return ValidationTarget(candidate.model_path, candidate.current_sha256, "candidate")

    def promote(self, record: ProjectRecord, candidate: CandidateRecord, *, explicit_approval: bool) -> None:
        self._assert_candidate_path(record, candidate.model_path)
        if not explicit_approval:
            raise WorkspaceError("Candidate Promoteには利用者の明示承認が必要です")
        if candidate.status is not CandidateStatus.PROMOTABLE or not candidate.promotable:
            raise WorkspaceError("検証済みでPromotableなCandidateだけをPromoteできます")
        self.verify_base(record)
        if sha256_file(candidate.model_path) != candidate.current_sha256:
            raise WorkspaceError("CandidateのSHA-256が記録値と一致しません")
        checkpoint = ProjectWorkspace(record.root.parent).checkpoint(record, f"before-{candidate.candidate_id}")
        shutil.copy2(candidate.model_path, record.working_model)
        record.working_sha256 = sha256_file(record.working_model)
        candidate.status = CandidateStatus.PROMOTED
        candidate.promotable = False
        self.save(candidate, record)
        record.checkpoints.append(checkpoint)

    def verify_base(self, record: ProjectRecord) -> None:
        if sha256_file(record.source_model) != record.source_sha256:
            raise WorkspaceError("sourceコピーのSHA-256が一致しません")
        if sha256_file(record.working_model) != record.working_sha256:
            raise WorkspaceError("workingコピーのSHA-256が一致しません")
        if record.original_model and record.original_sha256 and sha256_file(record.original_model) != record.original_sha256:
            raise WorkspaceError("公式サンプル原本のSHA-256が一致しません")

    @staticmethod
    def _new_id() -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"candidate-{stamp}-{uuid.uuid4().hex[:8]}"

    def _candidate_model_path(self, record: ProjectRecord, candidate_id: str) -> Path:
        if not candidate_id.startswith("candidate-") or Path(candidate_id).name != candidate_id:
            raise WorkspaceError("Candidate IDが不正です")
        return (record.root / "candidates" / candidate_id / record.working_model.name).resolve()

    def _candidate_metadata_path(self, record: ProjectRecord, candidate_id: str) -> Path:
        return self._candidate_model_path(record, candidate_id).parent / "candidate.json"

    def _assert_candidate_path(self, record: ProjectRecord, path: Path) -> None:
        candidates_root = (record.root / "candidates").resolve()
        resolved = path.resolve()
        try:
            resolved.relative_to(candidates_root)
        except ValueError as error:
            raise WorkspaceError("Candidateはproject/candidates配下だけに作成できます") from error
        if resolved.suffix.casefold() != ".cmo3" or resolved.parent.parent != candidates_root:
            raise WorkspaceError("Candidateモデルのパスが不正です")


class CandidateSandbox:
    """The only Phase 2B path that may save, and only to a candidate copy."""

    _PROPERTY = "LabelColorType"
    _TEMPORARY_COLORS = ("blue", "green", "purple", "orange", "red")
    _HASH_POLL_SECONDS = 0.25
    _HASH_MAX_POLLS = 20
    _HASH_STABLE_READS = 2
    _OPEN_IDENTITY_MAX_POLLS = 12
    _OPEN_IDENTITY_POLL_SECONDS = 0.25

    def __init__(
        self,
        cubism: CubismExternalEditAdapter,
        pc_control: WindowsPcControlAdapter,
        *,
        manager: CandidateManager | None = None,
        validator: AutomaticModelValidator | None = None,
    ) -> None:
        self.cubism = cubism
        self.pc_control = pc_control
        self.manager = manager or CandidateManager()
        self.validator = validator or AutomaticModelValidator(cubism, pc_control)

    async def run(self, record: ProjectRecord, *, emergency_stop_verified: bool) -> CandidateSandboxReport:
        """Create, edit, save, validate, then retain a rejected candidate.

        The caller must provide an explicit one-time human confirmation that
        the actual emergency-stop mechanism works.  Without it no candidate
        directory is created and no Cubism/PC write action is sent.
        """
        plan = self.manager.plan(record)
        if not emergency_stop_verified:
            raise CandidateSandboxError(
                "Candidate Saveは、Windows PC Control MCPのEmergency Stopを手動確認するまでBLOCKEDです"
            )
        await self.cubism.verify_edit_schema()
        await self.pc_control.verify_candidate_save_schema()
        await self._ensure_not_stopped()
        status = await self.cubism.get_status()
        if not status.get("connected") or not status.get("registered") or not status.get("approved"):
            raise CandidateSandboxError("Cubism MCPの接続またはAllow承認を確認できません")
        if not status.get("edit_approved"):
            raise CandidateSandboxError("Candidate編集にはCubismのEdit承認が必要です")

        candidate = self.manager.create(record, plan)
        try:
            await self.pc_control.open_allowed_candidate_model(candidate.model_path)
            await self.pc_control.focus_cubism()
            identity = await self._wait_for_candidate_identity(candidate)
            candidate.status = CandidateStatus.OPENED
            candidate.model_uid, candidate.document_uid = identity.model_uid, identity.document_uid
            self.manager.save(candidate, record)

            part_id, before = await self._select_editable_part(identity.model_uid)
            temporary = next(color for color in self._TEMPORARY_COLORS if color != before)
            await self._ensure_not_stopped()
            await self.cubism.edit_part_label_color(identity.model_uid, part_id, temporary)
            changed = self._label_color(await self.cubism.get_object(identity.model_uid, part_id))
            if changed != temporary:
                raise CandidateSandboxError("Candidate編集の読取り確認が一致しません")
            candidate.status = CandidateStatus.EDITED
            self.manager.save(candidate, record)

            await self._pre_save_guard(record, candidate, identity)
            await self.pc_control.save_current_candidate()
            candidate.current_sha256 = await self._wait_for_saved_hash(candidate)
            candidate.status = CandidateStatus.SAVED
            self.manager.save(candidate, record)

            # An Emergency Stop after the save must prevent Phase 1 from
            # issuing any further Cubism command, even though the candidate
            # file has already changed.
            await self._ensure_not_stopped()
            validation = await self.validator.run(record, target=self.manager.validation_target(record, candidate))
            candidate.validation_result = {
                "summary": validation.summary,
                "all_restored": validation.all_restored,
                "restore_readback": validation.restore_readback,
                "source_hash_unchanged": validation.source_hash_unchanged,
                "working_hash_unchanged": validation.working_hash_unchanged,
                "target_hash_unchanged": validation.target_hash_unchanged,
            }
            if not self._validation_passed(validation):
                raise CandidateSandboxError("Candidate ValidationがPASSしませんでした")
            candidate.status = CandidateStatus.VALIDATED
            self.manager.save(candidate, record)
            # Phase 2B never promotes automatically.  Retain this verified
            # artifact for inspection, but make its non-promotion explicit.
            candidate = self.manager.mark_rejected(record, candidate)
            return CandidateSandboxReport(
                candidate.candidate_id,
                candidate.model_path,
                candidate.current_sha256,
                sha256_file(record.source_model) == record.source_sha256,
                sha256_file(record.working_model) == record.working_sha256,
                validation,
                candidate.status,
            )
        except EmergencyStopError:
            candidate.status = CandidateStatus.BLOCKED
            self.manager.save(candidate, record)
            raise
        except Exception:
            candidate.status = CandidateStatus.NEEDS_HUMAN_REVIEW
            self.manager.save(candidate, record)
            raise

    async def _pre_save_guard(
        self, record: ProjectRecord, candidate: CandidateRecord, identity: LiveIdentity
    ) -> None:
        self.manager.verify_base(record)
        self.manager._assert_candidate_path(record, candidate.model_path)
        if candidate.status is not CandidateStatus.EDITED:
            raise CandidateSandboxError("Candidateが編集済み状態ではないため保存できません")
        if sha256_file(candidate.model_path) != candidate.initial_sha256:
            raise CandidateSandboxError("Save前にCandidateファイルが予期せず変化しました")
        await self._ensure_not_stopped()
        await self.pc_control.focus_cubism()
        await self._guard_candidate_identity(candidate, identity)
        self.manager.verify_base(record)

    async def _wait_for_saved_hash(self, candidate: CandidateRecord) -> str:
        last_hash: str | None = None
        stable_reads = 0
        for _poll in range(self._HASH_MAX_POLLS):
            await self._ensure_not_stopped()
            current = sha256_file(candidate.model_path)
            if current != candidate.initial_sha256:
                stable_reads = stable_reads + 1 if current == last_hash else 1
                if stable_reads >= self._HASH_STABLE_READS:
                    return current
                last_hash = current
            await asyncio.sleep(self._HASH_POLL_SECONDS)
        raise CandidateSandboxError("Candidate保存後のSHA-256変化と安定化を確認できません")

    async def _read_candidate_identity(self, candidate: CandidateRecord) -> LiveIdentity:
        model_uid = await self.cubism.get_model_uid()
        document_uid, model_path = self._document_for_model(await self.cubism.get_documents(), model_uid)
        if not self._same_path(model_path, candidate.model_path):
            raise IdentityMismatchError("Cubismで開いている文書がCandidateコピーと一致しません")
        return LiveIdentity(model_uid, document_uid, await self.cubism.get_current_edit_mode(), model_path)

    async def _wait_for_candidate_identity(self, candidate: CandidateRecord) -> LiveIdentity:
        """Allow Cubism's asynchronous document switch to become observable.

        This bounded loop only reads identity. It never retries opening,
        editing, or focusing Cubism; a wrong document remains a hard stop.
        """
        last_error: IdentityMismatchError | None = None
        for attempt in range(self._OPEN_IDENTITY_MAX_POLLS):
            try:
                return await self._read_candidate_identity(candidate)
            except IdentityMismatchError as error:
                last_error = error
                if attempt + 1 < self._OPEN_IDENTITY_MAX_POLLS:
                    await asyncio.sleep(self._OPEN_IDENTITY_POLL_SECONDS)
        assert last_error is not None
        raise last_error

    async def _guard_candidate_identity(self, candidate: CandidateRecord, identity: LiveIdentity) -> None:
        if await self.cubism.get_model_uid() != identity.model_uid:
            raise IdentityMismatchError("Candidate保存前にmodel UIDが変化しました")
        if await self.cubism.get_current_edit_mode() != identity.edit_mode:
            raise IdentityMismatchError("Candidate保存前にCubismの編集モードが変化しました")
        document_uid, model_path = self._document_for_model(await self.cubism.get_documents(), identity.model_uid)
        if document_uid != identity.document_uid or not self._same_path(model_path, candidate.model_path):
            raise IdentityMismatchError("Candidate保存前にdocument UIDまたは対象文書が変化しました")

    async def _select_editable_part(self, model_uid: str) -> tuple[str, str]:
        structure = await self.cubism.get_part_structure(model_uid)
        for part_id in self._part_ids(structure.get("PartStructure")):
            object_data = await self.cubism.get_object(model_uid, part_id)
            value = self._label_color(object_data)
            if value is not None:
                return part_id, value
        raise CandidateSandboxError("LabelColorTypeを持つ編集可能なPartが見つかりません")

    async def _ensure_not_stopped(self) -> None:
        status = await self.pc_control.get_status()
        if status.get("control_stopped") or status.get("emergency_stop_file_exists"):
            raise EmergencyStopError("Windows PC Control MCPは緊急停止中です")

    @staticmethod
    def _validation_passed(report: ValidationReport) -> bool:
        return (
            report.all_restored
            and report.restore_readback
            and report.source_hash_unchanged
            and report.working_hash_unchanged
            and report.target_hash_unchanged
            and report.summary.get("FAIL", 0) == 0
        )

    @classmethod
    def _part_ids(cls, value: Any) -> list[str]:
        if not isinstance(value, dict):
            return []
        result: list[str] = []
        item_id = value.get("Id")
        if value.get("Type") == "Part" and isinstance(item_id, str) and not item_id.startswith("%"):
            result.append(item_id)
        children = value.get("Children", value.get("Entries", []))
        if isinstance(children, list):
            for child in children:
                result.extend(cls._part_ids(child))
        return result

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
        return str(left.resolve()).casefold() == str(right.resolve()).casefold()
