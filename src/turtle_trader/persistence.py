"""Local SQLite storage for deterministic offline research runs."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import closing
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any


def _encode(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _encode(asdict(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal values cannot be persisted")
        return {"__type__": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, Path):
        return {"__type__": "path", "value": str(value)}
    if isinstance(value, Enum):
        return _encode(value.value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("persisted mapping keys must be strings")
        return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite float values cannot be persisted")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported persisted value: {type(value).__name__}")


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict):
        kind = value.get("__type__")
        raw = value.get("value")
        if kind == "decimal":
            if not isinstance(raw, str):
                raise ValueError("invalid persisted decimal value")
            return Decimal(raw)
        if kind == "datetime":
            if not isinstance(raw, str):
                raise ValueError("invalid persisted datetime value")
            return datetime.fromisoformat(raw)
        if kind == "date":
            if not isinstance(raw, str):
                raise ValueError("invalid persisted date value")
            return date.fromisoformat(raw)
        if kind == "path":
            if not isinstance(raw, str):
                raise ValueError("invalid persisted path value")
            return Path(raw)
        return {key: _decode(item) for key, item in value.items()}
    return value


def _json(value: Any) -> str:
    return json.dumps(_encode(value), allow_nan=False, separators=(",", ":"), sort_keys=True)


class RunStore:
    """SQLite-backed run and audit-record store. Each saved run is atomic."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        # Inspect existing databases without opening a write transaction. In particular,
        # do not stamp a legacy database with the new version or create tables in it.
        if self.path.exists() and self.path.stat().st_size:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as existing:
                version = existing.execute("PRAGMA user_version").fetchone()[0]
                columns = existing.execute("PRAGMA table_info(runs)").fetchall()
                if version != 2 or [row[1] for row in columns] != [
                    "run_id", "kind", "config_json", "data_hash", "result_json",
                    "source_hash", "created_at",
                ]:
                    raise ValueError("incompatible run database; choose a new version-2 database")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    data_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )"""
            )
            connection.execute("PRAGMA user_version = 2")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def save(
        self, kind: str, config: object, data_hash: str, result: object, source_hash: str
    ) -> str:
        if not kind:
            raise ValueError("kind must not be empty")
        config_json, result_json = _json(config), _json(result)
        run_id = str(uuid.uuid4())
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, kind, config_json, data_hash, result_json, source_hash,
                 datetime.now().astimezone().isoformat()),
            )
        return run_id

    def load(self, run_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown run_id: {run_id}")
        return {
            "schema_version": 2,
            "run_id": row["run_id"], "kind": row["kind"],
            "config": _decode(json.loads(row["config_json"])), "data_hash": row["data_hash"],
            "result": _decode(json.loads(row["result_json"])),
            "source_hash": row["source_hash"], "created_at": row["created_at"],
        }

    def list_runs(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT run_id, kind, data_hash, source_hash, created_at "
                "FROM runs ORDER BY created_at, run_id"
            ).fetchall()
        return [dict(row) for row in rows]
