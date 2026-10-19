from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from app.database import connect


class ExportSnapshotError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ExportIssueSnapshot:
    id: int
    title: str
    description: str
    severity: str
    responsible_person: str | None
    submitted_date: str | None
    planned_close_date: str | None
    corrective_action: str | None
    actual_close_date: str | None
    verification_date: str | None
    verification_conclusion: str | None
    confirmed: bool
    suggested_closed: bool = False


@dataclass(frozen=True, slots=True)
class ExportChecklistItemSnapshot:
    id: int
    stable_key: str
    display_no: str
    content: str
    starred: bool
    conclusion: str | None
    display_order: int
    source_row: int | None
    issues: tuple[ExportIssueSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ExportChecklistGroupSnapshot:
    id: int
    stable_key: str
    name: str
    display_order: int
    source_row: int | None
    items: tuple[ExportChecklistItemSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ExportAuditSnapshot:
    id: int
    sequence_no: int
    audit_kind: str
    report_scope: str
    process_code: str | None
    audit_date: str
    auditor: str | None
    confirmation_state: str
    revision: int
    activity_name: str
    config_item_name: str | None
    config_item_identifier: str | None
    config_item_version: str | None
    tracking_node_id: int | None
    tracking_node_type: str | None
    tracking_node_date: str | None
    checklist_name: str
    checklist_source_workbook: str | None
    checklist_source_sheet: str
    groups: tuple[ExportChecklistGroupSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ExportTrackingNodeSnapshot:
    id: int
    source_wbs: str
    node_type: str
    node_date: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class ExportPhaseSnapshot:
    id: int
    wbs: str
    name: str
    start_date: str
    end_date: str
    sort_order: int
    tracking_nodes: tuple[ExportTrackingNodeSnapshot, ...]
    audits: tuple[ExportAuditSnapshot, ...]


@dataclass(frozen=True, slots=True)
class ExportSnapshot:
    project_version_id: int
    project_code: str
    project_name: str
    version_no: int
    project_leader: str
    qa_person: str
    config_item_number: str
    settings_revision: int
    source_revision: str
    content_sha256: str
    trend_conclusion_override: str | None
    phases: tuple[ExportPhaseSnapshot, ...]


def _snapshot_hash(snapshot: ExportSnapshot) -> str:
    payload = asdict(replace(snapshot, content_sha256=""))
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def load_export_snapshot(
    database_path: Path,
    project_version_id: int,
) -> ExportSnapshot:
    """Freeze all export facts using one SQLite connection and one read transaction."""

    async with connect(database_path) as connection:
        # WAL lets an export hold a stable read view without blocking audit edits.
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("BEGIN")
        try:
            project = await (
                await connection.execute(
                    """
                    SELECT pv.id, pv.version_no, pv.project_leader, pv.qa_person,
                           p.project_code, p.project_name,
                           COALESCE(pvs.config_item_number, pv.config_item_number) AS config_item_number,
                           COALESCE(pvs.revision, 1) AS settings_revision,
                           pvs.trend_conclusion_override
                    FROM project_versions pv
                    JOIN projects p ON p.id = pv.project_id
                    LEFT JOIN project_version_settings pvs
                           ON pvs.project_version_id = pv.id
                    WHERE pv.id = ?
                    """,
                    (project_version_id,),
                )
            ).fetchone()
            if project is None:
                raise ExportSnapshotError(
                    "PROJECT_VERSION_NOT_FOUND", "项目数据版本不存在"
                )

            phase_rows = await (
                await connection.execute(
                    """
                    SELECT id, wbs, name, start_date, end_date, sort_order
                    FROM phases WHERE project_version_id = ?
                    ORDER BY sort_order, id
                    """,
                    (project_version_id,),
                )
            ).fetchall()
            node_rows = await (
                await connection.execute(
                    """
                    SELECT tn.* FROM tracking_nodes tn
                    JOIN phases ph ON ph.id = tn.phase_id
                    WHERE ph.project_version_id = ?
                    ORDER BY ph.sort_order, tn.sort_order, tn.id
                    """,
                    (project_version_id,),
                )
            ).fetchall()
            audit_rows = await (
                await connection.execute(
                    """
                    SELECT ai.*, qa.name AS activity_name,
                           ci.name AS config_item_name,
                           ci.identifier AS config_item_identifier,
                           ci.version AS config_item_version,
                           tn.node_type AS tracking_node_type,
                           tn.node_date AS tracking_node_date,
                           ct.name AS checklist_name,
                           ct.source_workbook AS checklist_source_workbook,
                           ct.source_sheet AS checklist_source_sheet
                    FROM audit_instances ai
                    JOIN quality_activities qa ON qa.id = ai.activity_id
                    JOIN checklist_templates ct ON ct.id = ai.checklist_template_id
                    LEFT JOIN config_items ci ON ci.id = ai.config_item_id
                    LEFT JOIN tracking_nodes tn ON tn.id = ai.tracking_node_id
                    WHERE ai.project_version_id = ?
                    ORDER BY ai.sequence_no, ai.id
                    """,
                    (project_version_id,),
                )
            ).fetchall()
            group_rows = await (
                await connection.execute(
                    """
                    SELECT cig.*, cg.source_row FROM checklist_instance_groups cig
                    JOIN audit_instances ai ON ai.id = cig.audit_instance_id
                    LEFT JOIN checklist_groups cg ON cg.id = cig.source_group_id
                    WHERE ai.project_version_id = ?
                    ORDER BY ai.sequence_no, cig.display_order, cig.id
                    """,
                    (project_version_id,),
                )
            ).fetchall()
            item_rows = await (
                await connection.execute(
                    """
                    SELECT cii.*, ci.source_row FROM checklist_instance_items cii
                    JOIN checklist_instance_groups cig ON cig.id = cii.instance_group_id
                    JOIN audit_instances ai ON ai.id = cig.audit_instance_id
                    LEFT JOIN checklist_items ci ON ci.id = cii.source_item_id
                    WHERE ai.project_version_id = ?
                    ORDER BY ai.sequence_no, cig.display_order, cii.display_order, cii.id
                    """,
                    (project_version_id,),
                )
            ).fetchall()
            issue_rows = await (
                await connection.execute(
                    """
                    SELECT nc.* FROM nonconformities nc
                    JOIN checklist_instance_items cii ON cii.id = nc.checklist_instance_item_id
                    JOIN checklist_instance_groups cig ON cig.id = cii.instance_group_id
                    JOIN audit_instances ai ON ai.id = cig.audit_instance_id
                    WHERE ai.project_version_id = ?
                    ORDER BY ai.sequence_no, cig.display_order, cii.display_order, nc.id
                    """,
                    (project_version_id,),
                )
            ).fetchall()

            issues_by_item: dict[int, list[ExportIssueSnapshot]] = {}
            for row in issue_rows:
                issues_by_item.setdefault(int(row["checklist_instance_item_id"]), []).append(
                    ExportIssueSnapshot(
                        id=int(row["id"]),
                        title=row["title"],
                        description=row["description"],
                        severity=row["severity"],
                        responsible_person=row["responsible_person"],
                        submitted_date=row["submitted_date"],
                        planned_close_date=row["planned_close_date"],
                        corrective_action=row["corrective_action"],
                        actual_close_date=row["actual_close_date"],
                        verification_date=row["verification_date"],
                        verification_conclusion=row["verification_conclusion"],
                        suggested_closed=bool(row["suggested_closed"]),
                        confirmed=bool(row["confirmed"]),
                    )
                )

            items_by_group: dict[int, list[ExportChecklistItemSnapshot]] = {}
            for row in item_rows:
                item_id = int(row["id"])
                items_by_group.setdefault(int(row["instance_group_id"]), []).append(
                    ExportChecklistItemSnapshot(
                        id=item_id,
                        stable_key=row["stable_key"],
                        display_no=row["display_no_snapshot"],
                        content=row["content_snapshot"],
                        starred=bool(row["starred_snapshot"]),
                        conclusion=row["conclusion"],
                        display_order=int(row["display_order"]),
                        source_row=(int(row["source_row"]) if row["source_row"] is not None else None),
                        issues=tuple(issues_by_item.get(item_id, ())),
                    )
                )

            groups_by_audit: dict[int, list[ExportChecklistGroupSnapshot]] = {}
            for row in group_rows:
                group_id = int(row["id"])
                groups_by_audit.setdefault(int(row["audit_instance_id"]), []).append(
                    ExportChecklistGroupSnapshot(
                        id=group_id,
                        stable_key=row["stable_key"],
                        name=row["name_snapshot"],
                        display_order=int(row["display_order"]),
                        source_row=(int(row["source_row"]) if row["source_row"] is not None else None),
                        items=tuple(items_by_group.get(group_id, ())),
                    )
                )

            audits_by_phase: dict[int, list[ExportAuditSnapshot]] = {}
            revisions = []
            for row in audit_rows:
                audit_id = int(row["id"])
                revision = int(row["revision"])
                revisions.append(f"{audit_id}:{revision}")
                audits_by_phase.setdefault(int(row["phase_id"]), []).append(
                    ExportAuditSnapshot(
                        id=audit_id,
                        sequence_no=int(row["sequence_no"]),
                        audit_kind=row["audit_kind"],
                        report_scope=row["report_scope"],
                        process_code=row["process_code"],
                        audit_date=row["audit_date"],
                        auditor=row["auditor"],
                        confirmation_state=row["confirmation_state"],
                        revision=revision,
                        activity_name=row["activity_name"],
                        config_item_name=row["config_item_name"],
                        config_item_identifier=row["config_item_identifier"],
                        config_item_version=row["config_item_version"],
                        tracking_node_id=(
                            int(row["tracking_node_id"])
                            if row["tracking_node_id"] is not None
                            else None
                        ),
                        tracking_node_type=row["tracking_node_type"],
                        tracking_node_date=row["tracking_node_date"],
                        checklist_name=row["checklist_name"],
                        checklist_source_workbook=row["checklist_source_workbook"],
                        checklist_source_sheet=row["checklist_source_sheet"],
                        groups=tuple(groups_by_audit.get(audit_id, ())),
                    )
                )

            nodes_by_phase: dict[int, list[ExportTrackingNodeSnapshot]] = {}
            for row in node_rows:
                nodes_by_phase.setdefault(int(row["phase_id"]), []).append(
                    ExportTrackingNodeSnapshot(
                        id=int(row["id"]),
                        source_wbs=row["source_wbs"],
                        node_type=row["node_type"],
                        node_date=row["node_date"],
                        sort_order=int(row["sort_order"]),
                    )
                )

            phases = tuple(
                ExportPhaseSnapshot(
                    id=int(row["id"]),
                    wbs=row["wbs"],
                    name=row["name"],
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    sort_order=int(row["sort_order"]),
                    tracking_nodes=tuple(nodes_by_phase.get(int(row["id"]), ())),
                    audits=tuple(audits_by_phase.get(int(row["id"]), ())),
                )
                for row in phase_rows
            )
            settings_revision = int(project["settings_revision"])
            source_revision = f"settings:{settings_revision}|audits:{','.join(revisions)}"
            snapshot = ExportSnapshot(
                project_version_id=int(project["id"]),
                project_code=project["project_code"],
                project_name=project["project_name"],
                version_no=int(project["version_no"]),
                project_leader=project["project_leader"],
                qa_person=project["qa_person"],
                config_item_number=project["config_item_number"],
                settings_revision=settings_revision,
                source_revision=source_revision,
                content_sha256="",
                trend_conclusion_override=project["trend_conclusion_override"],
                phases=phases,
            )
            snapshot = replace(snapshot, content_sha256=_snapshot_hash(snapshot))
            await connection.commit()
            return snapshot
        except BaseException:
            await connection.rollback()
            raise
