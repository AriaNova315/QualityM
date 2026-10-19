from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import aiosqlite



@dataclass(frozen=True, slots=True)
class TemplateSnapshot:
    groups: tuple[dict[str, Any], ...]


async def load_template_snapshots(
    connection: aiosqlite.Connection,
    template_ids: set[int],
) -> dict[int, TemplateSnapshot]:
    if not template_ids:
        return {}
    placeholders = ",".join("?" for _ in template_ids)
    rows = await (
        await connection.execute(
            f"""
            SELECT
                ct.id AS template_id,
                cg.id AS group_id, cg.stable_key AS group_key, cg.name AS group_name,
                cg.version AS group_version, cg.display_order AS group_order,
                ci.id AS item_id, ci.stable_key AS item_key, ci.display_no,
                ci.content, ci.starred, ci.display_order AS item_order,
                it.id AS issue_id, it.stable_key AS issue_key, it.version AS issue_version,
                it.title, it.description, it.corrective_suggestion, it.default_severity,
                it.placeholders_json, it.is_default, it.display_order AS issue_order
            FROM checklist_templates ct
            JOIN checklist_groups cg ON cg.checklist_template_id = ct.id AND cg.enabled = 1
            JOIN checklist_items ci ON ci.checklist_group_id = cg.id AND ci.enabled = 1
            JOIN issue_templates it ON it.checklist_item_id = ci.id AND it.enabled = 1
            WHERE ct.id IN ({placeholders}) AND ct.enabled = 1
            ORDER BY ct.id, cg.display_order, cg.id, ci.display_order, ci.id,
                     it.display_order, it.id
            """,
            tuple(sorted(template_ids)),
        )
    ).fetchall()
    grouped: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    items: dict[tuple[int, int], dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        template_id = int(row["template_id"])
        group_id = int(row["group_id"])
        item_id = int(row["item_id"])
        group = grouped[template_id].setdefault(
            group_id,
            {
                "source_group_id": group_id,
                "stable_key": row["group_key"],
                "name": row["group_name"],
                "version": row["group_version"],
                "display_order": int(row["group_order"]),
                "items": [],
            },
        )
        item = items[(template_id, group_id)].setdefault(
            item_id,
            {
                "source_item_id": item_id,
                "stable_key": row["item_key"],
                "display_no": row["display_no"],
                "content": row["content"],
                "starred": bool(row["starred"]),
                "display_order": int(row["item_order"]),
                "issues": [],
            },
        )
        item["issues"].append(
            {
                "source_issue_template_id": int(row["issue_id"]),
                "stable_key": row["issue_key"],
                "version": row["issue_version"],
                "title": row["title"],
                "description": row["description"],
                "corrective_suggestion": row["corrective_suggestion"],
                "default_severity": row["default_severity"],
                "placeholders": json.loads(row["placeholders_json"]),
                "is_default": bool(row["is_default"]),
                "display_order": int(row["issue_order"]),
            }
        )
        if item not in group["items"]:
            group["items"].append(item)
    return {
        template_id: TemplateSnapshot(tuple(groups.values()))
        for template_id, groups in grouped.items()
    }


async def create_audit_snapshot(
    connection: aiosqlite.Connection,
    *,
    audit_instance_id: int,
    template: TemplateSnapshot,
) -> None:
    for group in template.groups:
        cursor = await connection.execute(
            """
            INSERT INTO checklist_instance_groups(
                audit_instance_id, source_group_id, stable_key, name_snapshot,
                version_snapshot, display_order
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                audit_instance_id, group["source_group_id"], group["stable_key"],
                group["name"], group["version"], group["display_order"],
            ),
        )
        instance_group_id = int(cursor.lastrowid)
        for item in group["items"]:
            cursor = await connection.execute(
                """
                INSERT INTO checklist_instance_items(
                    instance_group_id, source_item_id, stable_key, display_no_snapshot,
                    content_snapshot, starred_snapshot, conclusion, display_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_group_id, item["source_item_id"], item["stable_key"],
                    item["display_no"], item["content"], int(item["starred"]),
                    "符合", item["display_order"],
                ),
            )
            instance_item_id = int(cursor.lastrowid)
            for issue in item["issues"]:
                await connection.execute(
                    """
                    INSERT INTO checklist_instance_issue_templates(
                        checklist_instance_item_id, source_issue_template_id, stable_key,
                        template_version_snapshot, title_snapshot, description_snapshot,
                        corrective_suggestion_snapshot, default_severity_snapshot,
                        placeholders_snapshot_json, is_default_snapshot, display_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        instance_item_id, issue["source_issue_template_id"], issue["stable_key"],
                        issue["version"], issue["title"], issue["description"],
                        issue["corrective_suggestion"], issue["default_severity"],
                        json.dumps(issue["placeholders"], ensure_ascii=False),
                        int(issue["is_default"]), issue["display_order"],
                    ),
                )
