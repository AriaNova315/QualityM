from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.application.checklist_mapping_service import product_mapping_subjects
from app.application.import_preview_service import PreviewTokenError, _token_hash
from app.application.audit_snapshot_service import (
    create_audit_snapshot,
    load_template_snapshots,
)
from app.database import connect, transaction
from app.domain.audit_expansion import expand_audit_instances
from app.domain.activity_rules import classify_activity


@dataclass(frozen=True, slots=True)
class ImportCommitResult:
    project_id: int
    project_version_id: int
    version_no: int
    audit_instance_count: int
    diagnostics: list[dict[str, Any]]


async def commit_preview(
    database_path: Path,
    token: str,
    phase_overrides: dict[str, bool],
    checklist_mappings: dict[str, str],
    save_global_mappings: bool,
    config_item_number: str = "0.0",
) -> ImportCommitResult:
    now = datetime.now(UTC)
    async with connect(database_path) as connection:
        async with transaction(connection):
            row = await (
                await connection.execute(
                    "SELECT * FROM import_previews WHERE token_hash = ?", (_token_hash(token),)
                )
            ).fetchone()
            if row is None:
                raise PreviewTokenError("IMPORT_PREVIEW_TOKEN_INVALID", "导入预览令牌无效")
            if row["status"] != "pending":
                raise PreviewTokenError("IMPORT_PREVIEW_TOKEN_CONSUMED", "导入预览令牌已使用")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                raise PreviewTokenError("IMPORT_PREVIEW_TOKEN_EXPIRED", "导入预览令牌已过期")
            payload = json.loads(row["parsed_payload_json"])
            blockers = [d for d in payload["diagnostics"] if d["severity"] == "blocker"]
            if blockers:
                raise PreviewTokenError("IMPORT_PREVIEW_HAS_BLOCKERS", "导入预览存在阻塞问题")

            excluded_company = {
                item["wbs"] for item in payload["excluded_top_level"]
                if item["reason"] == "excluded_company_validation"
            }
            if any(phase_overrides.get(wbs) for wbs in excluded_company):
                raise PreviewTokenError(
                    "IMPORT_PHASE_OVERRIDE_FORBIDDEN", "公司质量验证不能调整为项目阶段"
                )
            allowed_wbs = {phase["wbs"] for phase in payload["phases"]}
            if set(phase_overrides) - (allowed_wbs | excluded_company):
                raise PreviewTokenError("IMPORT_PHASE_OVERRIDE_INVALID", "阶段调整包含未知 WBS")
            disabled = {wbs for wbs, enabled in phase_overrides.items() if not enabled}
            effective_payload = {
                **payload,
                "phases": [phase for phase in payload["phases"] if phase["wbs"] not in disabled],
                "tracking_nodes": [node for node in payload["tracking_nodes"] if node["phase_wbs"] not in disabled],
                "quality_activities": [activity for activity in payload["quality_activities"] if activity["phase_wbs"] not in disabled],
            }

            resolutions = payload.get("product_mapping_resolutions")
            if not isinstance(resolutions, list):
                raise PreviewTokenError(
                    "IMPORT_MAPPING_PREVIEW_INVALID", "导入预览缺少工作产品检查单关联结果，请重新生成预览"
                )
            auto_mappings: dict[str, str | None] = {}
            for item in resolutions:
                if not isinstance(item, dict) or not isinstance(item.get("subject"), str):
                    raise PreviewTokenError(
                        "IMPORT_MAPPING_PREVIEW_INVALID", "导入预览中的工作产品检查单关联结果无效"
                    )
                stable_key = item.get("stable_key")
                if stable_key is not None and not isinstance(stable_key, str):
                    raise PreviewTokenError(
                        "IMPORT_MAPPING_PREVIEW_INVALID", "导入预览中的检查单模板标识无效"
                    )
                auto_mappings[item["subject"]] = stable_key
            subjects = set(product_mapping_subjects(payload))
            if set(auto_mappings) != subjects or set(checklist_mappings) - subjects:
                raise PreviewTokenError(
                    "IMPORT_MAPPING_PREVIEW_INVALID", "工作产品检查单关联与当前导入预览不一致，请重新生成预览"
                )
            effective_mappings = {
                subject: checklist_mappings.get(subject, automatic)
                for subject, automatic in auto_mappings.items()
            }
            expansion = expand_audit_instances(
                effective_payload,
                config_item_number=config_item_number,
                frozen_product_mappings=effective_mappings,
            )
            expansion_blockers = [d for d in expansion.diagnostics if d.severity == "blocker"]
            if expansion_blockers:
                raise PreviewTokenError(
                    "IMPORT_PREVIEW_HAS_BLOCKERS",
                    json.dumps([d.to_dict() for d in expansion_blockers], ensure_ascii=False),
                )

            template_rows = await (
                await connection.execute(
                    "SELECT id, stable_key, audit_kind FROM checklist_templates WHERE enabled = 1"
                )
            ).fetchall()
            templates = {r["stable_key"]: (r["id"], r["audit_kind"]) for r in template_rows}
            for instance in expansion.instances:
                template = templates.get(instance["checklist_stable_key"])
                expected_kind = "product" if instance["audit_kind"] == "product" else "process"
                if template is None or template[1] != expected_kind:
                    raise PreviewTokenError(
                        "IMPORT_CHECKLIST_TEMPLATE_INVALID",
                        f"预览所选检查单模板不可用：{instance['checklist_stable_key']}，请重新生成预览",
                    )

            project = payload["project"]
            existing_project = await (
                await connection.execute(
                    "SELECT id FROM projects WHERE project_code = ?", (project["project_code"],)
                )
            ).fetchone()
            if existing_project is None:
                cursor = await connection.execute(
                    "INSERT INTO projects(project_code, project_name) VALUES (?, ?)",
                    (project["project_code"], project["project_name"]),
                )
                project_id = int(cursor.lastrowid)
            else:
                project_id = int(existing_project["id"])
                await connection.execute(
                    "UPDATE projects SET project_name = ? WHERE id = ?",
                    (project["project_name"], project_id),
                )
            version_row = await (
                await connection.execute(
                    "SELECT COALESCE(MAX(version_no), 0) + 1 FROM project_versions WHERE project_id = ?",
                    (project_id,),
                )
            ).fetchone()
            version_no = int(version_row[0])
            cursor = await connection.execute(
                """
                INSERT INTO project_versions(
                    project_id, version_no, source_filename, source_sha256, imported_at,
                    config_item_number, project_leader, qa_person, raw_metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id, version_no, row["source_filename"], row["source_sha256"],
                    now.isoformat(), config_item_number, project["project_leader"],
                    project["qa_person"], json.dumps(project, ensure_ascii=False, sort_keys=True),
                ),
            )
            project_version_id = int(cursor.lastrowid)
            await connection.execute(
                """
                INSERT INTO project_version_settings(
                    project_version_id, config_item_number, revision, updated_at
                ) VALUES (?, ?, 1, ?)
                """,
                (project_version_id, config_item_number, now.isoformat()),
            )

            phase_ids: dict[str, int] = {}
            for order, phase in enumerate(effective_payload["phases"], start=1):
                cursor = await connection.execute(
                    """
                    INSERT INTO phases(project_version_id, wbs, name, start_date, end_date, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (project_version_id, phase["wbs"], phase["name"], phase["start_date"], phase["end_date"], order),
                )
                phase_ids[phase["wbs"]] = int(cursor.lastrowid)

            for item in payload["phases"] + payload["excluded_top_level"]:
                selected = phase_overrides.get(item["wbs"], item["wbs"] in phase_ids)
                await connection.execute(
                    """
                    INSERT INTO phase_detection_overrides(
                        project_version_id, source_wbs, detected_as_phase,
                        user_selected_as_phase, source_reason
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project_version_id, item["wbs"], int(item["wbs"] in allowed_wbs),
                        int(bool(selected)), item.get("reason", "detected_top_level_with_children"),
                    ),
                )

            node_ids: dict[str, int] = {}
            for order, node in enumerate(effective_payload["tracking_nodes"], start=1):
                cursor = await connection.execute(
                    """
                    INSERT INTO tracking_nodes(
                        phase_id, source_wbs, node_type, node_date, sort_order
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (phase_ids[node["phase_wbs"]], node["wbs"], node["node_type"], node["node_date"], order),
                )
                node_ids[node["wbs"]] = int(cursor.lastrowid)

            config_ids: dict[tuple[str, str | None], int] = {}
            for item in payload["config_items"]:
                cursor = await connection.execute(
                    """
                    INSERT INTO config_items(
                        project_version_id, source_seq, name, identifier, version,
                        item_type, phase_wbs, review_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_version_id, item["source_order"], item["name"], item["identifier"],
                        item["version"], item["item_type"], item["phase_wbs"], item["review_date"],
                    ),
                )
                config_ids[(item["name"], item["version"])] = int(cursor.lastrowid)

            activity_ids: dict[str, int] = {}
            for order, activity in enumerate(effective_payload["quality_activities"], start=1):
                classification = classify_activity(activity["name"])
                if classification.kind == "report":
                    continue
                cursor = await connection.execute(
                    """
                    INSERT INTO quality_activities(
                        phase_id, source_wbs, name, audit_type, start_date, end_date,
                        auditor, linked_items_json, sort_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        phase_ids[activity["phase_wbs"]], activity["wbs"], activity["name"],
                        classification.kind, activity["start_date"], activity["end_date"],
                        activity["resources"][0] if activity["resources"] else project["qa_person"],
                        json.dumps(activity["linked_items"], ensure_ascii=False), order,
                    ),
                )
                activity_ids[activity["wbs"]] = int(cursor.lastrowid)

            template_snapshots = await load_template_snapshots(
                connection,
                {int(templates[item["checklist_stable_key"]][0]) for item in expansion.instances},
            )
            for instance in expansion.instances:
                template_id = int(templates[instance["checklist_stable_key"]][0])
                template_snapshot = template_snapshots.get(template_id)
                if template_snapshot is None:
                    raise PreviewTokenError(
                        "IMPORT_CHECKLIST_TEMPLATE_INVALID",
                        f"检查单模板没有可用快照内容：{instance['checklist_stable_key']}",
                    )
                cursor = await connection.execute(
                    """
                    INSERT INTO audit_instances(
                        project_version_id, phase_id, activity_id, config_item_id,
                        audit_kind, report_scope, process_code, checklist_template_id,
                        sequence_no, audit_date, tracking_node_id, auditor,
                        confirmation_state, revision, snapshot_schema_version,
                        user_edited, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, 2, 0, ?, ?)
                    """,
                    (
                        project_version_id, phase_ids[instance["phase_wbs"]],
                        activity_ids[instance["source_wbs"]],
                        config_ids.get((instance["config_item_name"], instance["config_item_version"])),
                        instance["audit_kind"], instance["report_scope"], instance["process_code"],
                        template_id, instance["sequence_no"],
                        instance["audit_date"], node_ids.get(instance["tracking_node_wbs"]),
                        instance["auditor"], now.isoformat(), now.isoformat(),
                    ),
                )
                await create_audit_snapshot(
                    connection,
                    audit_instance_id=int(cursor.lastrowid),
                    template=template_snapshot,
                )

            if save_global_mappings:
                for pattern, stable_key in checklist_mappings.items():
                    template = templates.get(stable_key)
                    if template is None or template[1] != "product":
                        raise PreviewTokenError(
                            "IMPORT_CHECKLIST_TEMPLATE_INVALID", f"产品检查单模板不可用：{stable_key}"
                        )
                    await connection.execute(
                        """
                        INSERT INTO mapping_rules(
                            target_kind, match_kind, pattern, checklist_template_id,
                            priority, source, enabled
                        ) VALUES ('product', 'exact', ?, ?, 300, 'user', 1)
                        ON CONFLICT(target_kind, match_kind, pattern, source)
                        DO UPDATE SET checklist_template_id = excluded.checklist_template_id,
                                      priority = excluded.priority, enabled = 1
                        """,
                        (pattern, template[0]),
                    )

            await connection.execute(
                "UPDATE import_previews SET overrides_json = ?, status = 'consumed' WHERE id = ?",
                (json.dumps(phase_overrides, ensure_ascii=False, sort_keys=True), row["id"]),
            )

    return ImportCommitResult(
        project_id, project_version_id, version_no, len(expansion.instances),
        [d.to_dict() for d in expansion.diagnostics],
    )
