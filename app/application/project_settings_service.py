from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.database import connect, transaction


class ProjectSettingsError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProjectVersionSettings:
    project_version_id: int
    config_item_number: str
    trend_conclusion_override: str | None
    revision: int
    updated_at: str


def _settings_from_row(row) -> ProjectVersionSettings:
    return ProjectVersionSettings(
        project_version_id=int(row["project_version_id"]),
        config_item_number=row["config_item_number"],
        trend_conclusion_override=row["trend_conclusion_override"],
        revision=int(row["revision"]),
        updated_at=row["updated_at"],
    )


async def get_project_version_settings(
    database_path: Path,
    project_version_id: int,
) -> ProjectVersionSettings:
    async with connect(database_path) as connection:
        row = await (
            await connection.execute(
                "SELECT * FROM project_version_settings WHERE project_version_id = ?",
                (project_version_id,),
            )
        ).fetchone()
    if row is None:
        raise ProjectSettingsError("PROJECT_VERSION_NOT_FOUND", "项目数据版本不存在")
    return _settings_from_row(row)


async def update_project_version_settings(
    database_path: Path,
    project_version_id: int,
    expected_revision: int,
    config_item_number: str,
    trend_conclusion_override: str | None = None,
) -> ProjectVersionSettings:
    normalized_number = config_item_number.strip()
    if not normalized_number:
        raise ProjectSettingsError("PROJECT_SETTINGS_INVALID", "配置项编号不能为空")
    normalized_override = (
        (trend_conclusion_override or "").strip() or None
    )

    now = datetime.now(UTC).isoformat()
    async with connect(database_path) as connection:
        async with transaction(connection):
            cursor = await connection.execute(
                """
                UPDATE project_version_settings
                SET config_item_number = ?, trend_conclusion_override = ?,
                    revision = revision + 1, updated_at = ?
                WHERE project_version_id = ? AND revision = ?
                """,
                (normalized_number, normalized_override, now, project_version_id, expected_revision),
            )
            if cursor.rowcount != 1:
                exists = await (
                    await connection.execute(
                        "SELECT 1 FROM project_version_settings WHERE project_version_id = ?",
                        (project_version_id,),
                    )
                ).fetchone()
                if exists is None:
                    raise ProjectSettingsError(
                        "PROJECT_VERSION_NOT_FOUND", "项目数据版本不存在"
                    )
                raise ProjectSettingsError(
                    "PROJECT_SETTINGS_REVISION_CONFLICT",
                    "项目设置已被修改，请刷新后重试",
                )
            row = await (
                await connection.execute(
                    "SELECT * FROM project_version_settings WHERE project_version_id = ?",
                    (project_version_id,),
                )
            ).fetchone()
    return _settings_from_row(row)
