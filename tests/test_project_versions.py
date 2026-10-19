from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application import project_version_service
from app.application.export_storage import ExportStorageError, prepare_delete_quarantine
from app.application.import_preview_service import create_preview
from app.application.project_import_service import commit_preview
from app.application.project_version_service import (
    ProjectVersionError,
    confirm_delete,
    create_delete_preview,
    get_project_version,
    list_project_versions,
    recover_project_version_storage,
)
from app.config import Settings
from app.database import initialize_database
from app.main import create_app
from app.repositories import exports


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "项目相关信息表.xlsx"


def run(coroutine):
    return asyncio.run(coroutine)


def committed_version(settings: Settings):
    run(initialize_database(settings.database_path, settings.migrations_dir, settings.bootstrap_seed_path, settings.seed_schema_path))
    preview = run(create_preview(settings.database_path, settings.import_temp_dir, SOURCE.name, SOURCE.read_bytes(), settings.import_preview_ttl_seconds, settings.upload_max_bytes))
    return run(commit_preview(settings.database_path, preview.token, {}, {}, False, "0.0"))


async def completed_export(settings: Settings, project_version_id: int, key: str = "completed"):
    output_dir = settings.generated_dir / f"export-{key}"
    output_dir.mkdir(parents=True)
    (output_dir / "manifest.txt").write_text("ok", encoding="utf-8")
    job = await exports.create_job(settings.database_path, project_version_id, key)
    await exports.transition_job(
        settings.database_path, job.id, frozenset({"queued"}), "snapshotting",
        progress_step="snapshotting",
    )
    await exports.transition_job(
        settings.database_path, job.id, frozenset({"snapshotting"}), "building",
        progress_step="building",
    )
    await exports.transition_job(
        settings.database_path, job.id, frozenset({"building"}), "validating",
        progress_step="validating", output_dir=str(output_dir),
    )
    await exports.transition_job(
        settings.database_path, job.id, frozenset({"validating"}), "completed",
        progress_step="completed",
    )
    return output_dir


def test_project_version_list_and_detail_include_stable_counts(test_settings: Settings) -> None:
    committed = committed_version(test_settings)
    listed = run(list_project_versions(test_settings.database_path))
    detail = run(get_project_version(test_settings.database_path, committed.project_version_id))

    assert len(listed) == 1
    assert listed[0] == detail
    assert detail["project_name"]
    assert detail["project_code"]
    assert detail["version_no"] == 1
    assert detail["phase_count"] == 6
    assert detail["audit_count"] == 52
    assert detail["checklist_item_count"] > 0


def test_delete_requires_matching_version_token_text_and_unchanged_snapshot(test_settings: Settings) -> None:
    committed = committed_version(test_settings)
    preview = run(create_delete_preview(test_settings.database_path, committed.project_version_id))

    with pytest.raises(ProjectVersionError) as wrong_owner:
        run(confirm_delete(test_settings.database_path, test_settings.generated_dir, committed.project_version_id + 1, preview["preview_token"], preview["confirmation_text"]))
    assert wrong_owner.value.code == "PROJECT_DELETE_TOKEN_INVALID"

    with pytest.raises(ProjectVersionError) as wrong_text:
        run(confirm_delete(test_settings.database_path, test_settings.generated_dir, committed.project_version_id, preview["preview_token"], "删除别的项目"))
    assert wrong_text.value.code == "PROJECT_DELETE_CONFIRMATION_MISMATCH"

    with sqlite3.connect(test_settings.database_path) as connection:
        connection.execute("UPDATE project_versions SET source_filename='changed.xlsx' WHERE id=?", (committed.project_version_id,))
        connection.commit()
    with pytest.raises(ProjectVersionError) as stale:
        run(confirm_delete(test_settings.database_path, test_settings.generated_dir, committed.project_version_id, preview["preview_token"], preview["confirmation_text"]))
    assert stale.value.code == "PROJECT_DELETE_PREVIEW_STALE"


