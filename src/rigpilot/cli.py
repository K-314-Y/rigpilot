"""Small, beginner-facing Phase 0B commands with no live MCP side effects."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .config import ConfigurationError, McpConfiguration
from .live_adapters import CubismExternalEditAdapter, WindowsPcControlAdapter
from .mcp_client import McpClientError, open_mcp_session
from .models import ProjectRecord
from .probe import ProbeError, SafeParameterProbe
from .storage import JsonProjectStore
from .workspace import ProjectWorkspace, WorkspaceError, sha256_file


def status_payload() -> dict[str, str]:
    """Report the truth about the shipped Phase 0B integration boundary."""
    return {
        "RigPilot": "準備完了（Phase 0B）",
        "Cubism MCP": "未接続（実機連携は未実装）",
        "Windows PC Control MCP": "未接続（実機連携は未実装）",
        "Cubism Model": "未確認",
        "Parameter Probe": "未実装",
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
            result["Model"] = "OK" if await cubism.get_model_uid() else "要確認"
        else:
            result["Model"] = "未確認"
        return result


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
    except (OSError, ValueError, WorkspaceError, ConfigurationError, McpClientError, ProbeError) as error:
        print(f"RigPilotを実行できませんでした: {error}")
        print("元のモデルは変更していません。パスとファイル名を確認して、もう一度実行してください。")
        return 2
    parser.error("未対応のコマンドです。")
    return 2
