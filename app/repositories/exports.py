from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.database import connect, transaction


ACTIVE_STATUSES = frozenset({"queued", "snapshotting", "building", "validating"})
TERMINAL_STATUSES = frozenset({"completed", "failed"})
ALLOWED_TRANSITIONS = {
    "queued": frozenset({"snapshotting", "failed"}),
    "snapshotting": frozenset({"building", "failed"}),
    "building": frozenset({"validating", "failed"}),
    "validating": frozenset({"completed", "failed"}),
    "failed": frozenset({"queued"}),
    "completed": frozenset(),
}


@dataclass(frozen=True, slots=True)
class ExportArtifactRecord:
    id: int
    artifact_kind: str
    filename: str
    sha256: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class ExportJobRecord:
    id: int
    project_version_id: int
    idempotency_key: str
    status: str
    progress_step: str | None
    requested_at: str
    started_at: str | None
    finished_at: str | None
    output_dir: str | None
    error: dict | None
    artifacts: tuple[ExportArtifactRecord, ...] = ()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _job_from_row(row: aiosqlite.Row, artifacts: tuple[ExportArtifactRecord, ...] = ()) -> ExportJobRecord:
    return ExportJobRecord(
        id=int(row["id"]), project_version_id=int(row["project_version_id"]),
        idempotency_key=row["idempotency_key"], status=row["status"],
        progress_step=row["progress_step"], requested_at=row["requested_at"],
        started_at=row["started_at"], finished_at=row["finished_at"],
        output_dir=row["output_dir"], error=json.loads(row["error_json"]) if row["error_json"] else None,
        artifacts=artifacts,
    )


async def _artifacts(connection: aiosqlite.Connection, job_id: int) -> tuple[ExportArtifactRecord, ...]:
    rows = await (await connection.execute(
        "SELECT id, artifact_kind, filename, sha256, byte_size FROM export_artifacts WHERE export_job_id=? ORDER BY id", (job_id,)
    )).fetchall()
    return tuple(ExportArtifactRecord(int(row["id"]), row["artifact_kind"], row["filename"], row["sha256"], int(row["byte_size"])) for row in rows)


async def get_job(database_path: Path, job_id: int) -> ExportJobRecord | None:
    async with connect(database_path) as connection:
        row = await (await connection.execute("SELECT * FROM export_jobs WHERE id=?", (job_id,))).fetchone()
        return None if row is None else _job_from_row(row, await _artifacts(connection, job_id))


async def get_job_by_idempotency(database_path: Path, project_version_id: int, key: str) -> ExportJobRecord | None:
    async with connect(database_path) as connection:
        row = await (await connection.execute(
            "SELECT * FROM export_jobs WHERE project_version_id=? AND idempotency_key=?", (project_version_id, key)
        )).fetchone()
        return None if row is None else _job_from_row(row, await _artifacts(connection, int(row["id"])))


async def create_job(database_path: Path, project_version_id: int, idempotency_key: str) -> ExportJobRecord:
    async with connect(database_path) as connection:
        async with transaction(connection):
            exists = await (await connection.execute("SELECT 1 FROM project_versions WHERE id=?", (project_version_id,))).fetchone()
            if exists is None:
                raise LookupError("PROJECT_VERSION_NOT_FOUND")
            cursor = await connection.execute(
                "INSERT INTO export_jobs(project_version_id,idempotency_key,status,progress_step,requested_at) VALUES(?,?,?,?,?)",
                (project_version_id, idempotency_key, "queued", "queued", _now()),
            )
            row = await (await connection.execute("SELECT * FROM export_jobs WHERE id=?", (cursor.lastrowid,))).fetchone()
    return _job_from_row(row)


async def transition_job(
    database_path: Path, job_id: int, expected: frozenset[str], target: str, *, progress_step: str,
    output_dir: str | None = None, error: dict | None = None,
) -> ExportJobRecord | None:
    if target not in ACTIVE_STATUSES | TERMINAL_STATUSES:
        raise ValueError(f"Unknown export job status: {target}")
    if not expected or any(target not in ALLOWED_TRANSITIONS.get(status, frozenset()) for status in expected):
        raise ValueError(f"Illegal export job transition from {sorted(expected)} to {target}")
    now = _now()
    set_parts = ["status=?", "progress_step=?"]
    values: list[object] = [target, progress_step]
    if target == "snapshotting":
        set_parts.append("started_at=COALESCE(started_at, ?)"); values.append(now)
    if output_dir is not None:
        set_parts.append("output_dir=?"); values.append(output_dir)
    if target in TERMINAL_STATUSES:
        set_parts.append("finished_at=?"); values.append(now)
    if error is not None:
        set_parts.append("error_json=?"); values.append(json.dumps(error, ensure_ascii=False, sort_keys=True))
    values.extend([job_id, *sorted(expected)])
    placeholders = ",".join("?" for _ in expected)
    async with connect(database_path) as connection:
        async with transaction(connection):
            cursor = await connection.execute(
                f"UPDATE export_jobs SET {', '.join(set_parts)} WHERE id=? AND status IN ({placeholders})", values
            )
            if cursor.rowcount != 1:
                return None
            row = await (await connection.execute("SELECT * FROM export_jobs WHERE id=?", (job_id,))).fetchone()
    return _job_from_row(row)


async def replace_artifacts(database_path: Path, job_id: int, artifacts: tuple[ExportArtifactRecord, ...]) -> None:
    async with connect(database_path) as connection:
        async with transaction(connection):
            await connection.execute("DELETE FROM export_artifacts WHERE export_job_id=?", (job_id,))
            await connection.executemany(
                "INSERT INTO export_artifacts(export_job_id,artifact_kind,filename,sha256,byte_size) VALUES(?,?,?,?,?)",
                [(job_id, item.artifact_kind, item.filename, item.sha256, item.byte_size) for item in artifacts],
            )


async def mark_incomplete_jobs_failed(database_path: Path) -> int:
    error = json.dumps({"code": "EXPORT_INTERRUPTED", "message": "应用重启前导出作业未完成，请重试。"}, ensure_ascii=False)
    async with connect(database_path) as connection:
        async with transaction(connection):
            cursor = await connection.execute(
                "UPDATE export_jobs SET status='failed', progress_step='failed', finished_at=?, error_json=? WHERE status IN ('queued','snapshotting','building','validating')",
                (_now(), error),
            )
            return cursor.rowcount
