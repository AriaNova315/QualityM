from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.application.import_preview_service import (
    PreviewTokenError,
    create_preview,
    get_preview,
)
from app.application.project_import_service import commit_preview
from app.importers.project_workbook import WorkbookStructureError
from app.safety import UploadValidationError


router = APIRouter(prefix="/api/imports", tags=["imports"])


class PhaseOverrideRequest(BaseModel):
    phase_overrides: dict[str, bool] = Field(default_factory=dict)


class ImportCommitRequest(BaseModel):
    phase_overrides: dict[str, bool] = Field(default_factory=dict)
    checklist_mappings: dict[str, str] = Field(default_factory=dict)
    save_global_mappings: bool = False
    config_item_number: str = Field(default="0.0", min_length=1, max_length=40)


def _http_error(exc: Exception, status_code: int = 422) -> HTTPException:
    code = getattr(exc, "code", "IMPORT_FAILED")
    return HTTPException(status_code=status_code, detail={"code": code, "message": str(exc)})


@router.post("/preview", status_code=201)
async def upload_preview(
    request: Request,
    file: Annotated[UploadFile, File(...)],
) -> dict:
    settings = request.app.state.settings
    content = await file.read(settings.upload_max_bytes + 1)
    try:
        preview = await create_preview(
            settings.database_path,
            settings.import_temp_dir,
            file.filename or "",
            content,
            settings.import_preview_ttl_seconds,
            settings.upload_max_bytes,
        )
    except WorkbookStructureError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "IMPORT_STRUCTURE_INVALID", "diagnostics": [d.to_dict() for d in exc.diagnostics]},
        ) from exc
    except UploadValidationError as exc:
        raise _http_error(exc) from exc
    return {
        "preview_id": preview.preview_id,
        "preview_token": preview.token,
        "expires_at": preview.expires_at,
        "preview": preview.payload,
    }


@router.get("/preview/{token}")
async def read_preview(request: Request, token: str) -> dict:
    try:
        return await get_preview(request.app.state.settings.database_path, token)
    except PreviewTokenError as exc:
        raise _http_error(exc, 404 if exc.code.endswith("INVALID") else 409) from exc


@router.post("/preview/{token}/confirm")
async def confirm_preview(request: Request, token: str, body: ImportCommitRequest) -> dict:
    try:
        result = await commit_preview(
            request.app.state.settings.database_path,
            token,
            body.phase_overrides,
            body.checklist_mappings,
            body.save_global_mappings,
            body.config_item_number,
        )
    except PreviewTokenError as exc:
        raise _http_error(exc, 404 if exc.code.endswith("INVALID") else 409) from exc
    return {
        "status": "consumed",
        "project_version_created": True,
        "project_id": result.project_id,
        "project_version_id": result.project_version_id,
        "version_no": result.version_no,
        "audit_instance_count": result.audit_instance_count,
        "diagnostics": result.diagnostics,
    }
