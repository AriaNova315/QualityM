from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from app.database import connect, transaction
from app.domain.calendar import planned_close_date
from app.domain.issue_template_policy import (
    IssuePolicyViolation,
    validate_close_confirmation,
    validate_issue_severity,
)


DEFAULT_VERIFICATION_CONCLUSION = "纠正措施已实施，不符合项问题均按期关闭。"


class AuditWorkflowError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _validate_severity(starred: bool, severity: str) -> None:
    try:
        validate_issue_severity(starred=starred, severity=severity)
    except IssuePolicyViolation as exc:
        raise AuditWorkflowError(exc.code, str(exc)) from exc


async def _audit_row(connection: aiosqlite.Connection, audit_id: int):
    row = await (
        await connection.execute(
            """
            SELECT ai.*, p.project_code, p.project_name, pv.version_no, pv.project_leader,
                   ph.name AS phase_name, ph.end_date AS phase_end_date,
                   qa.name AS activity_name, ci.name AS config_item_name,
                   tn.node_date AS tracking_node_date
            FROM audit_instances ai
            JOIN project_versions pv ON pv.id = ai.project_version_id
            JOIN projects p ON p.id = pv.project_id
            JOIN phases ph ON ph.id = ai.phase_id
            JOIN quality_activities qa ON qa.id = ai.activity_id
            LEFT JOIN config_items ci ON ci.id = ai.config_item_id
            LEFT JOIN tracking_nodes tn ON tn.id = ai.tracking_node_id
            WHERE ai.id = ?
            """,
            (audit_id,),
        )
    ).fetchone()
    if row is None:
        raise AuditWorkflowError("AUDIT_NOT_FOUND", "审核实例不存在")
    return row


async def _require_revision(
    connection: aiosqlite.Connection, audit_id: int, expected_revision: int
):
    row = await _audit_row(connection, audit_id)
    if int(row["revision"]) != expected_revision:
        raise AuditWorkflowError("AUDIT_REVISION_CONFLICT", "审核记录已被修改，请刷新后重试")
    return row


async def _bump_revision(
    connection: aiosqlite.Connection, audit_id: int, expected_revision: int
) -> int:
    cursor = await connection.execute(
        """
        UPDATE audit_instances
        SET revision = revision + 1, user_edited = 1, updated_at = ?
        WHERE id = ? AND revision = ?
        """,
        (datetime.now(UTC).isoformat(), audit_id, expected_revision),
    )
    if cursor.rowcount != 1:
        raise AuditWorkflowError("AUDIT_REVISION_CONFLICT", "审核记录已被修改，请刷新后重试")
    return expected_revision + 1


