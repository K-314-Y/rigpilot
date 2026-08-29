"""Small, beginner-facing Phase 0B commands with no live MCP side effects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .audit import AuditLogger
from .models import ProjectRecord
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

    initialize = subcommands.add_parser(
        "init", help="元の.cmo3を読取り、RigPilot用の安全なコピーを作成します。"
    )
    initialize.add_argument("--workspace", type=Path, default=Path("projects"), help="RigPilotプロジェクトの保存先")
    initialize.add_argument("--project-id", required=True, help="プロジェクト名（例: mia-check）")
    initialize.add_argument("--model", required=True, type=Path, help="元の.cmo3へのパス")

    project_status = subcommands.add_parser("project-status", help="作成済みプロジェクトの安全状態を表示します。")
    project_status.add_argument("--project", required=True, type=Path, help="project.jsonへのパス")
    project_status.add_argument("--json", action="store_true", help="機械可読なJSONで表示します。")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "status"):
        _print_status(as_json=bool(getattr(args, "json", False)))
        return 0
    try:
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
    except (OSError, ValueError, WorkspaceError) as error:
        print(f"RigPilotを実行できませんでした: {error}")
        print("元のモデルは変更していません。パスとファイル名を確認して、もう一度実行してください。")
        return 2
    parser.error("未対応のコマンドです。")
    return 2