def test_delete_cascades_version_data_and_last_project(test_settings: Settings) -> None:
    committed = committed_version(test_settings)
    preview = run(create_delete_preview(test_settings.database_path, committed.project_version_id))
    result = run(confirm_delete(test_settings.database_path, test_settings.generated_dir, committed.project_version_id, preview["preview_token"], preview["confirmation_text"]))
    assert result["deleted"] is True
    with sqlite3.connect(test_settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_versions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_instances").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_project_version_routes_cover_success_failure_and_delete_ownership(test_settings: Settings) -> None:
    committed = committed_version(test_settings)
    with TestClient(create_app(test_settings)) as client:
        assert client.get("/api/project-versions").status_code == 200
        assert client.get(f"/api/project-versions/{committed.project_version_id}").status_code == 200
        assert client.get("/api/project-versions/999999").status_code == 404
        preview = client.post(f"/api/project-versions/{committed.project_version_id}/delete-preview")
        assert preview.status_code == 200
        payload = preview.json()
        mismatch = client.post(
            f"/api/project-versions/{committed.project_version_id + 1}/delete-confirm",
            json={"preview_token": payload["preview_token"], "confirmation_text": payload["confirmation_text"]},
        )
        assert mismatch.status_code == 409
        deleted = client.post(
            f"/api/project-versions/{committed.project_version_id}/delete-confirm",
            json={"preview_token": payload["preview_token"], "confirmation_text": payload["confirmation_text"]},
        )
        assert deleted.status_code == 200


def test_delete_removes_completed_export_directory_and_blocks_active_job(
    test_settings: Settings,
) -> None:
    committed = committed_version(test_settings)
    output_dir = run(completed_export(test_settings, committed.project_version_id))
    preview = run(
        create_delete_preview(test_settings.database_path, committed.project_version_id)
    )
    result = run(
        confirm_delete(
            test_settings.database_path,
            test_settings.generated_dir,
            committed.project_version_id,
            preview["preview_token"],
            preview["confirmation_text"],
        )
    )
    assert result["artifact_cleanup_pending"] is False
    assert not output_dir.exists()
    quarantine_root = test_settings.generated_dir / ".delete-quarantine"
    assert quarantine_root.is_dir()
    assert not any(quarantine_root.iterdir())

    second = committed_version(test_settings)
    second_preview = run(
        create_delete_preview(test_settings.database_path, second.project_version_id)
    )
    run(exports.create_job(test_settings.database_path, second.project_version_id, "active"))
    with pytest.raises(ProjectVersionError) as active:
        run(
            confirm_delete(
                test_settings.database_path,
                test_settings.generated_dir,
                second.project_version_id,
                second_preview["preview_token"],
                second_preview["confirmation_text"],
            )
        )
    assert active.value.code == "PROJECT_DELETE_EXPORT_ACTIVE"


def test_database_delete_failure_restores_quarantined_export(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed = committed_version(test_settings)
    output_dir = run(completed_export(test_settings, committed.project_version_id))
    preview = run(
        create_delete_preview(test_settings.database_path, committed.project_version_id)
    )

    async def fail_delete(*_args) -> None:
        raise sqlite3.OperationalError("injected delete failure")

    monkeypatch.setattr(project_version_service, "_delete_version_rows", fail_delete)
    with pytest.raises(sqlite3.OperationalError, match="injected delete failure"):
        run(
            confirm_delete(
                test_settings.database_path,
                test_settings.generated_dir,
                committed.project_version_id,
                preview["preview_token"],
                preview["confirmation_text"],
            )
        )
    assert output_dir.is_dir()
    assert run(get_project_version(test_settings.database_path, committed.project_version_id))


def test_cleanup_failure_is_retried_on_startup(
    test_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    committed = committed_version(test_settings)
    output_dir = run(completed_export(test_settings, committed.project_version_id))
    preview = run(
        create_delete_preview(test_settings.database_path, committed.project_version_id)
    )
    real_cleanup = project_version_service.cleanup_quarantine
    monkeypatch.setattr(project_version_service, "cleanup_quarantine", lambda _item: False)
    result = run(
        confirm_delete(
            test_settings.database_path,
            test_settings.generated_dir,
            committed.project_version_id,
            preview["preview_token"],
            preview["confirmation_text"],
        )
    )
    assert result["artifact_cleanup_pending"] is True
    assert not output_dir.exists()
    quarantine_root = test_settings.generated_dir / ".delete-quarantine"
    assert any(quarantine_root.iterdir())

    monkeypatch.setattr(project_version_service, "cleanup_quarantine", real_cleanup)
    recovered = run(
        recover_project_version_storage(
            test_settings.database_path, test_settings.generated_dir
        )
    )
    assert recovered["quarantines_cleaned"] == 1
    assert not any(quarantine_root.iterdir())


def test_startup_restores_precommit_quarantine_and_cleans_staging(
    test_settings: Settings,
) -> None:
    committed = committed_version(test_settings)
    output_dir = run(completed_export(test_settings, committed.project_version_id))
    quarantine = prepare_delete_quarantine(
        test_settings.generated_dir,
        committed.project_version_id,
        (str(output_dir),),
    )
    assert quarantine is not None
    assert not output_dir.exists()
    abandoned = test_settings.generated_dir / ".staging" / "999"
    abandoned.mkdir(parents=True)
    (abandoned / "partial.xlsx").write_bytes(b"partial")

    recovered = run(
        recover_project_version_storage(
            test_settings.database_path, test_settings.generated_dir
        )
    )
    assert recovered == {
        "quarantines_restored": 1,
        "quarantines_cleaned": 0,
        "cleanup_pending": 0,
        "staging_removed": 1,
        "staging_pending": 0,
    }
    assert output_dir.is_dir()
    assert not abandoned.exists()


def test_delete_quarantine_rejects_output_outside_generated_root(
    test_settings: Settings, tmp_path: Path
) -> None:
    outside = tmp_path / "outside-export"
    outside.mkdir()
    with pytest.raises(ExportStorageError) as unsafe:
        prepare_delete_quarantine(
            test_settings.generated_dir,
            1,
            (str(outside),),
        )
    assert unsafe.value.code == "EXPORT_STORAGE_PATH_INVALID"
    assert outside.is_dir()
