"""Beginner-facing, safety-first commands for Phase 1."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .candidates import CandidateManager, CandidateSandbox
from .config import ConfigurationError, McpConfiguration
from .edit_transaction import SafeEditTransaction
from .live_adapters import CubismExternalEditAdapter, WindowsPcControlAdapter
from .mcp_client import McpClientError, open_mcp_session
from .models import ProjectRecord, WorkflowState
from .probe import ProbeError, SafeParameterProbe
from .storage import JsonProjectStore
from .validation import AutomaticModelValidator
from .workspace import ProjectWorkspace, WorkspaceError, sha256_file


def status_payload() -> dict[str, str]:
    """Report only the shipped capability, never an unverified live state."""
    return {
        "RigPilot": "準備完了（Phase 1）",
        "Live Verification": "公式サンプルで確認済み",
        "Cubism MCP": "設定後に doctor で確認",
        "Windows PC Control MCP": "設定後に doctor で確認",
        "Safe Parameter Probe": "実装済み（公式サンプルで確認済み）",
    }


def _print_status(*, as_json: bool) -> None:
    payload = status_payload()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for name, value in payload.items():
        print(f"{name}: {value}")


async def _live_status(config_path: Path) -> dict[str, str]:
    config = McpConfiguration.load(config_path)
    async with open_mcp_session(config.cubism_server()) as cubism_session, open_mcp_session(config.pc_control_server()) as pc_session:
        cubism = CubismExternalEditAdapter(cubism_session)
        pc = WindowsPcControlAdapter(pc_session)
        await cubism.verify_schema()
        await pc.verify_schema()
        status = await cubism.get_status()
        result = {
            "RigPilot Workspace": "OK",
            "Cubism MCP": "OK",
            "Cubism Editor": "OK" if status.get("connected") and status.get("registered") else "要確認",
            "Allow": "OK" if status.get("approved") else "要確認",
            "Edit": "NOT REQUIRED",
            "Windows PC Control MCP": "OK",
            "Emergency Stop": "ON" if await pc.is_emergency_stopped() else "OFF",
        }
        try:
            await pc.find_cubism_window()
        except Exception:  # noqa: BLE001
            result["Cubism Window"] = "要確認"
        else:
            result["Cubism Window"] = "OK"
        if status.get("approved"):
            try:
                result["Model"] = "OK" if await cubism.get_model_uid() else "要確認"
            except McpClientError:
                result["Model"] = "要確認"
        else:
            result["Model"] = "未確認"
        return result


def _project_doctor_payload(project_file: Path | None) -> tuple[dict[str, str], ProjectRecord | None, str | None]:
    result = {
        "RigPilot": "OK", "Workspace": "未指定", "Official Sample": "未指定",
        "Test Model": "未指定", "Working Copy": "未指定",
    }
    if project_file is None:
        return result, None, "公式サンプルを用意した後、rigpilot init で安全な作業コピーを作成してください。"
    try:
        record = JsonProjectStore().load(project_file)
    except (OSError, ValueError, json.JSONDecodeError):
        result.update({"Workspace": "要確認", "Official Sample": "要確認", "Test Model": "要確認", "Working Copy": "要確認"})
        return result, None, f"project.jsonを確認してください: {project_file}"
    source_ok = record.source_model.is_file() and sha256_file(record.source_model) == record.source_sha256
    working_ok = record.working_model.is_file() and sha256_file(record.working_model) == record.working_sha256
    original_ok = (
        record.original_model is not None
        and record.original_sha256 is not None
        and record.original_model.is_file()
        and sha256_file(record.original_model) == record.original_sha256
    )
    result["Workspace"] = "OK" if record.root.is_dir() else "要確認"
    result["Official Sample"] = "OK" if original_ok else "要確認"
    result["Test Model"] = "OK" if source_ok else "要確認"
    result["Working Copy"] = "OK" if working_ok else "要確認"
    if not original_ok:
        return result, record, "公式サンプル原本を指定して、Phase 0B.1用の新しい作業コピーを作成してください。"
    if not source_ok:
        return result, record, "sourceコピーのSHA-256が一致しません。Probeは実行しません。"
    if not working_ok:
        return result, record, "workingコピーのSHA-256が一致しません。Probeは実行しません。"
    return result, record, None


def _doctor_payload(project_file: Path | None, config_path: Path) -> tuple[dict[str, str], str | None]:
    result, _record, next_action = _project_doctor_payload(project_file)
    if next_action is not None:
        result.update({"Cubism MCP": "未確認", "PC Control MCP": "未確認", "Safe Probe": "待機中"})
        return result, next_action
    try:
        live = asyncio.run(_live_status(config_path))
    except ConfigurationError:
        result.update({"Cubism MCP": "AWAITING USER ACTION（未設定）", "PC Control MCP": "AWAITING USER ACTION（未設定）", "Safe Probe": "待機中"})
        return result, "rigpilot setup を実行し、Windows PC Control MCPの場所を確認してください。"
    except (McpClientError, OSError, ValueError):
        result.update({"Cubism MCP": "AWAITING USER ACTION", "PC Control MCP": "AWAITING USER ACTION", "Safe Probe": "待機中"})
        return result, "Live2D Cubismを起動してworkingコピーを開き、外部アプリ連携のAllowを承認してください。"
    result.update(live)
    if live.get("Emergency Stop") == "ON":
        result["Safe Probe"] = "停止中"
        return result, "Windows PC Control MCPが緊急停止中です。原因を確認するまで再開しないでください。"
    if live.get("Cubism Editor") != "OK":
        result["Safe Probe"] = "待機中"
        return result, "Live2D Cubismを起動してください。"
    if live.get("Allow") != "OK":
        result["Safe Probe"] = "AWAITING USER ACTION"
        return result, "Cubism側で外部アプリ連携のAllowを承認してください。Editは不要です。"
    if live.get("Model") != "OK":
        result["Safe Probe"] = "待機中"
        return result, "RigPilotのworkingコピーをCubismで開いてください。"
    result["Safe Probe"] = "READY" if next_action is None else "待機中"
    return result, next_action


def _print_live_status(config_path: Path, *, as_json: bool) -> None:
    payload = asyncio.run(_live_status(config_path))
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for name, value in payload.items():
        print(f"{name}: {value}")


async def _run_probe(project_file: Path, config_path: Path) -> Any:
    record = JsonProjectStore().load(project_file)
    config = McpConfiguration.load(config_path)
    async with open_mcp_session(config.cubism_server()) as cubism_session, open_mcp_session(config.pc_control_server()) as pc_session:
        return await SafeParameterProbe(CubismExternalEditAdapter(cubism_session), WindowsPcControlAdapter(pc_session)).run(record)


async def _run_validation(project_file: Path, config_path: Path, *, dry_run: bool) -> Any:
    record = JsonProjectStore().load(project_file)
    config = McpConfiguration.load(config_path)
    async with open_mcp_session(config.cubism_server()) as cubism_session, open_mcp_session(config.pc_control_server()) as pc_session:
        validator = AutomaticModelValidator(CubismExternalEditAdapter(cubism_session), WindowsPcControlAdapter(pc_session))
        return await (validator.dry_run(record) if dry_run else validator.run(record))


async def _run_edit_test(project_file: Path, config_path: Path, *, dry_run: bool) -> Any:
    record = JsonProjectStore().load(project_file)
    config = McpConfiguration.load(config_path)
    async with open_mcp_session(config.cubism_server()) as cubism_session, open_mcp_session(config.pc_control_server()) as pc_session:
        transaction = SafeEditTransaction(CubismExternalEditAdapter(cubism_session), WindowsPcControlAdapter(pc_session))
        return await (transaction.dry_run(record) if dry_run else transaction.run(record))


async def _open_working_model(project_file: Path, config_path: Path) -> None:
    record = JsonProjectStore().load(project_file)
    working = _working_model_for_open(record)
    config = McpConfiguration.load(config_path)
    async with open_mcp_session(config.pc_control_server()) as pc_session:
        pc = WindowsPcControlAdapter(pc_session)
        await pc.verify_schema()
        if await pc.is_emergency_stopped():
            raise ProbeError("Windows PC Control MCPは緊急停止中です")
        await pc.open_allowed_working_model(working)


def _working_model_for_open(record: ProjectRecord) -> Path:
    working = record.working_model.resolve()
    if working.suffix.casefold() != ".cmo3" or not working.is_file() or working.parent != (record.root / "working").resolve():
        raise WorkspaceError("RigPilotのworkingコピーだけを開けます")
    return working


def _print_doctor(project_file: Path | None, config_path: Path, *, as_json: bool) -> tuple[dict[str, str], str | None]:
    payload, next_action = _doctor_payload(project_file, config_path)
    if as_json:
        print(json.dumps({"checks": payload, "next_action": next_action}, ensure_ascii=False, indent=2))
    else:
        for name, value in payload.items():
            print(f"{name}: {value}")
        if next_action:
            print(f"次の操作: {next_action}")
    return payload, next_action


def _discover_pc_control_root() -> Path | None:
    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        return None
    matches = [
        path for path in downloads.glob("windows-pc-control-mcp*")
        if (path / ".venv" / "Scripts" / "python.exe").is_file() and (path / "config.json").is_file()
    ]
    return matches[0] if len(matches) == 1 else None


def _setup(config_path: Path, pc_control_root: Path | None) -> int:
    if config_path.exists():
        print(f"既存設定を変更しません: {config_path}")
        return 0
    root = pc_control_root or _discover_pc_control_root()
    if root is None:
        print("Windows PC Control MCPを一意に見つけられませんでした。--pc-control-root で場所を指定してください。")
        return 3
    config_path.write_text(
        json.dumps({"pc_control_mcp_root": str(root.resolve()), "cubism_port": 22033}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"ローカル設定を作成しました: {config_path}")
    return 0


def _verify_live(project_file: Path, config_path: Path) -> int:
    checks, next_action = _print_doctor(project_file, config_path, as_json=False)
    if checks.get("Safe Probe") != "READY":
        print("実機Probeは開始していません。")
        return 3 if next_action else 2
    report = asyncio.run(_run_probe(project_file, config_path))
    record = JsonProjectStore().load(project_file)
    outcome = "passed" if report.restored and report.restore_readback and report.source_hash_unchanged and report.working_hash_unchanged else "failed"
    AuditLogger(record.root / "logs" / "audit.jsonl").record(
        project_id=record.project_id,
        step="phase_0b1_verify_live",
        adapter="probe",
        operation="official_sample_live_verification",
        outcome=outcome,
        model_uid=record.model_uid,
        document_uid=record.document_uid,
        metadata={
            "phase": "0B.1", "parameter_id": report.parameter_id,
            "restore_readback": report.restore_readback,
            "source_hash_unchanged": report.source_hash_unchanged,
            "working_hash_unchanged": report.working_hash_unchanged,
            "saved": False, "capture_count": report.screenshots_captured,
        },
    )
    print(f"実機検証: {'OK' if outcome == 'passed' else '要確認'}")
    print(f"Parameter: {report.parameter_id}、画面取得: {report.screenshots_captured}回、復元読取り: {'一致' if report.restore_readback else '不一致'}")
    return 0 if outcome == "passed" else 2


def _validate(project_file: Path, config_path: Path, *, dry_run: bool, as_json: bool) -> int:
    # AutomaticModelValidator performs the full safety preflight in this one
    # MCP session.  Do not run Doctor here: a separate session can cause a
    # second Windows/Python approval dialog without adding a safety check.
    report = asyncio.run(_run_validation(project_file, config_path, dry_run=dry_run))
    payload = report.to_dict()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif dry_run:
        print("モデル検査の予定（Parameterは変更していません）")
        for check in payload["checks"]:
            result = check["result"]
            print(f"{check['name']}: {result}")
    else:
        print("RigPilot モデル検査")
        for check in payload["checks"]:
            print(f"{check['name']}: {check['result']}")
        print(f"モデル復元: {'PASS' if report.all_restored and report.restore_readback else 'FAIL'}")
        print(f"ファイル保護: {'PASS' if report.source_hash_unchanged and report.working_hash_unchanged else 'FAIL'}")
        print(f"検査レポート: {report.report_path}")
    return 0 if dry_run or (report.all_restored and report.restore_readback and report.source_hash_unchanged and report.working_hash_unchanged) else 2


def _edit_test(project_file: Path, config_path: Path, *, dry_run: bool, as_json: bool) -> int:
    report = asyncio.run(_run_edit_test(project_file, config_path, dry_run=dry_run))
    payload = report.to_dict()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif dry_run:
        print("編集テスト対象: Part メタデータ")
        print(f"対象Part: {report.plan.target_id}")
        print(f"変更予定: {report.plan.property} ({report.plan.before} → {report.plan.temporary})")
        print("モデルの見た目: 変更しません")
        print("Save: しません")
        print("終了時: 元値へ戻します")
    else:
        print("RigPilot Phase 2A 編集テスト")
        print(f"対象Part: {report.plan.target_id} / {report.plan.property}")
        print(f"一時編集読取り: {'MATCH' if report.edit.matched else 'MISMATCH'}")
        print(f"Rollback読取り: {'MATCH' if report.rollback.matched else 'MISMATCH'}")
        print(f"Object Before/After: {'IDENTICAL' if report.object_before_after_identical else 'DIFFERENT'}")
        print(f"最終Phase 1検査: {'PASS' if report.final_validation else 'FAIL'}")
        print(f"取引レポート: {report.report_path}")
    return 0 if dry_run or (
        report.edit.matched and report.rollback.matched and report.object_before_after_identical
        and report.final_validation is not None and not report.emergency_stop
        and report.source_hash_unchanged and report.working_hash_unchanged and report.original_hash_unchanged is not False
    ) else 2


async def _run_candidate_test(
    project_file: Path, config_path: Path, *, emergency_stop_verified: bool
) -> Any:
    record = JsonProjectStore().load(project_file)
    config = McpConfiguration.load(config_path)
    async with open_mcp_session(config.cubism_server()) as cubism_session, open_mcp_session(config.pc_control_server()) as pc_session:
        sandbox = CandidateSandbox(
            CubismExternalEditAdapter(cubism_session), WindowsPcControlAdapter(pc_session)
        )
        return await sandbox.run(record, emergency_stop_verified=emergency_stop_verified)


async def _run_candidate_open_test(
    project_file: Path, config_path: Path, *, emergency_stop_verified: bool
) -> Any:
    record = JsonProjectStore().load(project_file)
    config = McpConfiguration.load(config_path)
    async with open_mcp_session(config.cubism_server()) as cubism_session, open_mcp_session(config.pc_control_server()) as pc_session:
        sandbox = CandidateSandbox(
            CubismExternalEditAdapter(cubism_session), WindowsPcControlAdapter(pc_session)
        )
        return await sandbox.open_only(record, emergency_stop_verified=emergency_stop_verified)


def _candidate_open_test(
    project_file: Path, config_path: Path, *, emergency_stop_verified: bool, as_json: bool
) -> int:
    """Run only the Phase 2B Candidate Open and identity gate."""
    if not emergency_stop_verified:
        print("Candidate Open実機確認は開始していません。")
        print("先にWindows PC Control MCPのEmergency Stopを手動確認し、--confirm-emergency-stop を付けて再実行してください。")
        return 3
    report = asyncio.run(_run_candidate_open_test(project_file, config_path, emergency_stop_verified=True))
    payload = {
        "phase": "2B",
        "stage": "candidate_open_identity",
        "candidate_id": report.candidate_id,
        "candidate_path": str(report.candidate_path),
        "candidate_sha256": report.candidate_sha256,
        "source_hash_unchanged": report.source_hash_unchanged,
        "working_hash_unchanged": report.working_hash_unchanged,
        "model_uid": report.model_uid,
        "document_uid": report.document_uid,
        "edit": "NOT RUN",
        "save": "NOT RUN",
        "validation": "NOT RUN",
        "promoted": False,
        "final_status": report.final_status.value,
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("RigPilot Phase 2B Candidate Open")
        print(f"Candidate: {report.candidate_path}")
        print(f"Document UID: {report.document_uid}")
        print("Edit / Save / Validation: NOT RUN")
    return 0


def _candidate_test(
    project_file: Path,
    config_path: Path,
    *,
    dry_run: bool,
    emergency_stop_verified: bool,
    as_json: bool,
) -> int:
    """Plan candidate-only work without opening Cubism or writing files.

    The real save stage remains deliberately unavailable until the configured
    PC Control emergency-stop registration has been manually verified.  This
    prevents a candidate save from becoming an unguarded first interaction.
    """
    record = JsonProjectStore().load(project_file)
    if dry_run:
        plan = CandidateManager().plan(record)
        payload = {
            "phase": "2B",
            "dry_run": True,
            "base_path": str(plan.base_path),
            "base_sha256": plan.base_sha256,
            "candidate_id": plan.candidate_id,
            "candidate_path": str(plan.candidate_path),
            "allowed_edit": "Part LabelColorType only",
            "save_target": "Candidate only",
            "source_and_working": "UNCHANGED",
            "export": "NOT PERFORMED",
            "filesystem_writes": 0,
        }
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("RigPilot Phase 2B Candidate Sandbox 計画")
            print(f"Base: {plan.base_path}")
            print(f"Base SHA-256: {plan.base_sha256}")
            print(f"Candidate: {plan.candidate_path}")
            print("編集予定: Part LabelColorType のみ")
            print("Save先: Candidate のみ")
            print("source / working: 変更しません")
            print("Export: 実行しません")
        return 0
    if not emergency_stop_verified:
        print("Candidate実機保存は開始していません。")
        print("先にWindows PC Control MCPのEmergency Stopを手動確認し、--confirm-emergency-stop を付けて再実行してください。")
        return 3
    report = asyncio.run(_run_candidate_test(project_file, config_path, emergency_stop_verified=True))
    payload = {
        "phase": "2B",
        "candidate_id": report.candidate_id,
        "candidate_path": str(report.candidate_path),
        "candidate_sha256": report.candidate_sha256,
        "source_hash_unchanged": report.source_hash_unchanged,
        "working_hash_unchanged": report.working_hash_unchanged,
        "candidate_validation": report.validation.to_dict(),
        "final_status": report.final_status.value,
        "promoted": False,
    }
    AuditLogger(record.root / "logs" / "audit.jsonl").record(
        project_id=record.project_id,
        step="phase_2b_candidate_sandbox",
        adapter="candidate_sandbox",
        operation="candidate_only_label_color_save_and_validation",
        outcome="success",
        model_uid=report.validation.model_uid,
        document_uid=report.validation.document_uid,
        metadata={
            "candidate_id": report.candidate_id,
            "candidate_sha256": report.candidate_sha256,
            "source_hash_unchanged": report.source_hash_unchanged,
            "working_hash_unchanged": report.working_hash_unchanged,
            "final_status": report.final_status.value,
            "promoted": False,
        },
    )
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("RigPilot Phase 2B Candidate Sandbox")
        print(f"Candidate: {report.candidate_path}")
        print(f"Candidate SHA-256: {report.candidate_sha256}")
        print(f"Candidate検査: {'PASS' if report.validation.all_restored else 'FAIL'}")
        print("working/source: UNCHANGED" if report.source_hash_unchanged and report.working_hash_unchanged else "working/source: CHANGED")
        print("Promote: 実行しません（Candidateは保持してREJECTEDにします）")
    return 0


def _resume_after_review(project_file: Path) -> None:
    record = JsonProjectStore().load(project_file)
    if record.state not in {WorkflowState.FAILED, WorkflowState.NEEDS_HUMAN_REVIEW}:
        raise WorkspaceError("failedまたはneeds_human_review状態のプロジェクトだけを再開できます")
    if sha256_file(record.source_model) != record.source_sha256 or sha256_file(record.working_model) != record.working_sha256:
        raise WorkspaceError("sourceまたはworkingコピーのSHA-256が一致しないため再開できません")
    if (
        record.original_model is not None
        and record.original_sha256 is not None
        and (not record.original_model.is_file() or sha256_file(record.original_model) != record.original_sha256)
    ):
        raise WorkspaceError("公式サンプル原本のSHA-256が一致しないため再開できません")
    record.state = WorkflowState.PAUSED
    JsonProjectStore().save(record)
    AuditLogger(record.root / "logs" / "audit.jsonl").record(
        project_id=record.project_id,
        step="review_acknowledged",
        adapter="workspace",
        operation="resume_after_human_review",
        outcome="success",
        model_uid=record.model_uid,
        document_uid=record.document_uid,
        metadata={"saved": False, "source_hash_unchanged": True, "working_hash_unchanged": True},
    )


def _create_project(*, workspace: Path, project_id: str, model: Path) -> ProjectRecord:
    record = ProjectWorkspace(workspace).create_project(project_id, model)
    JsonProjectStore().save(record)
    AuditLogger(record.root / "logs" / "audit.jsonl").record(
        project_id=record.project_id,
        step="initialize",
        adapter="workspace",
        operation="copy_source",
        outcome="success",
        model_uid=None,
        document_uid=None,
    )
    return record


def _print_project_status(project_file: Path, *, as_json: bool) -> None:
    record = JsonProjectStore().load(project_file)
    source_unchanged = sha256_file(record.source_model) == record.source_sha256
    payload: dict[str, Any] = {
        "project_id": record.project_id,
        "state": record.state.value,
        "source_model": str(record.source_model),
        "source_integrity": "OK" if source_unchanged else "CHANGED",
        "working_model": str(record.working_model),
        "identity_checked": bool(record.model_uid and record.document_uid),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"プロジェクト: {payload['project_id']}")
    print(f"状態: {payload['state']}")
    print(f"元データコピー: {payload['source_integrity']}")
    print(f"作業コピー: {payload['working_model']}")
    print(f"Cubism対象照合: {'済み' if payload['identity_checked'] else '未実施'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rigpilot",
        description="RigPilot Phase 0B: 安全な作業コピーと状態確認を行います。",
    )
    subcommands = parser.add_subparsers(dest="command")

    status = subcommands.add_parser("status", help="現在実装されている接続範囲を表示します。")
    status.add_argument("--json", action="store_true", help="機械可読なJSONで表示します。")
    status.add_argument("--live", action="store_true", help="設定済みの実MCPへ読み取り接続します。")
    status.add_argument("--config", type=Path, default=Path("rigpilot.local.json"), help="ローカルMCP設定ファイル")

    initialize = subcommands.add_parser(
        "init", help="元の.cmo3を読取り、RigPilot用の安全なコピーを作成します。"
    )
    initialize.add_argument("--workspace", type=Path, default=Path("projects"), help="RigPilotプロジェクトの保存先")
    initialize.add_argument("--project-id", required=True, help="プロジェクト名（例: mia-check）")
    initialize.add_argument("--model", required=True, type=Path, help="元の.cmo3へのパス")

    project_status = subcommands.add_parser("project-status", help="作成済みプロジェクトの安全状態を表示します。")
    project_status.add_argument("--project", required=True, type=Path, help="project.jsonへのパス")
    project_status.add_argument("--json", action="store_true", help="機械可読なJSONで表示します。")

    probe = subcommands.add_parser("probe", help="作業コピーに対して一時Parameter Probeを実行します。")
    probe.add_argument("--project", required=True, type=Path, help="project.jsonへのパス")
    probe.add_argument("--config", type=Path, default=Path("rigpilot.local.json"), help="ローカルMCP設定ファイル")

    setup = subcommands.add_parser("setup", help="既存設定を壊さずにローカルMCP設定を作成します。")
    setup.add_argument("--config", type=Path, default=Path("rigpilot.local.json"), help="作成するローカル設定ファイル")
    setup.add_argument("--pc-control-root", type=Path, help="Windows PC Control MCPのフォルダー")

    doctor = subcommands.add_parser("doctor", help="実機検証の準備状態と次の1操作を表示します。")
    doctor.add_argument("--project", type=Path, help="project.jsonへのパス")
    doctor.add_argument("--config", type=Path, default=Path("rigpilot.local.json"), help="ローカルMCP設定ファイル")
    doctor.add_argument("--json", action="store_true", help="機械可読なJSONで表示します。")

    verify = subcommands.add_parser("verify-live", help="公式サンプルの安全な実機検証を一括実行します。")
    verify.add_argument("--project", required=True, type=Path, help="公式サンプルから作ったproject.jsonへのパス")
    verify.add_argument("--config", type=Path, default=Path("rigpilot.local.json"), help="ローカルMCP設定ファイル")

    validate = subcommands.add_parser("validate", help="モデルの基本動作を安全に一巡検査します。")
    validate.add_argument("--project", required=True, type=Path, help="project.jsonへのパス")
    validate.add_argument("--config", type=Path, default=Path("rigpilot.local.json"), help="ローカルMCP設定ファイル")
    validate.add_argument("--dry-run", action="store_true", help="Parameterを変更せず、検査予定だけを表示します。")
    validate.add_argument("--json", action="store_true", help="機械可読なJSONで表示します。")

    edit_test = subcommands.add_parser("edit-test", help="Phase 2Aの可逆なPartメタデータ編集を検査します。")
    edit_test.add_argument("--project", required=True, type=Path, help="project.jsonへのパス")
    edit_test.add_argument("--config", type=Path, default=Path("rigpilot.local.json"), help="ローカルMCP設定ファイル")
    edit_test.add_argument("--dry-run", action="store_true", help="編集せず、対象と取引計画だけを確認します。")
    edit_test.add_argument("--json", action="store_true", help="機械可読なJSONで表示します。")

    candidate_test = subcommands.add_parser(
        "candidate-test", help="Phase 2Bの候補モデル隔離・保存計画を確認します。"
    )
    candidate_test.add_argument("--project", required=True, type=Path, help="project.jsonへのパス")
    candidate_test.add_argument("--config", type=Path, default=Path("rigpilot.local.json"), help="ローカルMCP設定ファイル")
    candidate_test.add_argument("--dry-run", action="store_true", help="ファイルもMCPも変更せず、Candidate計画だけを表示します。")
    candidate_test.add_argument(
        "--confirm-emergency-stop",
        action="store_true",
        help="Windows PC Control MCPのEmergency Stopを手動確認済みであることを明示します。",
    )
    candidate_test.add_argument("--json", action="store_true", help="機械可読なJSONで表示します。")

    candidate_open_test = subcommands.add_parser(
        "candidate-open-test", help="Phase 2BのCandidate OpenとIdentityだけを実機確認します。"
    )
    candidate_open_test.add_argument("--project", required=True, type=Path, help="project.jsonへのパス")
    candidate_open_test.add_argument("--config", type=Path, default=Path("rigpilot.local.json"), help="ローカルMCP設定ファイル")
    candidate_open_test.add_argument(
        "--confirm-emergency-stop",
        action="store_true",
        help="Windows PC Control MCPのEmergency Stopを手動確認済みであることを明示します。",
    )
    candidate_open_test.add_argument("--json", action="store_true", help="機械可読なJSONで表示します。")

    open_working = subcommands.add_parser("open-working", help="安全なworkingコピーを既定アプリで開きます。")
    open_working.add_argument("--project", required=True, type=Path, help="project.jsonへのパス")
    open_working.add_argument("--config", type=Path, default=Path("rigpilot.local.json"), help="ローカルMCP設定ファイル")

    resume_review = subcommands.add_parser("resume-review", help="確認済みの停止状態を安全に再開待機へ戻します。")
    resume_review.add_argument("--project", required=True, type=Path, help="project.jsonへのパス")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "status") and not getattr(args, "live", False):
        _print_status(as_json=bool(getattr(args, "json", False)))
        return 0
    try:
        if args.command == "status":
            _print_live_status(args.config, as_json=args.json)
            return 0
        if args.command == "init":
            record = _create_project(workspace=args.workspace, project_id=args.project_id, model=args.model)
            print("安全な作業コピーを作成しました。")
            print(f"プロジェクト: {record.project_id}")
            print(f"元データコピー: {record.source_model}")
            print(f"作業コピー: {record.working_model}")
            return 0
        if args.command == "project-status":
            _print_project_status(args.project, as_json=args.json)
            return 0
        if args.command == "probe":
            report = asyncio.run(_run_probe(args.project, args.config))
            print(f"Probe完了: {report.parameter_id}、画面取得 {report.screenshots_captured}回、復元: {'OK' if report.restored else '失敗'}")
            return 0
        if args.command == "setup":
            return _setup(args.config, args.pc_control_root)
        if args.command == "doctor":
            _print_doctor(args.project, args.config, as_json=args.json)
            return 0
        if args.command == "verify-live":
            return _verify_live(args.project, args.config)
        if args.command == "validate":
            return _validate(args.project, args.config, dry_run=args.dry_run, as_json=args.json)
        if args.command == "edit-test":
            return _edit_test(args.project, args.config, dry_run=args.dry_run, as_json=args.json)
        if args.command == "candidate-test":
            return _candidate_test(
                args.project,
                args.config,
                dry_run=args.dry_run,
                emergency_stop_verified=args.confirm_emergency_stop,
                as_json=args.json,
            )
        if args.command == "candidate-open-test":
            return _candidate_open_test(
                args.project,
                args.config,
                emergency_stop_verified=args.confirm_emergency_stop,
                as_json=args.json,
            )
        if args.command == "open-working":
            asyncio.run(_open_working_model(args.project, args.config))
            print("workingコピーを開く要求を送信しました。Windowsの確認が出た場合は内容を確認して承認してください。")
            return 0
        if args.command == "resume-review":
            _resume_after_review(args.project)
            print("確認済みとして再開待機へ戻しました。モデルやファイルは変更していません。")
            return 0
    except (OSError, ValueError, WorkspaceError, ConfigurationError, McpClientError, ProbeError) as error:
        print(f"RigPilotを実行できませんでした: {error}")
        print("元のモデルは変更していません。パスとファイル名を確認して、もう一度実行してください。")
        return 2
    parser.error("未対応のコマンドです。")
    return 2
