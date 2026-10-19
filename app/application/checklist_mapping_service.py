from __future__ import annotations

from typing import Any

from app.domain.mapping import (
    MappingCandidate,
    MappingError,
    builtin_product_candidates,
    choose_mapping_candidate,
)


async def load_user_exact_mappings(connection) -> dict[str, list[str]]:
    rows = await (
        await connection.execute(
            """
            SELECT mr.pattern, ct.stable_key
            FROM mapping_rules mr
            JOIN checklist_templates ct ON ct.id = mr.checklist_template_id
            WHERE mr.target_kind = 'product' AND mr.match_kind = 'exact'
              AND mr.source = 'user' AND mr.enabled = 1 AND ct.enabled = 1
            ORDER BY mr.priority DESC, mr.id
            """
        )
    ).fetchall()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row["pattern"], []).append(row["stable_key"])
    return result


def product_mapping_subjects(payload: dict[str, Any]) -> tuple[str, ...]:
    subjects: list[str] = []
    seen: set[str] = set()
    for activity in payload.get("quality_activities", []):
        for subject in activity.get("linked_items", []):
            if subject not in seen:
                seen.add(subject)
                subjects.append(subject)
    return tuple(subjects)


async def build_product_mapping_resolutions(
    connection, payload: dict[str, Any]
) -> list[dict[str, str | None]]:
    """Create the immutable automatic suggestions shown to the user in import preview."""
    rows = await (
        await connection.execute(
            """
            SELECT stable_key, name
            FROM checklist_templates
            WHERE audit_kind = 'product' AND enabled = 1
            ORDER BY display_order, id
            """
        )
    ).fetchall()
    templates = {row["stable_key"]: row["name"] for row in rows}
    user_mappings = await load_user_exact_mappings(connection)
    project_name = str(payload["project"]["project_name"])
    resolutions: list[dict[str, str | None]] = []
    for subject in product_mapping_subjects(payload):
        candidates = builtin_product_candidates(subject, project_name)
        candidates.extend(
            MappingCandidate(value, 300, "user")
            for value in user_mappings.get(subject, [])
        )
        try:
            selected = choose_mapping_candidate(candidates, subject)
            template_name = templates.get(selected.stable_key)
            if template_name is None:
                raise MappingError("IMPORT_UNKNOWN_CHECKLIST_MAPPING", subject)
            resolutions.append(
                {
                    "subject": subject,
                    "stable_key": selected.stable_key,
                    "template_name": template_name,
                    "source": selected.source,
                    "status": "resolved",
                }
            )
        except MappingError as exc:
            resolutions.append(
                {
                    "subject": subject,
                    "stable_key": None,
                    "template_name": None,
                    "source": None,
                    "status": "manual_required",
                    "reason": exc.code,
                }
            )
    return resolutions
