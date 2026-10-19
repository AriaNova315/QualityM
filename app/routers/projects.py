from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.application.project_settings_service import (
    ProjectSettingsError,
    get_project_version_settings,
    update_project_version_settings,
)
from app.application.project_version_service import (
    ProjectVersionError,
    confirm_delete,
    create_delete_preview,
    get_project_version,
    list_project_versions,
)


router = APIRouter(prefix="/api/project-versions", tags=["projects"])


class ProjectSettingsUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    config_item_number: str = Field(min_length=1, max_length=40)
    trend_conclusion_override: str | None = Field(default=None, max_length=1000)


class ProjectDeleteConfirmRequest(BaseModel):
    preview_token: str = Field(min_length=20, max_length=200)
    confirmation_text: str = Field(min_length=1, max_length=200)


def _http_error(exc: ProjectSettingsError) -> HTTPException:
    status = 404 if exc.code == "PROJECT_VERSION_NOT_FOUND" else 409
    if exc.code == "PROJECT_SETTINGS_INVALID":
        status = 422
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


def _version_http_error(exc: ProjectVersionError) -> HTTPException:
    if exc.code == "PROJECT_VERSION_NOT_FOUND":
        status = 404
    elif exc.code == "PROJECT_DELETE_CONFIRMATION_MISMATCH":
        status = 422
    else:
        status = 409
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


@router.get("")
async def read_project_versions(request: Request) -> list[dict]:
    return await list_project_versions(request.app.state.settings.database_path)


@router.get("/{project_version_id}")
async def read_project_version(request: Request, project_version_id: int) -> dict:
    try:
        return await get_project_version(request.app.state.settings.database_path, project_version_id)
    except ProjectVersionError as exc:
        raise _version_http_error(exc) from exc


@router.post("/{project_version_id}/delete-preview")
async def preview_project_version_delete(request: Request, project_version_id: int) -> dict:
    try:
        return await create_delete_preview(
            request.app.state.settings.database_path, project_version_id
        )
    except ProjectVersionError as exc:
        raise _version_http_error(exc) from exc


@router.post("/{project_version_id}/delete-confirm")
async def confirm_project_version_delete(
    request: Request, project_version_id: int, body: ProjectDeleteConfirmRequest
) -> dict:
    try:
        return await confirm_delete(
            request.app.state.settings.database_path,
            request.app.state.settings.generated_dir,
            project_version_id,
            body.preview_token,
            body.confirmation_text,
        )
    except ProjectVersionError as exc:
        raise _version_http_error(exc) from exc


@router.get("/{project_version_id}/settings")
async def read_project_settings(request: Request, project_version_id: int) -> dict:
    try:
        settings = await get_project_version_settings(
            request.app.state.settings.database_path, project_version_id
        )
    except ProjectSettingsError as exc:
        raise _http_error(exc) from exc
    return asdict(settings)


@router.patch("/{project_version_id}/settings")
async def patch_project_settings(
    request: Request,
    project_version_id: int,
    body: ProjectSettingsUpdateRequest,
) -> dict:
    try:
        settings = await update_project_version_settings(
            request.app.state.settings.database_path,
            project_version_id,
            body.expected_revision,
            body.config_item_number,
            body.trend_conclusion_override,
        )
    except ProjectSettingsError as exc:
        raise _http_error(exc) from exc
    return asdict(settings)
