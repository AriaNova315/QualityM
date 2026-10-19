from __future__ import annotations

import asyncio
import copy
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.database import (
    MigrationError,
    connect,
    discover_migrations,
    foreign_key_violations,
    initialize_database,
    migrate,
    transaction,
)
from app.seeds import (
    SeedValidationError,
    import_seed,
    preview_seed_upgrade,
    validate_seed_payload,
)
from app.main import create_app


def run(coroutine):
    return asyncio.run(coroutine)


def copy_migrations(source: Path, target: Path) -> Path:
    shutil.copytree(source, target)
    return target


def valid_seed() -> dict:
    return {
        "seed_version": "1.0.0",
        "checklists": [
            {
                "stable_key": "product.requirements",
                "name": "软件需求规格说明检查单",
                "audit_kind": "product",
                "source_sheet": "软件需求规格说明",
                "groups": [
                    {
                        "stable_key": "content",
                        "name": "内容检查",
                        "items": [
                            {
                                "stable_key": "complete",
                                "display_no": "1*",
                                "content": "需求内容是否完整？",
                                "starred": True,
                                "issue_templates": [
                                    {
                                        "stable_key": "requirements.missing",
                                        "title": "需求内容缺失",
                                        "description": "需求文档存在必要内容缺失。",
                                        "corrective_suggestion": "补充缺失内容并重新评审。",
                                        "default_severity": "严重",
                                        "placeholders": [],
                                        "is_default": True,
                                    },
                                    {
                                        "stable_key": "requirements.unclear",
                                        "title": "需求表述不清",
                                        "description": "需求文档存在含义不明确的表述。",
                                        "corrective_suggestion": "澄清表述并确认一致理解。",
                                        "default_severity": "一般",
                                        "placeholders": [],
                                        "is_default": False,
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def write_seed(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_empty_database_migrates_and_restarts_idempotently(
    tmp_path: Path, test_settings: Settings
) -> None:
    database_path = tmp_path / "db" / "qualitym.db"

    first_version = run(
        initialize_database(
            database_path,
            test_settings.migrations_dir,
            test_settings.bootstrap_seed_path,
        )
    )
    second_version = run(
        initialize_database(
            database_path,
            test_settings.migrations_dir,
            test_settings.bootstrap_seed_path,
        )
    )

    assert first_version == second_version == 8
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 8
        assert connection.execute("SELECT COUNT(*) FROM seed_imports").fetchone()[0] == 1
        item_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(checklist_items)")
        }
        issue_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(issue_templates)")
        }
        audit_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(audit_instances)")
        }
        assert "revision" in item_columns and "revision" in issue_columns
        assert {"snapshot_schema_version", "user_edited"} <= audit_columns
        assert connection.execute(
            "SELECT COUNT(*) FROM issue_templates WHERE is_default=1"
        ).fetchone()[0] == 0
    assert run(foreign_key_violations(database_path)) == []


def test_application_lifespan_initializes_database(test_settings: Settings) -> None:
    app = create_app(test_settings)

    async def start_and_stop() -> None:
        async with app.router.lifespan_context(app):
            assert app.state.schema_version == 8

    run(start_and_stop())
    assert test_settings.database_path.is_file()
    with sqlite3.connect(test_settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 8


def test_default_conclusion_migration_backfills_only_unchecked_items(
    tmp_path: Path, test_settings: Settings
) -> None:
    migrations_dir = copy_migrations(
        test_settings.migrations_dir, tmp_path / "migrations"
    )
    latest_migration = migrations_dir / "008_default_checklist_conclusion.sql"
    latest_migration.unlink()
    database_path = tmp_path / "qualitym.db"
    assert run(migrate(database_path, migrations_dir)) == 7

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        rows = (
            (999, 1, "unchecked", "1", "待检查", 0, None, 1),
            (999, 2, "noncompliant", "2", "已发现问题", 0, "不符合", 2),
            (999, 3, "not-applicable", "3", "不适用", 0, "不适用", 3),
        )
        connection.executemany(
            """
            INSERT INTO checklist_instance_items(
                instance_group_id, source_item_id, stable_key, display_no_snapshot,
                content_snapshot, starred_snapshot, conclusion, display_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.commit()

    shutil.copy2(
        test_settings.migrations_dir / latest_migration.name,
        latest_migration,
    )
    assert run(migrate(database_path, migrations_dir)) == 8
    with sqlite3.connect(database_path) as connection:
        conclusions = dict(
            connection.execute(
                "SELECT stable_key, conclusion FROM checklist_instance_items"
            )
        )
    assert conclusions == {
        "unchecked": "符合",
        "noncompliant": "不符合",
        "not-applicable": "不适用",
    }


def test_every_connection_enforces_foreign_keys(tmp_path: Path, test_settings: Settings) -> None:
    database_path = tmp_path / "qualitym.db"
    run(migrate(database_path, test_settings.migrations_dir))

    async def attempt_invalid_insert() -> None:
        async with connect(database_path) as connection:
            cursor = await connection.execute("PRAGMA foreign_keys")
            assert (await cursor.fetchone())[0] == 1
            with pytest.raises(sqlite3.IntegrityError):
                await connection.execute(
                    """
                    INSERT INTO project_versions(
                        project_id, version_no, source_filename, source_sha256, imported_at
                    ) VALUES (999, 1, 'source.xlsx', ?, '2026-08-12T00:00:00Z')
                    """,
                    ("0" * 64,),
                )

    run(attempt_invalid_insert())


def test_transaction_rolls_back_on_error(tmp_path: Path, test_settings: Settings) -> None:
    database_path = tmp_path / "qualitym.db"
    run(migrate(database_path, test_settings.migrations_dir))

    async def failed_transaction() -> None:
        async with connect(database_path) as connection:
            with pytest.raises(RuntimeError, match="abort"):
                async with transaction(connection):
                    await connection.execute(
                        "INSERT INTO projects(project_code, project_name) VALUES ('P1', '项目一')"
                    )
                    raise RuntimeError("abort")
            cursor = await connection.execute("SELECT COUNT(*) FROM projects")
            assert (await cursor.fetchone())[0] == 0

    run(failed_transaction())


def test_project_cascade_delete_preserves_global_templates(
    tmp_path: Path, test_settings: Settings
) -> None:
    database_path = tmp_path / "qualitym.db"
    run(migrate(database_path, test_settings.migrations_dir))
    seed_path = write_seed(tmp_path / "seed.json", valid_seed())
    run(import_seed(database_path, seed_path))

    async def insert_and_delete_project() -> None:
        async with connect(database_path) as connection:
            async with transaction(connection):
                cursor = await connection.execute(
                    "INSERT INTO projects(project_code, project_name) VALUES ('P1', '项目一')"
                )
                project_id = cursor.lastrowid
                await connection.execute(
                    """
                    INSERT INTO project_versions(
                        project_id, version_no, source_filename, source_sha256, imported_at
                    ) VALUES (?, 1, 'source.xlsx', ?, '2026-08-12T00:00:00Z')
                    """,
                    (project_id, "0" * 64),
                )
            await connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            await connection.commit()
            project_count = await (await connection.execute("SELECT COUNT(*) FROM project_versions")).fetchone()
            template_count = await (await connection.execute("SELECT COUNT(*) FROM checklist_templates")).fetchone()
            assert project_count[0] == 0
            assert template_count[0] == 1

    run(insert_and_delete_project())
    assert run(foreign_key_violations(database_path)) == []


def test_migration_checksum_change_is_rejected(tmp_path: Path, test_settings: Settings) -> None:
    migrations_dir = copy_migrations(test_settings.migrations_dir, tmp_path / "migrations")
    database_path = tmp_path / "qualitym.db"
    run(migrate(database_path, migrations_dir))
    with (migrations_dir / "001_initial.sql").open("a", encoding="utf-8") as handle:
        handle.write("\n-- changed\n")

    with pytest.raises(MigrationError, match="checksum or name changed"):
        run(migrate(database_path, migrations_dir))


def test_migration_version_gap_is_rejected(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (migrations_dir / "003_third.sql").write_text("SELECT 3;", encoding="utf-8")

    with pytest.raises(MigrationError, match="contiguous"):
        discover_migrations(migrations_dir)


def test_failed_migration_does_not_record_version(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_broken.sql").write_text(
        "CREATE TABLE partial_table(id INTEGER); INVALID SQL;", encoding="utf-8"
    )
    database_path = tmp_path / "qualitym.db"

    with pytest.raises(MigrationError, match="failed"):
        run(migrate(database_path, migrations_dir))
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'partial_table'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "mutate, expected_message",
    [
        (
            lambda seed: seed["checklists"].append(seed["checklists"][0].copy()),
            "Duplicate checklist stable key",
        ),
        (
            lambda seed: seed["checklists"][0].update({"stable_key": "非法 key"}),
            "invalid stable key",
        ),
    ],
)
def test_invalid_seed_is_rejected(mutate, expected_message: str) -> None:
    seed = valid_seed()
    mutate(seed)
    with pytest.raises(SeedValidationError, match=expected_message):
        validate_seed_payload(seed)


def test_seed_import_is_idempotent_and_changed_version_is_rejected(
    tmp_path: Path, test_settings: Settings
) -> None:
    database_path = tmp_path / "qualitym.db"
    run(migrate(database_path, test_settings.migrations_dir))
    seed_path = write_seed(tmp_path / "seed.json", valid_seed())

    first = run(import_seed(database_path, seed_path))
    second = run(import_seed(database_path, seed_path))
    changed = valid_seed()
    changed["checklists"][0]["name"] = "被修改的同版本内容"
    write_seed(seed_path, changed)

    assert first.applied is True
    assert second.applied is False
    with pytest.raises(SeedValidationError, match="content changed"):
        run(import_seed(database_path, seed_path))


def upgraded_seed(version: str = "2.0.0") -> dict:
    seed = copy.deepcopy(valid_seed())
    seed["seed_version"] = version
    issues = seed["checklists"][0]["groups"][0]["items"][0]["issue_templates"]
    issues[0]["title"] = "需求范围覆盖不完整"
    issues.append(
        {
            "stable_key": "requirements.traceability",
            "title": "需求追踪关系不完整",
            "description": "需求与设计、验证结果之间缺少双向追踪关系。",
            "corrective_suggestion": "补充双向追踪并复核所有孤立项。",
            "default_severity": "严重",
            "placeholders": [],
            "is_default": False,
        }
    )
    return seed


def test_seed_upgrade_updates_builtin_content_and_disables_removed_records(
    tmp_path: Path, test_settings: Settings
) -> None:
    database_path = tmp_path / "qualitym.db"
    run(migrate(database_path, test_settings.migrations_dir))
    seed_path = write_seed(tmp_path / "seed.json", valid_seed())
    run(import_seed(database_path, seed_path))

    write_seed(seed_path, upgraded_seed())
    result = run(import_seed(database_path, seed_path))
    assert result.applied is True
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT stable_key, version, title, enabled FROM issue_templates ORDER BY id"
        ).fetchall()
        assert len(rows) == 3
        assert rows[0]["title"] == "需求范围覆盖不完整"
        assert {row["version"] for row in rows} == {"2.0.0"}

    third = upgraded_seed("3.0.0")
    third["checklists"][0]["groups"][0]["items"][0]["issue_templates"].pop()
    write_seed(seed_path, third)
    run(import_seed(database_path, seed_path))
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT enabled FROM issue_templates WHERE stable_key = 'requirements.traceability'"
        ).fetchone()[0] == 0


def test_seed_upgrade_requires_confirmation_and_preserves_user_modified_subtree(
    tmp_path: Path, test_settings: Settings
) -> None:
    database_path = tmp_path / "qualitym.db"
    run(migrate(database_path, test_settings.migrations_dir))
    seed_path = write_seed(tmp_path / "seed.json", valid_seed())
    run(import_seed(database_path, seed_path))
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE issue_templates
            SET title = '用户自定义标题', user_modified = 1
            WHERE stable_key = 'requirements.missing'
            """
        )
        connection.commit()

    write_seed(seed_path, upgraded_seed())
    preview = run(preview_seed_upgrade(database_path, seed_path))
    assert [(item.entity_type, item.stable_key) for item in preview.conflicts] == [
        ("issue", "requirements.missing")
    ]
    with pytest.raises(SeedValidationError, match="user-modified conflicts"):
        run(import_seed(database_path, seed_path))

    result = run(import_seed(database_path, seed_path, preserve_user_modified=True))
    assert result.preserved_user_modified == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT title FROM issue_templates WHERE stable_key = 'requirements.missing'"
        ).fetchone()[0] == "用户自定义标题"
        assert connection.execute("SELECT COUNT(*) FROM issue_templates").fetchone()[0] == 2

    removed = upgraded_seed("3.0.0")
    removed["checklists"][0]["groups"][0]["items"] = []
    write_seed(seed_path, removed)
    run(import_seed(database_path, seed_path, preserve_user_modified=True))
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT enabled FROM checklist_templates").fetchone()[0] == 1
        assert connection.execute("SELECT enabled FROM checklist_groups").fetchone()[0] == 1
        assert connection.execute("SELECT enabled FROM checklist_items").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM issue_templates WHERE enabled = 1"
        ).fetchone()[0] == 2
