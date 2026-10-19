from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from app.application.audit_service import (
    AuditWorkflowError,
    add_issue,
    delete_issue,
    get_audit,
    list_project_audits,
    set_item_conclusion,
    update_audit,
    update_issue,
)
from app.application.template_service import (
    TemplateServiceError,
    save_audit_issue_as_template,
)


router = APIRouter(tags=["audits"])


class AuditUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    auditor: str | None = Field(default=None, min_length=1, max_length=100)
    confirmation_state: Literal["draft", "confirmed"] | None = None


class ConclusionUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    conclusion: Literal["符合", "不符合", "不适用"]
    issue_template_ids: list[int] = Field(default_factory=list)
    clear_issues: bool = False


class IssueCreateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    candidate_template_id: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    severity: Literal["轻微", "一般", "严重"] | None = None
    responsible_person: str | None = Field(default=None, max_length=100)
    submitted_date: str | None = None
    planned_close_date: str | None = None
    corrective_action: str | None = None

    @model_validator(mode="after")
    def require_candidate_or_manual_content(self):
        if self.candidate_template_id is None and not all(
            (self.title, self.description, self.severity)
        ):
            raise ValueError("手工问题必须提供标题、描述和严重程度")
        return self


class IssueUpdateRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    severity: Literal["轻微", "一般", "严重"] | None = None
    responsible_person: str | None = Field(default=None, max_length=100)
    submitted_date: str | None = None
    planned_close_date: str | None = None
    corrective_action: str | None = None
    actual_close_date: str | None = None
    verification_date: str | None = None
    verification_conclusion: str | None = None
    suggested_closed: bool | None = None
    confirmed: bool | None = None


class SaveIssueAsTemplateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    corrective_suggestion: str = Field(min_length=1, max_length=4000)
    default_severity: Literal["轻微", "一般", "严重"]


def _http_error(exc: AuditWorkflowError) -> HTTPException:
    not_found = {
        "AUDIT_NOT_FOUND", "PROJECT_VERSION_NOT_FOUND", "CHECKLIST_ITEM_NOT_FOUND",
        "AUDIT_ISSUE_NOT_FOUND",
    }
    conflicts = {
        "AUDIT_REVISION_CONFLICT", "CHECKLIST_NONCOMPLIANT_WITHOUT_ISSUE",
        "CHECKLIST_COMPLIANT_WITH_ISSUES", "CHECKLIST_ISSUE_REQUIRES_NONCOMPLIANT",
        "CHECKLIST_LAST_ISSUE_REQUIRES_CONCLUSION_CHANGE", "AUDIT_CONFIRMATION_INVALID",
    }
    status = 404 if exc.code in not_found else 409 if exc.code in conflicts else 422
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


@router.get("/api/project-versions/{project_version_id}/audits")
async def read_project_audits(request: Request, project_version_id: int) -> list[dict]:
    try:
        return await list_project_audits(
            request.app.state.settings.database_path, project_version_id
        )
    except AuditWorkflowError as exc:
        raise _http_error(exc) from exc


@router.get("/api/audits/{audit_id}")
async def read_audit(request: Request, audit_id: int) -> dict:
    try:
        return await get_audit(request.app.state.settings.database_path, audit_id)
    except AuditWorkflowError as exc:
        raise _http_error(exc) from exc


@router.patch("/api/audits/{audit_id}")
async def patch_audit(request: Request, audit_id: int, body: AuditUpdateRequest) -> dict:
    changes = body.model_dump(exclude={"expected_revision"}, exclude_unset=True)
    try:
        return await update_audit(
            request.app.state.settings.database_path,
            audit_id,
            body.expected_revision,
            changes,
        )
    except AuditWorkflowError as exc:
        raise _http_error(exc) from exc


@router.put("/api/checklist-items/{item_id}/conclusion")
async def put_conclusion(
    request: Request, item_id: int, body: ConclusionUpdateRequest
) -> dict:
    try:
        return await set_item_conclusion(
            request.app.state.settings.database_path,
            item_id,
            body.expected_revision,
            body.conclusion,
            body.issue_template_ids,
            body.clear_issues,
        )
    except AuditWorkflowError as exc:
        raise _http_error(exc) from exc


@router.post("/api/checklist-items/{item_id}/issues", status_code=201)
async def post_issue(request: Request, item_id: int, body: IssueCreateRequest) -> dict:
    values = body.model_dump(
        exclude={"expected_revision", "candidate_template_id"}, exclude_unset=True
    )
    try:
        return await add_issue(
            request.app.state.settings.database_path,
            item_id,
            body.expected_revision,
            body.candidate_template_id,
            values,
        )
    except AuditWorkflowError as exc:
        raise _http_error(exc) from exc


@router.patch("/api/issues/{issue_id}")
async def patch_issue(request: Request, issue_id: int, body: IssueUpdateRequest) -> dict:
    changes = body.model_dump(exclude={"expected_revision"}, exclude_unset=True)
    try:
        return await update_issue(
            request.app.state.settings.database_path,
            issue_id,
            body.expected_revision,
            changes,
        )
    except AuditWorkflowError as exc:
        raise _http_error(exc) from exc


@router.post("/api/issues/{issue_id}/save-as-template", status_code=201)
async def save_issue_template(
    request: Request, issue_id: int, body: SaveIssueAsTemplateRequest
) -> dict:
    try:
        return await save_audit_issue_as_template(
            request.app.state.settings.database_path,
            issue_id,
            body.model_dump(),
        )
    except TemplateServiceError as exc:
        status = 404 if exc.code.endswith("NOT_FOUND") else 409 if (
            "LIMIT" in exc.code or "OVER_LIMIT" in exc.code
        ) else 422
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.delete("/api/issues/{issue_id}")
async def remove_issue(
    request: Request,
    issue_id: int,
    expected_revision: int = Query(ge=1),
    set_conclusion_to_compliant: bool = False,
) -> dict:
    try:
        return await delete_issue(
            request.app.state.settings.database_path,
            issue_id,
            expected_revision,
            set_conclusion_to_compliant,
        )
    except AuditWorkflowError as exc:
        raise _http_error(exc) from exc
