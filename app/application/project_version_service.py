from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.database import connect, transaction
from app.application.export_storage import (
    ExportStorageError,
    cleanup_abandoned_staging,
    cleanup_quarantine,
    list_quarantines,
    prepare_delete_quarantine,
    restore_quarantine,
)


class ProjectVersionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_ACTIVE_EXPORT_STATUSES = ("queued", "snapshotting", "building", "validating")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _snapshot(summary: dict[str, Any]) -> tuple[str, str]:
    encoded = json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _summary(connection, project_version_id: int) -> dict[str, Any] | None:
    row = await (
        await connection.execute(
            """
            SELECT pv.id AS project_version_id, p.id AS project_id,
                   p.project_code, p.project_name, pv.version_no,
                   pv.source_filename, pv.imported_at,
                   (SELECT COUNT(*) FROM phases ph WHERE ph.project_version_id=pv.id) AS phase_count,
                   (SELECT COUNT(*) FROM config_items ci WHERE ci.project_version_id=pv.id) AS config_item_count,
                   (SELECT COUNT(*) FROM quality_activities qa JOIN phases ph ON ph.id=qa.phase_id WHERE ph.project_version_id=pv.id) AS activity_count,
                   (SELECT COUNT(*) FROM audit_instances ai WHERE ai.project_version_id=pv.id) AS audit_count,
                   (SELECT COUNT(*) FROM checklist_instance_items cii JOIN checklist_instance_groups cig ON cig.id=cii.instance_group_id JOIN audit_instances ai ON ai.id=cig.audit_instance_id WHERE ai.project_version_id=pv.id) AS checklist_item_count,
                   (SELECT COUNT(*) FROM nonconformities nc JOIN checklist_instance_items cii ON cii.id=nc.checklist_instance_item_id JOIN checklist_instance_groups cig ON cig.id=cii.instance_group_id JOIN audit_instances ai ON ai.id=cig.audit_instance_id WHERE ai.project_version_id=pv.id) AS issue_count,
                   (SELECT COUNT(*) FROM export_jobs ej WHERE ej.project_version_id=pv.id) AS export_job_count,
                   (SELECT COUNT(*) FROM export_artifacts ea JOIN export_jobs ej ON ej.id=ea.export_job_id WHERE ej.project_version_id=pv.id) AS artifact_count
            FROM project_versions pv
            JOIN projects p ON p.id=pv.project_id
            WHERE pv.id=?
            """,
            (project_version_id,),
        )
    ).fetchone()
    return dict(row) if row is not None else None


async def list_project_versions(database_path: Path) -> list[dict[str, Any]]:
    async with connect(database_path) as connection:
        ids = await (
            await connection.execute(
                "SELECT id FROM project_versions ORDER BY imported_at DESC, id DESC"
            )
        ).fetchall()
        return [summary for row in ids if (summary := await _summary(connection, int(row["id"]))) is not None]


async def get_project_version(database_path: Path, project_version_id: int) -> dict[str, Any]:
    async with connect(database_path) as connection:
        summary = await _summary(connection, project_version_id)
    if summary is None:
        raise ProjectVersionError("PROJECT_VERSION_NOT_FOUND", "项目数据版本不存在")
    return summary