def _diagnostics(audit: dict[str, Any], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if audit["confirmation_state"] != "confirmed":
        result.append(
            {
                "code": "AUDIT_UNCONFIRMED",
                "severity": "warning",
                "scope": "audit",
                "location": {"audit_id": audit["id"]},
                "message": "审核记录尚未确认",
                "resolution": "请完成检查项和问题处理后，将审核状态改为“已确认”并保存。",
            }
        )
    for issue in issues:
        if not issue["confirmed"]:
            result.append(
                {
                    "code": "AUDIT_ISSUE_CLOSURE_UNCONFIRMED",
                    "severity": "warning",
                    "scope": "issue",
                    "location": {"audit_id": audit["id"], "issue_id": issue["id"]},
                    "message": f"问题“{issue['title']}”尚未确认关闭",
                    "resolution": "请核对整改和验证信息，勾选“关闭已确认”后保存问题。",
                }
            )
    return result


async def list_project_audits(database_path: Path, project_version_id: int) -> list[dict[str, Any]]:
    async with connect(database_path) as connection:
        exists = await (
            await connection.execute(
                "SELECT 1 FROM project_versions WHERE id = ?", (project_version_id,)
            )
        ).fetchone()
        if exists is None:
            raise AuditWorkflowError("PROJECT_VERSION_NOT_FOUND", "项目数据版本不存在")
        rows = await (
            await connection.execute(
                """
                SELECT ai.id, ai.sequence_no, ai.audit_kind, ai.report_scope, ai.audit_date,
                       ai.auditor, ai.confirmation_state, ai.revision, ph.name AS phase_name,
                       qa.name AS activity_name, ci.name AS config_item_name,
                       COUNT(DISTINCT cii.id) AS checklist_item_count,
                       COUNT(DISTINCT nc.id) AS issue_count,
                       COALESCE(SUM(CASE WHEN nc.confirmed = 1 THEN 1 ELSE 0 END), 0)
                           AS confirmed_issue_count
                FROM audit_instances ai
                JOIN phases ph ON ph.id = ai.phase_id
                JOIN quality_activities qa ON qa.id = ai.activity_id
                LEFT JOIN config_items ci ON ci.id = ai.config_item_id
                LEFT JOIN checklist_instance_groups cig ON cig.audit_instance_id = ai.id
                LEFT JOIN checklist_instance_items cii ON cii.instance_group_id = cig.id
                LEFT JOIN nonconformities nc ON nc.checklist_instance_item_id = cii.id
                WHERE ai.project_version_id = ?
                GROUP BY ai.id
                ORDER BY ai.sequence_no
                """,
                (project_version_id,),
            )
        ).fetchall()
    return [dict(row) for row in rows]


async def get_audit(database_path: Path, audit_id: int) -> dict[str, Any]:
    async with connect(database_path) as connection:
        audit_row = await _audit_row(connection, audit_id)
        group_rows = await (
            await connection.execute(
                """
                SELECT * FROM checklist_instance_groups
                WHERE audit_instance_id = ? ORDER BY display_order, id
                """,
                (audit_id,),
            )
        ).fetchall()
        item_rows = await (
            await connection.execute(
                """
                SELECT cii.* FROM checklist_instance_items cii
                JOIN checklist_instance_groups cig ON cig.id = cii.instance_group_id
                WHERE cig.audit_instance_id = ? ORDER BY cig.display_order, cii.display_order, cii.id
                """,
                (audit_id,),
            )
        ).fetchall()
        candidate_rows = await (
            await connection.execute(
                """
                SELECT ciit.* FROM checklist_instance_issue_templates ciit
                JOIN checklist_instance_items cii ON cii.id = ciit.checklist_instance_item_id
                JOIN checklist_instance_groups cig ON cig.id = cii.instance_group_id
                WHERE cig.audit_instance_id = ?
                ORDER BY cig.display_order, cii.display_order, ciit.display_order, ciit.id
                """,
                (audit_id,),
            )
        ).fetchall()
        issue_rows = await (
            await connection.execute(
                """
                SELECT nc.*, ciit.corrective_suggestion_snapshot
                       AS template_corrective_suggestion
                FROM nonconformities nc
                JOIN checklist_instance_items cii ON cii.id = nc.checklist_instance_item_id
                JOIN checklist_instance_groups cig ON cig.id = cii.instance_group_id
                LEFT JOIN checklist_instance_issue_templates ciit
                  ON ciit.id = nc.instance_issue_template_id
                WHERE cig.audit_instance_id = ? ORDER BY nc.id
                """,
                (audit_id,),
            )
        ).fetchall()

    audit = dict(audit_row)
    candidates_by_item: dict[int, list[dict[str, Any]]] = {}
    for row in candidate_rows:
        candidate = dict(row)
        candidate["placeholders_snapshot"] = json.loads(
            candidate.pop("placeholders_snapshot_json")
        )
        candidates_by_item.setdefault(int(row["checklist_instance_item_id"]), []).append(candidate)
    issues_by_item: dict[int, list[dict[str, Any]]] = {}
    issues = [dict(row) for row in issue_rows]
    for issue in issues:
        issues_by_item.setdefault(int(issue["checklist_instance_item_id"]), []).append(issue)
    items_by_group: dict[int, list[dict[str, Any]]] = {}
    for row in item_rows:
        item = dict(row)
        item["candidate_templates"] = candidates_by_item.get(int(row["id"]), [])
        item["issues"] = issues_by_item.get(int(row["id"]), [])
        items_by_group.setdefault(int(row["instance_group_id"]), []).append(item)
    groups = []
    for row in group_rows:
        group = dict(row)
        group["items"] = items_by_group.get(int(row["id"]), [])
        groups.append(group)
    audit["groups"] = groups
    audit["diagnostics"] = _diagnostics(audit, issues)
    return audit


async def update_audit(
    database_path: Path,
    audit_id: int,
    expected_revision: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    allowed = {"auditor", "confirmation_state"}
    if not changes or set(changes) - allowed:
        raise AuditWorkflowError("AUDIT_UPDATE_INVALID", "审核更新字段无效")
    if "auditor" in changes:
        if not isinstance(changes["auditor"], str) or not changes["auditor"].strip():
            raise AuditWorkflowError("AUDIT_UPDATE_INVALID", "审核人不能为空")
        changes["auditor"] = changes["auditor"].strip()
    if changes.get("confirmation_state") not in {None, "draft", "confirmed"}:
        raise AuditWorkflowError("AUDIT_UPDATE_INVALID", "确认状态无效")
    async with connect(database_path) as connection:
        async with transaction(connection):
            await _require_revision(connection, audit_id, expected_revision)
            if changes.get("confirmation_state") == "confirmed":
                invalid = await (
                    await connection.execute(
                        """
                        SELECT cii.id FROM checklist_instance_items cii
                        JOIN checklist_instance_groups cig ON cig.id = cii.instance_group_id
                        LEFT JOIN nonconformities nc ON nc.checklist_instance_item_id = cii.id
                        WHERE cig.audit_instance_id = ?
                        GROUP BY cii.id
                        HAVING cii.conclusion IS NULL
                            OR (cii.conclusion = '不符合' AND COUNT(nc.id) = 0)
                            OR (cii.conclusion IN ('符合', '不适用') AND COUNT(nc.id) > 0)
                            OR SUM(CASE WHEN nc.id IS NOT NULL AND nc.confirmed = 0 THEN 1 ELSE 0 END) > 0
                        LIMIT 1
                        """,
                        (audit_id,),
                    )
                ).fetchone()
                if invalid is not None:
                    raise AuditWorkflowError(
                        "AUDIT_CONFIRMATION_INVALID",
                        "仍有检查项结论不完整或问题尚未确认关闭",
                    )
            assignments = []
            values = []
            for field in ("auditor", "confirmation_state"):
                if field in changes:
                    assignments.append(f"{field} = ?")
                    values.append(changes[field])
            values.extend([datetime.now(UTC).isoformat(), audit_id, expected_revision])
            cursor = await connection.execute(
                f"UPDATE audit_instances SET {', '.join(assignments)}, "
                "revision = revision + 1, user_edited = 1, updated_at = ? "
                "WHERE id = ? AND revision = ?",
                tuple(values),
            )
            if cursor.rowcount != 1:
                raise AuditWorkflowError("AUDIT_REVISION_CONFLICT", "审核记录已被修改，请刷新后重试")
    return await get_audit(database_path, audit_id)


async def _item_context(connection: aiosqlite.Connection, item_id: int):
    row = await (
        await connection.execute(
            """
            SELECT cii.*, cig.audit_instance_id
            FROM checklist_instance_items cii
            JOIN checklist_instance_groups cig ON cig.id = cii.instance_group_id
            WHERE cii.id = ?
            """,
            (item_id,),
        )
    ).fetchone()
    if row is None:
        raise AuditWorkflowError("CHECKLIST_ITEM_NOT_FOUND", "检查项不存在")
    return row


def _planned_close(audit) -> str:
    return planned_close_date(
        "product" if audit["audit_kind"] == "product" else "process",
        date.fromisoformat(audit["audit_date"]),
        date.fromisoformat(audit["tracking_node_date"]) if audit["tracking_node_date"] else None,
        date.fromisoformat(audit["phase_end_date"]),
    ).planned_close_date.isoformat()


async def _insert_candidate_issue(
    connection: aiosqlite.Connection,
    item_id: int,
    candidate_id: int,
    audit,
) -> int:
    candidate = await (
        await connection.execute(
            """
            SELECT * FROM checklist_instance_issue_templates
            WHERE id = ? AND checklist_instance_item_id = ?
            """,
            (candidate_id, item_id),
        )
    ).fetchone()
    if candidate is None:
        raise AuditWorkflowError("AUDIT_ISSUE_TEMPLATE_INVALID", "问题模板不属于该检查项")
    item = await (
        await connection.execute(
            "SELECT starred_snapshot FROM checklist_instance_items WHERE id = ?",
            (item_id,),
        )
    ).fetchone()
    _validate_severity(bool(item["starred_snapshot"]), candidate["default_severity_snapshot"])
    close_date = _planned_close(audit)
    cursor = await connection.execute(
        """
        INSERT INTO nonconformities(
            checklist_instance_item_id, instance_issue_template_id,
            template_version_snapshot, title, description, severity,
            responsible_person, submitted_date, planned_close_date,
            corrective_action, actual_close_date, verification_date,
            verification_conclusion, suggested_closed, confirmed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
        """,
        (
            item_id, candidate_id, candidate["template_version_snapshot"],
            candidate["title_snapshot"], candidate["description_snapshot"],
            candidate["default_severity_snapshot"], audit["project_leader"],
            audit["audit_date"], close_date, candidate["corrective_suggestion_snapshot"],
            close_date, close_date, DEFAULT_VERIFICATION_CONCLUSION,
        ),
    )
    return int(cursor.lastrowid)


async def set_item_conclusion(
    database_path: Path,
    item_id: int,
    expected_revision: int,
    conclusion: str,
    issue_template_ids: list[int],
    clear_issues: bool,
) -> dict[str, Any]:
    if conclusion not in {"符合", "不符合", "不适用"}:
        raise AuditWorkflowError("CHECKLIST_CONCLUSION_INVALID", "检查结论无效")
    async with connect(database_path) as connection:
        async with transaction(connection):
            item = await _item_context(connection, item_id)
            audit = await _require_revision(
                connection, int(item["audit_instance_id"]), expected_revision
            )
            count = int(
                (
                    await (
                        await connection.execute(
                            "SELECT COUNT(*) FROM nonconformities WHERE checklist_instance_item_id = ?",
                            (item_id,),
                        )
                    ).fetchone()
                )[0]
            )
            if conclusion == "不符合":
                if count == 0 and not issue_template_ids:
                    raise AuditWorkflowError(
                        "CHECKLIST_NONCOMPLIANT_WITHOUT_ISSUE",
                        "不符合检查项至少需要选择一条问题模板",
                    )
                for candidate_id in dict.fromkeys(issue_template_ids):
                    await _insert_candidate_issue(connection, item_id, candidate_id, audit)
            elif count and not clear_issues:
                raise AuditWorkflowError(
                    "CHECKLIST_COMPLIANT_WITH_ISSUES",
                    "符合或不适用检查项不能保留问题；请确认清除问题",
                )
            elif count:
                await connection.execute(
                    "DELETE FROM nonconformities WHERE checklist_instance_item_id = ?", (item_id,)
                )
            await connection.execute(
                "UPDATE checklist_instance_items SET conclusion = ? WHERE id = ?",
                (conclusion, item_id),
            )
            revision = await _bump_revision(
                connection, int(item["audit_instance_id"]), expected_revision
            )
    return {"audit_id": int(item["audit_instance_id"]), "item_id": item_id, "revision": revision}


async def add_issue(
    database_path: Path,
    item_id: int,
    expected_revision: int,
    candidate_id: int | None,
    values: dict[str, Any],
) -> dict[str, Any]:
    async with connect(database_path) as connection:
        async with transaction(connection):
            item = await _item_context(connection, item_id)
            audit_id = int(item["audit_instance_id"])
            audit = await _require_revision(connection, audit_id, expected_revision)
            if item["conclusion"] != "不符合":
                await connection.execute(
                    "UPDATE checklist_instance_items SET conclusion = '不符合' WHERE id = ?",
                    (item_id,),
                )
            if candidate_id is not None:
                issue_id = await _insert_candidate_issue(connection, item_id, candidate_id, audit)
            else:
                required = ("title", "description", "severity")
                if any(not str(values.get(field, "")).strip() for field in required):
                    raise AuditWorkflowError("AUDIT_ISSUE_INVALID", "手工问题缺少标题、描述或严重程度")
                _validate_issue_values(
                    values,
                    starred=bool(item["starred_snapshot"]),
                    audit_date=audit["audit_date"],
                    phase_end_date=audit["phase_end_date"],
                )
                close_date = _planned_close(audit)
                planned_date = values.get("planned_close_date") or close_date
                cursor = await connection.execute(
                    """
                    INSERT INTO nonconformities(
                        checklist_instance_item_id, title, description, severity,
                        responsible_person, submitted_date, planned_close_date,
                        corrective_action, actual_close_date, verification_date,
                        verification_conclusion, suggested_closed, confirmed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
                    """,
                    (
                        item_id, values["title"].strip(), values["description"].strip(),
                        values["severity"], values.get("responsible_person") or audit["project_leader"],
                        values.get("submitted_date") or audit["audit_date"],
                        planned_date,
                        values.get("corrective_action"),
                        planned_date,
                        planned_date,
                        DEFAULT_VERIFICATION_CONCLUSION,
                    ),
                )
                issue_id = int(cursor.lastrowid)
            revision = await _bump_revision(connection, audit_id, expected_revision)
    return {"audit_id": audit_id, "item_id": item_id, "issue_id": issue_id, "revision": revision}


_ISSUE_FIELDS = {
    "title", "description", "severity", "responsible_person", "submitted_date",
    "planned_close_date", "corrective_action", "actual_close_date", "verification_date",
    "verification_conclusion", "suggested_closed", "confirmed",
}


def _validate_issue_values(
    values: dict[str, Any],
    *,
    starred: bool | None = None,
    audit_date: str | None = None,
    phase_end_date: str | None = None,
) -> None:
    if "severity" in values:
        if starred is None:
            if values["severity"] not in {"轻微", "一般", "严重"}:
                raise AuditWorkflowError("AUDIT_ISSUE_INVALID", "问题严重程度无效")
        else:
            _validate_severity(starred, values["severity"])
    required_text_labels = {"title": "问题标题", "description": "问题描述"}
    for field, label in required_text_labels.items():
        if field in values and (
            not isinstance(values[field], str) or not values[field].strip()
        ):
            raise AuditWorkflowError("AUDIT_ISSUE_INVALID", f"{label}不能为空")
    parsed: dict[str, date] = {}
    for field in ("submitted_date", "planned_close_date", "actual_close_date", "verification_date"):
        if values.get(field):
            try:
                parsed[field] = date.fromisoformat(values[field])
            except ValueError as exc:
                raise AuditWorkflowError("AUDIT_ISSUE_DATE_INVALID", f"{field} 日期无效") from exc
    if parsed.get("actual_close_date") and parsed.get("submitted_date") and parsed["actual_close_date"] < parsed["submitted_date"]:
        raise AuditWorkflowError("AUDIT_ISSUE_DATE_INVALID", "实际关闭日期不能早于提交日期")
    if parsed.get("planned_close_date") and parsed.get("submitted_date") and parsed["planned_close_date"] < parsed["submitted_date"]:
        raise AuditWorkflowError("AUDIT_ISSUE_DATE_INVALID", "计划关闭日期不能早于提交日期")
    if parsed.get("verification_date") and parsed.get("actual_close_date") and parsed["verification_date"] < parsed["actual_close_date"]:
        raise AuditWorkflowError("AUDIT_ISSUE_DATE_INVALID", "验证日期不能早于实际关闭日期")
    try:
        validate_close_confirmation(values)
    except IssuePolicyViolation as exc:
        raise AuditWorkflowError(exc.code, str(exc)) from exc
    lower = date.fromisoformat(audit_date) if audit_date else None
    upper = date.fromisoformat(phase_end_date) if phase_end_date else None
    if lower and any(value < lower for value in parsed.values()):
        raise AuditWorkflowError("AUDIT_ISSUE_DATE_INVALID", "问题日期不能早于审核日期")
    if upper and any(value > upper for value in parsed.values()):
        raise AuditWorkflowError("AUDIT_ISSUE_DATE_INVALID", "问题日期不能晚于阶段结束日期")


async def update_issue(
    database_path: Path,
    issue_id: int,
    expected_revision: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    if not changes or set(changes) - _ISSUE_FIELDS:
        raise AuditWorkflowError("AUDIT_ISSUE_INVALID", "问题更新字段无效")
    async with connect(database_path) as connection:
        async with transaction(connection):
            row = await (
                await connection.execute(
                    """
                    SELECT nc.*, cig.audit_instance_id, ai.audit_date,
                           ph.end_date AS phase_end_date, cii.starred_snapshot
                    FROM nonconformities nc
                    JOIN checklist_instance_items cii ON cii.id = nc.checklist_instance_item_id
                    JOIN checklist_instance_groups cig ON cig.id = cii.instance_group_id
                    JOIN audit_instances ai ON ai.id = cig.audit_instance_id
                    JOIN phases ph ON ph.id = ai.phase_id
                    WHERE nc.id = ?
                    """,
                    (issue_id,),
                )
            ).fetchone()
            if row is None:
                raise AuditWorkflowError("AUDIT_ISSUE_NOT_FOUND", "问题不存在")
            audit_id = int(row["audit_instance_id"])
            await _require_revision(connection, audit_id, expected_revision)
            merged = {field: row[field] for field in _ISSUE_FIELDS}
            merged.update(changes)
            _validate_issue_values(
                merged,
                starred=bool(row["starred_snapshot"]),
                audit_date=row["audit_date"],
                phase_end_date=row["phase_end_date"],
            )
            if changes.get("confirmed"):
                changes["suggested_closed"] = False
            assignments = [f"{field} = ?" for field in changes]
            await connection.execute(
                f"UPDATE nonconformities SET {', '.join(assignments)} WHERE id = ?",
                (*changes.values(), issue_id),
            )
            revision = await _bump_revision(connection, audit_id, expected_revision)
    return {"audit_id": audit_id, "issue_id": issue_id, "revision": revision}


async def delete_issue(
    database_path: Path,
    issue_id: int,
    expected_revision: int,
    set_conclusion_to_compliant: bool,
) -> dict[str, Any]:
    async with connect(database_path) as connection:
        async with transaction(connection):
            row = await (
                await connection.execute(
                    """
                    SELECT nc.checklist_instance_item_id, cig.audit_instance_id
                    FROM nonconformities nc
                    JOIN checklist_instance_items cii ON cii.id = nc.checklist_instance_item_id
                    JOIN checklist_instance_groups cig ON cig.id = cii.instance_group_id
                    WHERE nc.id = ?
                    """,
                    (issue_id,),
                )
            ).fetchone()
            if row is None:
                raise AuditWorkflowError("AUDIT_ISSUE_NOT_FOUND", "问题不存在")
            audit_id = int(row["audit_instance_id"])
            item_id = int(row["checklist_instance_item_id"])
            await _require_revision(connection, audit_id, expected_revision)
            count = int(
                (await (await connection.execute(
                    "SELECT COUNT(*) FROM nonconformities WHERE checklist_instance_item_id = ?",
                    (item_id,),
                )).fetchone())[0]
            )
            if count == 1 and not set_conclusion_to_compliant:
                raise AuditWorkflowError(
                    "CHECKLIST_LAST_ISSUE_REQUIRES_CONCLUSION_CHANGE",
                    "删除最后一条问题时必须确认将检查结论改为符合",
                )
            await connection.execute("DELETE FROM nonconformities WHERE id = ?", (issue_id,))
            if count == 1:
                await connection.execute(
                    "UPDATE checklist_instance_items SET conclusion = '符合' WHERE id = ?", (item_id,)
                )
            revision = await _bump_revision(connection, audit_id, expected_revision)
    return {"audit_id": audit_id, "item_id": item_id, "revision": revision}
