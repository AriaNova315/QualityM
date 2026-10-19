from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.application.export_service import ExportJobManager, ExportServiceError, job_payload
from app.repositories import exports


router = APIRouter(tags=["exports"])


def _manager(request: Request) -> ExportJobManager:
    return request.app.state.export_job_manager


def _http_error(exc: ExportServiceError) -> HTTPException:
    statuses = {
        "PROJECT_VERSION_NOT_FOUND": 404, "EXPORT_JOB_NOT_FOUND": 404,
        "EXPORT_ARTIFACT_NOT_FOUND": 404, "EXPORT_QUEUE_FULL": 429,
        "EXPORT_IDEMPOTENCY_KEY_INVALID": 422, "EXPORT_ARTIFACT_INVALID_PATH": 422,
        "EXPORT_ACTIVE_JOB_EXISTS": 409, "EXPORT_RETRY_INVALID_STATE": 409,
        "EXPORT_ARTIFACT_MISSING": 409, "EXPORT_ARTIFACT_TAMPERED": 409,
    }
    return HTTPException(status_code=statuses.get(exc.code, 422), detail={"code": exc.code, "message": str(exc)})


@router.post("/api/project-versions/{project_version_id}/exports", status_code=202)
async def create_export(
    request: Request, project_version_id: int, response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    try:
        job, created = await _manager(request).create(project_version_id, idempotency_key or "")
    except ExportServiceError as exc:
        raise _http_error(exc) from exc
    if not created:
        response.status_code = 200
    return job_payload(job)


@router.get("/api/exports/{job_id}")
async def read_export(request: Request, job_id: int) -> dict:
    job = await exports.get_job(request.app.state.settings.database_path, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "EXPORT_JOB_NOT_FOUND", "message": "导出作业不存在"})
    return job_payload(job)


@router.post("/api/exports/{job_id}/retry", status_code=202)
async def retry_export(request: Request, job_id: int) -> dict:
    try:
        return job_payload(await _manager(request).retry(job_id))
    except ExportServiceError as exc:
        raise _http_error(exc) from exc


@router.get("/api/exports/{job_id}/artifacts/{filename}")
async def download_export_artifact(request: Request, job_id: int, filename: str):
    try:
        _job, _artifact, path = await _manager(request).artifact_path(job_id, filename)
    except ExportServiceError as exc:
        raise _http_error(exc) from exc
    return FileResponse(path, filename=filename)