async def create_delete_preview(
    database_path: Path, project_version_id: int, ttl_seconds: int = 600
) -> dict[str, Any]:
    now = _utc_now()
    async with connect(database_path) as connection:
        async with transaction(connection):
            summary = await _summary(connection, project_version_id)
            if summary is None:
                raise ProjectVersionError("PROJECT_VERSION_NOT_FOUND", "项目数据版本不存在")
            active = await (
                await connection.execute(
                    "SELECT COUNT(*) FROM export_jobs WHERE project_version_id=? AND status IN (?,?,?,?)",
                    (project_version_id, *_ACTIVE_EXPORT_STATUSES),
                )
            ).fetchone()
            if int(active[0]) > 0:
                raise ProjectVersionError("PROJECT_DELETE_EXPORT_ACTIVE", "项目版本仍有导出作业正在运行")
            token = secrets.token_urlsafe(32)
            encoded, digest = _snapshot(summary)
            confirmation_text = f"删除 {summary['project_code']} V{summary['version_no']}"
            expires_at = now + timedelta(seconds=ttl_seconds)
            await connection.execute(
                """
                INSERT INTO project_delete_previews(
                    token_hash, project_version_id, snapshot_sha256, confirmation_text,
                    summary_json, status, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    _token_hash(token), project_version_id, digest, confirmation_text,
                    encoded, expires_at.isoformat(), now.isoformat(),
                ),
            )
    return {
        "preview_token": token,
        "confirmation_text": confirmation_text,
        "expires_at": expires_at.isoformat(),
        "summary": summary,
    }


async def confirm_delete(
    database_path: Path,
    generated_dir: Path,
    project_version_id: int,
    preview_token: str,
    confirmation_text: str,
) -> dict[str, Any]:
    # SQLite and the filesystem cannot share one transaction:
    # manifest -> same-disk quarantine -> SQLite commit -> cleanup.
    # Any pre-commit failure restores immediately; startup repeats recovery.
    now = _utc_now()
    quarantine = None
    try:
        async with connect(database_path) as connection:
            async with transaction(connection):
                preview = await (
                    await connection.execute(
                        "SELECT * FROM project_delete_previews WHERE token_hash=?",
                        (_token_hash(preview_token),),
                    )
                ).fetchone()
                if preview is None or int(preview["project_version_id"]) != project_version_id:
                    raise ProjectVersionError("PROJECT_DELETE_TOKEN_INVALID", "删除确认令牌无效")
                if preview["status"] != "pending":
                    raise ProjectVersionError("PROJECT_DELETE_TOKEN_CONSUMED", "删除确认令牌已使用")
                if datetime.fromisoformat(preview["expires_at"]) <= now:
                    await connection.execute(
                        "UPDATE project_delete_previews SET status='expired' WHERE id=?", (preview["id"],)
                    )
                    raise ProjectVersionError("PROJECT_DELETE_TOKEN_EXPIRED", "删除确认令牌已过期")
                if confirmation_text != preview["confirmation_text"]:
                    raise ProjectVersionError("PROJECT_DELETE_CONFIRMATION_MISMATCH", "删除确认文字不匹配")
                summary = await _summary(connection, project_version_id)
                if summary is None:
                    raise ProjectVersionError("PROJECT_VERSION_NOT_FOUND", "项目数据版本不存在")
                active = await (
                    await connection.execute(
                        "SELECT COUNT(*) FROM export_jobs WHERE project_version_id=? AND status IN (?,?,?,?)",
                        (project_version_id, *_ACTIVE_EXPORT_STATUSES),
                    )
                ).fetchone()
                if int(active[0]) > 0:
                    raise ProjectVersionError("PROJECT_DELETE_EXPORT_ACTIVE", "项目版本仍有导出作业正在运行")
                _encoded, digest = _snapshot(summary)
                if digest != preview["snapshot_sha256"]:
                    raise ProjectVersionError("PROJECT_DELETE_PREVIEW_STALE", "项目数据已变化，请重新预览后删除")
                output_rows = await (
                    await connection.execute(
                        "SELECT DISTINCT output_dir FROM export_jobs "
                        "WHERE project_version_id=? AND output_dir IS NOT NULL",
                        (project_version_id,),
                    )
                ).fetchall()
                try:
                    quarantine = prepare_delete_quarantine(
                        generated_dir,
                        project_version_id,
                        (str(row["output_dir"]) for row in output_rows),
                    )
                except ExportStorageError as exc:
                    raise ProjectVersionError(exc.code, str(exc)) from exc
                project_id = int(summary["project_id"])
                await _delete_version_rows(connection, project_version_id, project_id)
    except BaseException:
        if quarantine is not None:
            try:
                restore_quarantine(generated_dir, quarantine)
            except ExportStorageError as recovery_exc:
                raise ProjectVersionError(
                    "PROJECT_DELETE_STORAGE_RECOVERY_PENDING",
                    "删除未提交，导出目录将在下次启动时恢复",
                ) from recovery_exc
        raise

    cleanup_pending = quarantine is not None and not cleanup_quarantine(quarantine)
    return {
        "deleted": True,
        "summary": summary,
        "artifact_cleanup_pending": cleanup_pending,
    }


async def _delete_version_rows(
    connection, project_version_id: int, project_id: int
) -> None:
    await connection.execute("DELETE FROM project_versions WHERE id=?", (project_version_id,))
    remaining = await (
        await connection.execute(
            "SELECT COUNT(*) FROM project_versions WHERE project_id=?", (project_id,)
        )
    ).fetchone()
    if int(remaining[0]) == 0:
        await connection.execute("DELETE FROM projects WHERE id=?", (project_id,))


async def recover_project_version_storage(
    database_path: Path, generated_dir: Path
) -> dict[str, int]:
    restored = 0
    cleaned = 0
    cleanup_pending = 0
    async with connect(database_path) as connection:
        for quarantine in list_quarantines(generated_dir):
            exists = await (
                await connection.execute(
                    "SELECT 1 FROM project_versions WHERE id=?",
                    (quarantine.project_version_id,),
                )
            ).fetchone()
            if exists is not None:
                try:
                    restore_quarantine(generated_dir, quarantine)
                except ExportStorageError as exc:
                    raise ProjectVersionError(
                        "PROJECT_STORAGE_STARTUP_RECOVERY_FAILED", str(exc)
                    ) from exc
                restored += 1
            elif cleanup_quarantine(quarantine):
                cleaned += 1
            else:
                cleanup_pending += 1
    staging_removed, staging_pending = cleanup_abandoned_staging(generated_dir)
    return {
        "quarantines_restored": restored,
        "quarantines_cleaned": cleaned,
        "cleanup_pending": cleanup_pending,
        "staging_removed": staging_removed,
        "staging_pending": staging_pending,
    }
