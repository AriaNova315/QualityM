from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.application.template_service import (
    TemplateServiceError,
    checklist_library,
    commit_import_preview,
    copy_issue,
    create_import_preview,
    create_issue,
    create_template_entity,
    flat_template_rows,
    restore_builtin_entity,
    update_issue,
    update_template_entity,
)
from app.excel.template_library import build_template_library
from app.safety import UploadValidationError

router = APIRouter(prefix="/api/templates", tags=["templates"])


class ChecklistCreate(BaseModel):
    stable_key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    audit_kind: str
    display_order: int = Field(default=1, ge=1)


class GroupCreate(BaseModel):
    parent_id: int = Field(gt=0)
    stable_key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    display_order: int = Field(default=1, ge=1)


class ItemCreate(BaseModel):
    parent_id: int = Field(gt=0)
    stable_key: str = Field(min_length=1, max_length=120)
    display_no: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=1)
    starred: bool = False
    display_order: int = Field(default=1, ge=1)


class EntityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    audit_kind: str | None = None
    display_no: str | None = Field(default=None, min_length=1, max_length=40)
    content: str | None = Field(default=None, min_length=1)
    starred: bool | None = None
    enabled: bool | None = None
    display_order: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1)
    corrective_suggestion: str | None = Field(default=None, min_length=1)
    default_severity: str | None = None


class IssueCreate(BaseModel):
    item_id: int = Field(gt=0)
    stable_key: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    corrective_suggestion: str = Field(min_length=1)
    default_severity: str
    placeholders: list[str] = Field(default_factory=list)
    display_order: int | None = Field(default=None, ge=1)


class IssueCopy(BaseModel):
    stable_key: str = Field(min_length=1, max_length=160)


class IssueUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1)
    corrective_suggestion: str | None = Field(default=None, min_length=1)
    default_severity: str | None = None
    enabled: bool | None = None
    display_order: int | None = Field(default=None, ge=1)


class PreviewCommit(BaseModel):
    preview_token: str = Field(min_length=20, max_length=200)


def _error(error: TemplateServiceError) -> HTTPException:
    if error.code.endswith("NOT_FOUND"):
        status = 404
    elif any(marker in error.code for marker in ("CONFLICT", "INVARIANT", "MINIMUM", "DEFAULT", "PREVIEW")):
        status = 409
    else:
        status = 422
    return HTTPException(status_code=status, detail={"code": error.code, "message": str(error)})


@router.get("/checklists")
async def get_checklists(request: Request):
    return await checklist_library(request.app.state.settings.database_path)


@router.post("/checklists")
async def post_checklist(request: Request, body: ChecklistCreate):
    try:
        return await create_template_entity(request.app.state.settings.database_path, "checklists", None, body.model_dump())
    except TemplateServiceError as error:
        raise _error(error) from error


@router.post("/groups")
async def post_group(request: Request, body: GroupCreate):
    try:
        return await create_template_entity(request.app.state.settings.database_path, "groups", body.parent_id, body.model_dump(exclude={"parent_id"}))
    except TemplateServiceError as error:
        raise _error(error) from error


@router.post("/items")
async def post_item(request: Request, body: ItemCreate):
    try:
        return await create_template_entity(request.app.state.settings.database_path, "items", body.parent_id, body.model_dump(exclude={"parent_id"}))
    except TemplateServiceError as error:
        raise _error(error) from error


@router.patch("/{entity_type}/{entity_id}")
async def patch_entity(request: Request, entity_type: str, entity_id: int, body: EntityUpdate):
    if entity_type not in {"checklists", "groups", "items", "issues"}:
        raise HTTPException(status_code=404, detail={"code": "TEMPLATE_ENTITY_NOT_FOUND", "message": "模板实体不存在"})
    try:
        if entity_type == "issues":
            return await update_issue(request.app.state.settings.database_path, entity_id, body.model_dump(exclude_none=True))
        return await update_template_entity(request.app.state.settings.database_path, entity_type, entity_id, body.model_dump(exclude_none=True))
    except TemplateServiceError as error:
        raise _error(error) from error


@router.post("/issues")
async def post_issue(request: Request, body: IssueCreate):
    try:
        return await create_issue(request.app.state.settings.database_path, body.item_id, body.model_dump(exclude={"item_id"}))
    except TemplateServiceError as error:
        raise _error(error) from error


@router.patch("/issues/{issue_id}")
async def patch_issue(request: Request, issue_id: int, body: IssueUpdate):
    try:
        return await update_issue(request.app.state.settings.database_path, issue_id, body.model_dump(exclude_none=True))
    except TemplateServiceError as error:
        raise _error(error) from error


@router.post("/issues/{issue_id}/copy")
async def post_issue_copy(request: Request, issue_id: int, body: IssueCopy):
    try:
        return await copy_issue(request.app.state.settings.database_path, issue_id, body.stable_key)
    except TemplateServiceError as error:
        raise _error(error) from error


@router.post("/{entity_type}/{entity_id}/restore")
async def post_restore(request: Request, entity_type: str, entity_id: int):
    try:
        settings = request.app.state.settings
        return await restore_builtin_entity(settings.database_path, settings.bootstrap_seed_path, entity_type, entity_id)
    except TemplateServiceError as error:
        raise _error(error) from error


@router.post("/export")
async def export_templates(request: Request):
    descriptor, raw_path = tempfile.mkstemp(prefix="qualitym-template-library-", suffix=".xlsx")
    os.close(descriptor)
    path = Path(raw_path)
    build_template_library(await flat_template_rows(request.app.state.settings.database_path), path)
    return FileResponse(path, filename="QualityM-模板库.xlsx", background=BackgroundTask(path.unlink, missing_ok=True))


@router.post("/import-preview")
async def import_preview(request: Request, file: UploadFile):
    settings = request.app.state.settings
    try:
        return await create_import_preview(settings.database_path, await file.read(settings.upload_max_bytes + 1), ttl_seconds=settings.import_preview_ttl_seconds, max_bytes=settings.upload_max_bytes)
    except (TemplateServiceError, UploadValidationError, ValueError) as error:
        code = getattr(error, "code", "TEMPLATE_IMPORT_INVALID")
        raise HTTPException(status_code=422, detail={"code": code, "message": str(error)}) from error


@router.post("/import-commit")
async def import_commit(request: Request, body: PreviewCommit):
    try:
        return await commit_import_preview(request.app.state.settings.database_path, body.preview_token)
    except TemplateServiceError as error:
        raise _error(error) from error
