from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from app.application.export_snapshot_service import ExportSnapshotError, load_export_snapshot
from app.excel.company_trend_builder import build_complete_export_package
from app.excel.phase_workbook_builder import build_all_phase_workbooks
from app.excel.validator import validate_company_trend_workbooks, validate_phase_workbook
from app.repositories import exports


class ExportServiceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _diagnostic(code: str, message: str) -> dict:
    return {"code": code, "severity": "blocker", "scope": "export", "location": {}, "message": message, "resolution": "修正问题后重试导出。"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_kind(path: Path) -> str:
    if path.name == "manifest.txt": return "manifest"
    if path.name == "公司级质量保证过程审核记录.xlsx": return "company_workbook"
    if path.name == "项目质量工作趋势分析.xlsx": return "trend_workbook"
    return "phase_workbook"


def _build_and_validate(snapshot, generated_dir: Path, job_id: int):
    staging_root = generated_dir / ".staging" / str(job_id)
    staging_root.mkdir(parents=True, exist_ok=False)
    try:
        package = build_complete_export_package(snapshot, staging_root, build_all_phase_workbooks)
        for path in package.phase_workbooks:
            validate_phase_workbook(path)
        validate_company_trend_workbooks(package.company_workbook, package.trend_workbook)
        # The builder names directories to the second.  A completed job can be
        # followed by another request inside that second, so add the persistent
        # job id before the atomic publish to avoid a timestamp collision.
        final_dir = generated_dir / f"{package.output_dir.name}-J{job_id:06d}"
        if final_dir.exists():
            raise FileExistsError(f"Export output already exists: {final_dir.name}")
        os.replace(package.output_dir, final_dir)
        records = tuple(
            exports.ExportArtifactRecord(0, _artifact_kind(path), path.name, _sha256(final_dir / path.name), (final_dir / path.name).stat().st_size)
            for path in (*package.phase_workbooks, *((package.company_workbook,) if package.company_workbook else ()), package.trend_workbook, package.manifest)
        )
        return final_dir, records
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)


