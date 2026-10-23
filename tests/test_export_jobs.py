from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import aiosqlite

from app.application.export_service import ExportJobManager, ExportServiceError
from app.application.import_preview_service import create_preview
from app.application.project_import_service import commit_preview
from app.database import initialize_database
from app.main import create_app
from app.repositories import exports


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "项目相关信息表.xlsx"


def run(coroutine):
    return asyncio.run(coroutine)


async def _create_project(settings) -> int:
    preview = await create_preview(
        settings.database_path, settings.import_temp_dir, SOURCE.name,
        SOURCE.read_bytes(), settings.import_preview_ttl_seconds, settings.upload_max_bytes,
    )
    return (await commit_preview(settings.database_path, preview.token, {}, {}, False, "0.0")).project_version_id


async def _wait_for_terminal(client: httpx.AsyncClient, job_id: int) -> dict:
    deadline = asyncio.get_running_loop().time() + 180
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/exports/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        await asyncio.sleep(0.5)
    raise AssertionError(f"Export job did not finish within 180 seconds: {payload}")


def test_export_api_builds_downloadable_manifest_and_is_idempotent(test_settings) -> None:
    async def scenario() -> None:
        app = create_app(test_settings)
        async with app.router.lifespan_context(app):
            version_id = await _create_project(test_settings)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                missing_key = await client.post(f"/api/project-versions/{version_id}/exports")
                assert missing_key.status_code == 422
                created = await client.post(
                    f"/api/project-versions/{version_id}/exports", headers={"Idempotency-Key": "export-1"}
                )
                assert created.status_code == 202
                job_id = created.json()["id"]
                repeated = await client.post(
                    f"/api/project-versions/{version_id}/exports", headers={"Idempotency-Key": "export-1"}
                )
                assert repeated.status_code == 200
                assert repeated.json()["id"] == job_id
                finished = await _wait_for_terminal(client, job_id)
                assert finished["status"] == "completed", finished
                assert len(finished["artifacts"]) == 9
                manifest = next(item for item in finished["artifacts"] if item["artifact_kind"] == "manifest")
                download = await client.get(f"/api/exports/{job_id}/artifacts/{manifest['filename']}")
                assert download.status_code == 200
                assert "项目质量工作趋势分析.xlsx" in download.content.decode("utf-8")
                traversal = await client.get(f"/api/exports/{job_id}/artifacts/%2E%2E%2Fqualitym.db")
                assert traversal.status_code in {404, 422}
                first, second = await asyncio.gather(
                    client.post(f"/api/project-versions/{version_id}/exports", headers={"Idempotency-Key": "concurrent-1"}),
                    client.post(f"/api/project-versions/{version_id}/exports", headers={"Idempotency-Key": "concurrent-2"}),
                )
                assert sorted((first.status_code, second.status_code)) == [202, 409]
    run(scenario())


def test_active_job_conflict_and_restart_recovery(test_settings) -> None:
    async def scenario() -> None:
        await initialize_database(test_settings.database_path, test_settings.migrations_dir, test_settings.bootstrap_seed_path, test_settings.seed_schema_path)
        version_id = await _create_project(test_settings)
        first = await exports.create_job(test_settings.database_path, version_id, "first")
        with pytest.raises(aiosqlite.IntegrityError):
            await exports.create_job(test_settings.database_path, version_id, "second")
        assert await exports.mark_incomplete_jobs_failed(test_settings.database_path) == 1
        recovered = await exports.get_job(test_settings.database_path, first.id)
        assert recovered and recovered.status == "failed"
        assert recovered.error and recovered.error["code"] == "EXPORT_INTERRUPTED"
    run(scenario())


def test_manager_rejects_invalid_transition_and_tampered_artifact(test_settings, tmp_path) -> None:
    async def scenario() -> None:
        await initialize_database(test_settings.database_path, test_settings.migrations_dir, test_settings.bootstrap_seed_path, test_settings.seed_schema_path)
        version_id = await _create_project(test_settings)
        job = await exports.create_job(test_settings.database_path, version_id, "state")
        with pytest.raises(ValueError, match="Illegal export job transition"):
            await exports.transition_job(test_settings.database_path, job.id, frozenset({"queued"}), "completed", progress_step="bad")
        manager = ExportJobManager(test_settings.database_path, tmp_path / "exports", 1)
        await manager.start()
        try:
            retried = await manager.retry(job.id)
            assert retried.status == "queued"
            with pytest.raises(ExportServiceError) as invalid_retry:
                await manager.retry(job.id)
            assert invalid_retry.value.code == "EXPORT_RETRY_INVALID_STATE"
            for _ in range(200):
                retried_complete = await exports.get_job(test_settings.database_path, job.id)
                if retried_complete and retried_complete.status == "completed":
                    break
                await asyncio.sleep(0.05)
            assert retried_complete and retried_complete.status == "completed"
            created, accepted = await manager.create(version_id, "real")
            assert accepted
            for _ in range(200):
                complete = await exports.get_job(test_settings.database_path, created.id)
                if complete and complete.status == "completed":
                    break
                await asyncio.sleep(0.05)
            assert complete and complete.status == "completed"
            artifact = complete.artifacts[0]
            (Path(complete.output_dir) / artifact.filename).write_bytes(b"tampered")
            with pytest.raises(ExportServiceError) as tampered:
                await manager.artifact_path(created.id, artifact.filename)
            assert tampered.value.code == "EXPORT_ARTIFACT_TAMPERED"
            with pytest.raises(ExportServiceError) as unsafe:
                await manager.artifact_path(created.id, "../qualitym.db")
            assert unsafe.value.code == "EXPORT_ARTIFACT_INVALID_PATH"
        finally:
            await manager.stop()
    run(scenario())
