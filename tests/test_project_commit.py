from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from app.application.import_preview_service import PreviewTokenError, create_preview
from app.application.project_import_service import commit_preview
from app.application.project_settings_service import (
    ProjectSettingsError,
    get_project_version_settings,
    update_project_version_settings,
)
from app.config import Settings
from app.database import connect, initialize_database


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "项目相关信息表.xlsx"


def run(coroutine):
    return asyncio.run(coroutine)


def initialize(settings: Settings) -> None:
    run(
        initialize_database(
            settings.database_path,
            settings.migrations_dir,
            settings.bootstrap_seed_path,
            settings.seed_schema_path,
        )
    )


def preview(settings: Settings):
    return run(
        create_preview(
            settings.database_path,
            settings.import_temp_dir,
            SOURCE.name,
            SOURCE.read_bytes(),
            settings.import_preview_ttl_seconds,
            settings.upload_max_bytes,
        )
    )


def commit(settings: Settings, token: str):
    return run(commit_preview(settings.database_path, token, {}, {}, False, "0.0"))


def test_commit_creates_complete_project_version_transaction(test_settings: Settings) -> None:
    initialize(test_settings)
    stored = preview(test_settings)
    result = commit(test_settings, stored.token)

    assert result.version_no == 1
    assert result.audit_instance_count == 52
    with sqlite3.connect(test_settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_versions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM phases").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM tracking_nodes").fetchone()[0] == 7
        assert connection.execute("SELECT COUNT(*) FROM config_items").fetchone()[0] == 26
        assert connection.execute("SELECT COUNT(*) FROM quality_activities").fetchone()[0] == 48
        assert connection.execute("SELECT COUNT(*) FROM audit_instances").fetchone()[0] == 52
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_instances WHERE report_scope='company'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM phase_detection_overrides").fetchone()[0] == 7
        assert connection.execute("SELECT config_item_number FROM project_version_settings").fetchone()[0] == "0.0"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_same_project_code_creates_independent_incrementing_versions(test_settings: Settings) -> None:
    initialize(test_settings)
    first = commit(test_settings, preview(test_settings).token)
    second = commit(test_settings, preview(test_settings).token)

    assert (first.version_no, second.version_no) == (1, 2)
    with sqlite3.connect(test_settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM project_versions").fetchone()[0] == 2
        assert connection.execute("SELECT MIN(sequence_no), MAX(sequence_no) FROM audit_instances WHERE project_version_id=?", (second.project_version_id,)).fetchone() == (1, 52)


def test_unknown_mapping_rolls_back_every_project_write(test_settings: Settings) -> None:
    initialize(test_settings)
    stored = preview(test_settings)
    with pytest.raises(PreviewTokenError):
        run(
            commit_preview(
                test_settings.database_path,
                stored.token,
                {},
                {"相控阵数字样机软件研制任务书": "product.not-exist"},
                False,
            )
        )
    with sqlite3.connect(test_settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert connection.execute("SELECT status FROM import_previews").fetchone()[0] == "pending"


def test_commit_uses_the_preview_mapping_snapshot_not_new_global_rules(test_settings: Settings) -> None:
    initialize(test_settings)
    stored = preview(test_settings)

    async def add_new_global_rule() -> None:
        async with connect(test_settings.database_path) as connection:
            row = await (
                await connection.execute(
                    "SELECT id FROM checklist_templates WHERE stable_key='product.software-test-plan'"
                )
            ).fetchone()
            await connection.execute(
                """
                INSERT INTO mapping_rules(
                    target_kind, match_kind, pattern, checklist_template_id,
                    priority, source, enabled
                ) VALUES ('product', 'exact', '相控阵数字样机软件开发计划', ?, 300, 'user', 1)
                """,
                (row["id"],),
            )
            await connection.commit()

    run(add_new_global_rule())
    committed = commit(test_settings, stored.token)
    with sqlite3.connect(test_settings.database_path) as connection:
        stable_key = connection.execute(
            """
            SELECT ct.stable_key
            FROM audit_instances ai
            JOIN config_items ci ON ci.id = ai.config_item_id
            JOIN checklist_templates ct ON ct.id = ai.checklist_template_id
            WHERE ai.project_version_id = ? AND ci.name = '相控阵数字样机软件开发计划'
            """,
            (committed.project_version_id,),
        ).fetchone()[0]
    assert stable_key == "product.software-development-plan"


def test_phase_override_can_exclude_detected_phase(test_settings: Settings) -> None:
    initialize(test_settings)
    stored = preview(test_settings)
    result = run(
        commit_preview(test_settings.database_path, stored.token, {"7": False}, {}, False)
    )
    assert result.audit_instance_count == 47
    with sqlite3.connect(test_settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM phases").fetchone()[0] == 5
        assert connection.execute(
            "SELECT user_selected_as_phase FROM phase_detection_overrides WHERE source_wbs='7'"
        ).fetchone()[0] == 0


def test_project_settings_update_uses_optimistic_revision(test_settings: Settings) -> None:
    initialize(test_settings)
    committed = commit(test_settings, preview(test_settings).token)

    initial = run(
        get_project_version_settings(test_settings.database_path, committed.project_version_id)
    )
    updated = run(
        update_project_version_settings(
            test_settings.database_path,
            committed.project_version_id,
            initial.revision,
            " 1.2 ",
        )
    )
    assert updated.config_item_number == "1.2"
    assert updated.revision == initial.revision + 1

    with pytest.raises(ProjectSettingsError) as stale:
        run(
            update_project_version_settings(
                test_settings.database_path,
                committed.project_version_id,
                initial.revision,
                "2.0",
            )
        )
    assert stale.value.code == "PROJECT_SETTINGS_REVISION_CONFLICT"
    current = run(
        get_project_version_settings(test_settings.database_path, committed.project_version_id)
    )
    assert (current.config_item_number, current.revision) == ("1.2", 2)


def test_project_settings_persists_and_clears_trend_conclusion_override(test_settings: Settings) -> None:
    initialize(test_settings)
    committed = commit(test_settings, preview(test_settings).token)
    initial = run(get_project_version_settings(test_settings.database_path, committed.project_version_id))
    overridden = run(
        update_project_version_settings(
            test_settings.database_path,
            committed.project_version_id,
            initial.revision,
            initial.config_item_number,
            "人工审核结论",
        )
    )
    assert overridden.trend_conclusion_override == "人工审核结论"
    cleared = run(
        update_project_version_settings(
            test_settings.database_path,
            committed.project_version_id,
            overridden.revision,
            overridden.config_item_number,
            "   ",
        )
    )
    assert cleared.trend_conclusion_override is None