class ExportJobManager:
    """Owns one bounded worker; database state remains the source of truth."""

    def __init__(self, database_path: Path, generated_dir: Path, queue_max: int) -> None:
        if queue_max < 1:
            raise ValueError("export_queue_max must be at least 1")
        self.database_path = database_path
        self.generated_dir = generated_dir
        self._queue: asyncio.Queue[int | None] = asyncio.Queue(maxsize=queue_max)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qualitym-export")
        self._worker: asyncio.Task | None = None
        self._submit_lock = asyncio.Lock()

    async def start(self) -> None:
        await exports.mark_incomplete_jobs_failed(self.database_path)
        self._worker = asyncio.create_task(self._run(), name="qualitym-export-worker")

    async def stop(self) -> None:
        if self._worker is not None:
            await self._queue.put(None)
            await self._worker
            self._worker = None
        self._executor.shutdown(wait=True, cancel_futures=False)

    async def create(self, project_version_id: int, idempotency_key: str) -> tuple[exports.ExportJobRecord, bool]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ExportServiceError("EXPORT_IDEMPOTENCY_KEY_INVALID", "Idempotency-Key 必须为 1 至 200 个字符")
        async with self._submit_lock:
            prior = await exports.get_job_by_idempotency(self.database_path, project_version_id, idempotency_key)
            if prior is not None:
                return prior, False
            if self._queue.full():
                raise ExportServiceError("EXPORT_QUEUE_FULL", "导出队列已满，请稍后重试")
            try:
                job = await exports.create_job(self.database_path, project_version_id, idempotency_key)
            except LookupError as exc:
                raise ExportServiceError("PROJECT_VERSION_NOT_FOUND", "项目数据版本不存在") from exc
            except Exception as exc:
                if "idx_export_jobs_one_active_per_version" in str(exc) or "UNIQUE constraint failed: export_jobs.project_version_id" in str(exc):
                    raise ExportServiceError("EXPORT_ACTIVE_JOB_EXISTS", "该项目数据版本已有进行中的导出作业") from exc
                raise
            self._queue.put_nowait(job.id)
            return job, True

    async def retry(self, job_id: int) -> exports.ExportJobRecord:
        async with self._submit_lock:
            job = await exports.get_job(self.database_path, job_id)
            if job is None:
                raise ExportServiceError("EXPORT_JOB_NOT_FOUND", "导出作业不存在")
            if job.status != "failed":
                raise ExportServiceError("EXPORT_RETRY_INVALID_STATE", "仅失败的导出作业可以重试")
            if self._queue.full():
                raise ExportServiceError("EXPORT_QUEUE_FULL", "导出队列已满，请稍后重试")
            queued = await exports.transition_job(self.database_path, job_id, frozenset({"failed"}), "queued", progress_step="queued")
            if queued is None:
                raise ExportServiceError("EXPORT_RETRY_INVALID_STATE", "导出作业状态已变化，请刷新后重试")
            self._queue.put_nowait(job_id)
            return queued

    async def _run(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                if job_id is None:
                    return
                await self._process(job_id)
            finally:
                self._queue.task_done()

    async def _process(self, job_id: int) -> None:
        job = await exports.transition_job(self.database_path, job_id, frozenset({"queued"}), "snapshotting", progress_step="snapshotting")
        if job is None:
            return
        try:
            snapshot = await load_export_snapshot(self.database_path, job.project_version_id)
            if await exports.transition_job(self.database_path, job_id, frozenset({"snapshotting"}), "building", progress_step="building") is None:
                return
            loop = asyncio.get_running_loop()
            final_dir, artifacts = await loop.run_in_executor(self._executor, _build_and_validate, snapshot, self.generated_dir, job_id)
            if await exports.transition_job(self.database_path, job_id, frozenset({"building"}), "validating", progress_step="validating", output_dir=str(final_dir)) is None:
                return
            await exports.replace_artifacts(self.database_path, job_id, artifacts)
            await exports.transition_job(self.database_path, job_id, frozenset({"validating"}), "completed", progress_step="completed", output_dir=str(final_dir))
        except ExportSnapshotError as exc:
            await self._fail(job_id, exc.code, str(exc))
        except Exception as exc:
            await self._fail(job_id, "EXPORT_BUILD_FAILED", str(exc))

    async def _fail(self, job_id: int, code: str, message: str) -> None:
        await exports.transition_job(self.database_path, job_id, exports.ACTIVE_STATUSES, "failed", progress_step="failed", error=_diagnostic(code, message))

    async def artifact_path(self, job_id: int, filename: str) -> tuple[exports.ExportJobRecord, exports.ExportArtifactRecord, Path]:
        if not filename or Path(filename).name != filename:
            raise ExportServiceError("EXPORT_ARTIFACT_INVALID_PATH", "导出文件名不安全")
        job = await exports.get_job(self.database_path, job_id)
        if job is None:
            raise ExportServiceError("EXPORT_JOB_NOT_FOUND", "导出作业不存在")
        artifact = next((item for item in job.artifacts if item.filename == filename), None)
        if artifact is None or not job.output_dir:
            raise ExportServiceError("EXPORT_ARTIFACT_NOT_FOUND", "导出文件不存在")
        path = Path(job.output_dir) / filename
        if not path.is_file():
            await self._fail(job_id, "EXPORT_ARTIFACT_MISSING", "导出文件已缺失")
            raise ExportServiceError("EXPORT_ARTIFACT_MISSING", "导出文件已缺失")
        if _sha256(path) != artifact.sha256:
            await self._fail(job_id, "EXPORT_ARTIFACT_TAMPERED", "导出文件校验失败")
            raise ExportServiceError("EXPORT_ARTIFACT_TAMPERED", "导出文件校验失败")
        return job, artifact, path


def job_payload(job: exports.ExportJobRecord) -> dict:
    payload = asdict(job)
    payload.pop("output_dir", None)
    return payload
