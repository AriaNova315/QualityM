from __future__ import annotations

import asyncio
import json
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.application.import_preview_service import (
    PreviewTokenError,
    cleanup_expired_previews,
    consume_preview,
    create_preview,
    get_preview,
)
from app.config import Settings
from app.database import connect, initialize_database
from app.main import create_app
from app.safety import UploadLimits, UploadValidationError, validate_xlsx
from app.safety import safe_upload_filename


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


def create(settings: Settings, ttl_seconds: int = 3600):
    return run(
        create_preview(
            settings.database_path,
            settings.import_temp_dir,
            SOURCE.name,
            SOURCE.read_bytes(),
            ttl_seconds,
            settings.upload_max_bytes,
        )
    )


def test_preview_persists_across_service_restart_without_creating_project_version(
    test_settings: Settings,
) -> None:
    initialize(test_settings)
    stored = create(test_settings)

    payload_after_restart = run(get_preview(test_settings.database_path, stored.token))
    assert len(payload_after_restart["phases"]) == 6
    assert (test_settings.import_temp_dir / f"{hashlib_token(stored.token)[:2]}/{hashlib_token(stored.token)}.xlsx").is_file()
    with sqlite3.connect(test_settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM import_previews").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM project_versions").fetchone()[0] == 0


def test_preview_includes_fixed_named_product_mapping_suggestions(test_settings: Settings) -> None:
    initialize(test_settings)
    stored = create(test_settings)

    resolutions = run(get_preview(test_settings.database_path, stored.token))["product_mapping_resolutions"]
    development_plan = next(
        item for item in resolutions if item["subject"] == "相控阵数字样机软件开发计划"
    )
    assert development_plan == {
        "subject": "相控阵数字样机软件开发计划",
        "stable_key": "product.software-development-plan",
        "template_name": "软件开发计划检查单",
        "source": "builtin_exact",
        "status": "resolved",
    }


