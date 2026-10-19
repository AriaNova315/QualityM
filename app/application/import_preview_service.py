from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.application.checklist_mapping_service import build_product_mapping_resolutions
from app.database import connect, transaction
from app.importers.project_workbook import parse_project_workbook
from app.safety import UploadLimits, atomic_write_bytes, safe_upload_filename, validate_xlsx


class PreviewTokenError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StoredPreview:
    token: str
    preview_id: int
    payload: dict[str, Any]
    expires_at: str


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def create_preview(
    database_path: Path,
    import_temp_dir: Path,
    filename: str,
    content: bytes,
    ttl_seconds: int,
    max_bytes: int,
) -> StoredPreview:
    safe_name = safe_upload_filename(filename)
    if len(content) > max_bytes:
        from app.safety import UploadValidationError

        raise UploadValidationError("UPLOAD_TOO_LARGE", "文件超过上传大小限制")
    token = secrets.token_urlsafe(32)
    token_hash = _token_hash(token)
    relative_path = f"{token_hash[:2]}/{token_hash}.xlsx"
    stored_path = import_temp_dir / relative_path
    atomic_write_bytes(stored_path, content)
    try:
        source_sha256 = validate_xlsx(stored_path, UploadLimits(max_bytes=max_bytes))
        payload = parse_project_workbook(stored_path)
        now = _utc_now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        async with connect(database_path) as connection:
            async with transaction(connection):
                payload["product_mapping_resolutions"] = await build_product_mapping_resolutions(
                    connection, payload
                )
                cursor = await connection.execute(
                    """
                    INSERT INTO import_previews(
                        token_hash, source_filename, source_sha256, temp_relative_path,
                        parsed_payload_json, overrides_json, diagnostics_json, status,
                        expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, '{}', ?, 'pending', ?, ?)
                    """,
                    (
                        token_hash, safe_name, source_sha256, relative_path,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        json.dumps(payload["diagnostics"], ensure_ascii=False, sort_keys=True),
                        expires_at.isoformat(), now.isoformat(),
                    ),
                )
                preview_id = int(cursor.lastrowid)
        return StoredPreview(token, preview_id, payload, expires_at.isoformat())
    except BaseException:
        stored_path.unlink(missing_ok=True)
        parent = stored_path.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
        raise


async def get_preview(database_path: Path, token: str) -> dict[str, Any]:
    async with connect(database_path) as connection:
        row = await (
            await connection.execute(
                "SELECT * FROM import_previews WHERE token_hash = ?", (_token_hash(token),)
            )
        ).fetchone()
    if row is None:
        raise PreviewTokenError("IMPORT_PREVIEW_TOKEN_INVALID", "导入预览令牌无效")
    if row["status"] != "pending":
        raise PreviewTokenError("IMPORT_PREVIEW_TOKEN_CONSUMED", "导入预览令牌已使用")
    if datetime.fromisoformat(row["expires_at"]) <= _utc_now():
        raise PreviewTokenError("IMPORT_PREVIEW_TOKEN_EXPIRED", "导入预览令牌已过期")
    payload = json.loads(row["parsed_payload_json"])
    payload["overrides"] = json.loads(row["overrides_json"])
    return payload


async def consume_preview(
    database_path: Path,
    token: str,
    phase_overrides: dict[str, bool],
) -> int:
    token_hash = _token_hash(token)
    now = _utc_now()
    async with connect(database_path) as connection:
        async with transaction(connection):
            row = await (
                await connection.execute(
                    "SELECT id, status, expires_at, parsed_payload_json FROM import_previews WHERE token_hash = ?",
                    (token_hash,),
                )
            ).fetchone()
            if row is None:
                raise PreviewTokenError("IMPORT_PREVIEW_TOKEN_INVALID", "导入预览令牌无效")
            if row["status"] != "pending":
                raise PreviewTokenError("IMPORT_PREVIEW_TOKEN_CONSUMED", "导入预览令牌已使用")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                await connection.execute(
                    "UPDATE import_previews SET status = 'expired' WHERE id = ?", (row["id"],)
                )
                raise PreviewTokenError("IMPORT_PREVIEW_TOKEN_EXPIRED", "导入预览令牌已过期")
            payload = json.loads(row["parsed_payload_json"])
            blockers = [
                diagnostic for diagnostic in payload.get("diagnostics", [])
                if diagnostic.get("severity") == "blocker"
            ]
            if blockers:
                raise PreviewTokenError(
                    "IMPORT_PREVIEW_HAS_BLOCKERS",
                    f"导入预览仍有 {len(blockers)} 个阻塞问题",
                )
            allowed_wbs = {
                phase["wbs"] for phase in payload["phases"]
            } | {item["wbs"] for item in payload["excluded_top_level"]}
            unknown = set(phase_overrides) - allowed_wbs
            if unknown:
                raise PreviewTokenError(
                    "IMPORT_PHASE_OVERRIDE_INVALID", f"阶段调整包含未知 WBS：{sorted(unknown)}"
                )
            excluded_company_wbs = {
                item["wbs"] for item in payload["excluded_top_level"]
                if item["reason"] == "excluded_company_validation"
            }
            forbidden = {wbs for wbs, enabled in phase_overrides.items() if enabled and wbs in excluded_company_wbs}
            if forbidden:
                raise PreviewTokenError(
                    "IMPORT_PHASE_OVERRIDE_FORBIDDEN",
                    f"公司质量验证不能调整为项目阶段：{sorted(forbidden)}",
                )
            cursor = await connection.execute(
                """
                UPDATE import_previews
                SET overrides_json = ?, status = 'consumed'
                WHERE id = ? AND status = 'pending'
                """,
                (json.dumps(phase_overrides, ensure_ascii=False, sort_keys=True), row["id"]),
            )
            if cursor.rowcount != 1:
                raise PreviewTokenError("IMPORT_PREVIEW_TOKEN_CONSUMED", "导入预览令牌已使用")
            return int(row["id"])


async def cleanup_expired_previews(database_path: Path, import_temp_dir: Path) -> int:
    now = _utc_now().isoformat()
    async with connect(database_path) as connection:
        rows = await (
            await connection.execute(
                "SELECT id, temp_relative_path FROM import_previews WHERE status = 'pending' AND expires_at <= ?",
                (now,),
            )
        ).fetchall()
        async with transaction(connection):
            await connection.execute(
                "UPDATE import_previews SET status = 'expired' WHERE status = 'pending' AND expires_at <= ?",
                (now,),
            )
    for row in rows:
        path = import_temp_dir / row["temp_relative_path"]
        path.unlink(missing_ok=True)
    return len(rows)
