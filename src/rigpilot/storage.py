"""JSON persistence separated from the workflow logic for a later SQLite migration."""

from __future__ import annotations

import json
from pathlib import Path

from .models import ProjectRecord, utc_now


class JsonProjectStore:
    def path_for(self, record: ProjectRecord) -> Path:
        return record.root / "project.json"

    def save(self, record: ProjectRecord) -> None:
        record.updated_at = utc_now()
        destination = self.path_for(record)
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)

    def load(self, path: Path) -> ProjectRecord:
        return ProjectRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