def hashlib_token(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()


def test_token_is_one_time_and_double_confirm_is_safe(test_settings: Settings) -> None:
    initialize(test_settings)
    stored = create(test_settings)
    preview_id = run(consume_preview(test_settings.database_path, stored.token, {"1": True}))
    assert preview_id == stored.preview_id
    with pytest.raises(PreviewTokenError, match="已使用"):
        run(consume_preview(test_settings.database_path, stored.token, {"1": True}))
    with sqlite3.connect(test_settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM project_versions").fetchone()[0] == 0


def test_concurrent_double_confirm_has_exactly_one_winner(test_settings: Settings) -> None:
    initialize(test_settings)
    stored = create(test_settings)

    async def race():
        return await asyncio.gather(
            consume_preview(test_settings.database_path, stored.token, {"1": True}),
            consume_preview(test_settings.database_path, stored.token, {"1": True}),
            return_exceptions=True,
        )

    results = run(race())
    assert sum(isinstance(result, int) for result in results) == 1
    errors = [result for result in results if isinstance(result, PreviewTokenError)]
    assert len(errors) == 1
    assert errors[0].code == "IMPORT_PREVIEW_TOKEN_CONSUMED"


def test_tampered_and_forbidden_override_tokens_are_rejected(test_settings: Settings) -> None:
    initialize(test_settings)
    stored = create(test_settings)
    with pytest.raises(PreviewTokenError) as tampered:
        run(get_preview(test_settings.database_path, stored.token + "x"))
    assert tampered.value.code == "IMPORT_PREVIEW_TOKEN_INVALID"
    with pytest.raises(PreviewTokenError) as forbidden:
        run(consume_preview(test_settings.database_path, stored.token, {"5": True}))
    assert forbidden.value.code == "IMPORT_PHASE_OVERRIDE_FORBIDDEN"


def test_expired_preview_cleanup_removes_controlled_file(test_settings: Settings) -> None:
    initialize(test_settings)
    stored = create(test_settings)
    token_hash = hashlib_token(stored.token)
    stored_file = test_settings.import_temp_dir / token_hash[:2] / f"{token_hash}.xlsx"

    async def expire() -> None:
        async with connect(test_settings.database_path) as connection:
            await connection.execute(
                "UPDATE import_previews SET expires_at = ? WHERE id = ?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), stored.preview_id),
            )
            await connection.commit()

    run(expire())
    with pytest.raises(PreviewTokenError) as error:
        run(get_preview(test_settings.database_path, stored.token))
    assert error.value.code == "IMPORT_PREVIEW_TOKEN_EXPIRED"
    assert run(cleanup_expired_previews(test_settings.database_path, test_settings.import_temp_dir)) == 1
    assert not stored_file.exists()


def test_upload_validation_rejects_wrong_type_corruption_and_zip_bomb(tmp_path: Path) -> None:
    with pytest.raises(UploadValidationError) as wrong_type:
        safe_upload_filename("project.xls")
    assert wrong_type.value.code == "UPLOAD_UNSUPPORTED_TYPE"
    with pytest.raises(UploadValidationError) as unsafe_name:
        safe_upload_filename("../project.xlsx")
    assert unsafe_name.value.code == "UPLOAD_UNSAFE_FILENAME"
    text_file = tmp_path / "bad.xlsx"
    text_file.write_text("not an xlsx", encoding="utf-8")
    with pytest.raises(UploadValidationError) as corrupt:
        validate_xlsx(text_file)
    assert corrupt.value.code == "UPLOAD_INVALID_XLSX"

    bomb = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "x")
        archive.writestr("xl/workbook.xml", "x" * 100_000)
    with pytest.raises(UploadValidationError) as compressed:
        validate_xlsx(bomb, UploadLimits(max_compression_ratio=5.0))
    assert compressed.value.code == "UPLOAD_ZIP_BOMB"


def test_blocking_diagnostic_prevents_confirmation(test_settings: Settings) -> None:
    initialize(test_settings)
    stored = create(test_settings)

    async def inject_blocker() -> None:
        async with connect(test_settings.database_path) as connection:
            row = await (
                await connection.execute(
                    "SELECT parsed_payload_json FROM import_previews WHERE id = ?", (stored.preview_id,)
                )
            ).fetchone()
            payload = json.loads(row[0])
            payload["diagnostics"].append({"code": "TEST", "severity": "blocker"})
            await connection.execute(
                "UPDATE import_previews SET parsed_payload_json = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), stored.preview_id),
            )
            await connection.commit()

    run(inject_blocker())
    with pytest.raises(PreviewTokenError) as error:
        run(consume_preview(test_settings.database_path, stored.token, {}))
    assert error.value.code == "IMPORT_PREVIEW_HAS_BLOCKERS"


def test_oversized_upload_is_rejected_before_storage(test_settings: Settings) -> None:
    initialize(test_settings)
    with pytest.raises(UploadValidationError) as error:
        run(
            create_preview(
                test_settings.database_path,
                test_settings.import_temp_dir,
                "project.xlsx",
                b"x" * 11,
                3600,
                10,
            )
        )
    assert error.value.code == "UPLOAD_TOO_LARGE"
    assert not test_settings.import_temp_dir.exists()


def test_preview_http_api_round_trip(test_settings: Settings) -> None:
    app = create_app(test_settings)

    async def request_flow() -> None:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/imports/preview",
                    files={"file": (SOURCE.name, SOURCE.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                )
                assert response.status_code == 201
                data = response.json()
                token = data["preview_token"]
                assert len(data["preview"]["phases"]) == 6
                read_response = await client.get(f"/api/imports/preview/{token}")
                assert read_response.status_code == 200
                confirm = await client.post(
                    f"/api/imports/preview/{token}/confirm",
                    json={"phase_overrides": {phase["wbs"]: True for phase in data["preview"]["phases"]}},
                )
                assert confirm.status_code == 200
                assert confirm.json()["project_version_created"] is True
                assert confirm.json()["audit_instance_count"] == 52
                version_id = confirm.json()["project_version_id"]
                settings = await client.get(f"/api/project-versions/{version_id}/settings")
                assert settings.status_code == 200
                assert settings.json()["config_item_number"] == "0.0"
                changed = await client.patch(
                    f"/api/project-versions/{version_id}/settings",
                    json={"expected_revision": 1, "config_item_number": "1.2"},
                )
                assert changed.status_code == 200
                assert changed.json()["revision"] == 2
                stale = await client.patch(
                    f"/api/project-versions/{version_id}/settings",
                    json={"expected_revision": 1, "config_item_number": "2.0"},
                )
                assert stale.status_code == 409
                assert stale.json()["detail"]["code"] == "PROJECT_SETTINGS_REVISION_CONFLICT"
                repeated = await client.post(
                    f"/api/imports/preview/{token}/confirm", json={"phase_overrides": {}}
                )
                assert repeated.status_code == 409

    run(request_flow())
