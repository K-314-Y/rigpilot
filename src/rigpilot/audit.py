"""Append-only JSON-lines audit logging without credentials or screenshot bytes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SENSITIVE_KEYS = {"token", "authorization", "password", "secret", "screenshot", "image"}


def _safe_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    return {
        key: "[redacted]" if key.lower() in _SENSITIVE_KEYS else item
        for key, item in (value or {}).items()
    }


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        project_id: str,
        step: str,
        adapter: str,
        operation: str,
        outcome: str,
        model_uid: str | None,
        document_uid: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "project_id": project_id,
            "step": step,
            "adapter": adapter,
            "operation": operation,
            "outcome": outcome,
            "model_uid": model_uid,
            "document_uid": document_uid,
            "metadata": _safe_metadata(metadata),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
