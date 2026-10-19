from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def wbs_sort_key(wbs: str) -> tuple[tuple[int, Any], ...]:
    result: list[tuple[int, Any]] = []
    for part in wbs.split("."):
        try:
            result.append((0, Decimal(part)))
        except InvalidOperation:
            result.append((1, part))
    return tuple(result)


def audit_sort_key(instance: dict) -> tuple:
    return (
        instance["phase_order"],
        instance["audit_date"],
        wbs_sort_key(instance["source_wbs"]),
        instance.get("config_source_order") or 0,
    )


def assign_sequences(instances: list[dict]) -> list[dict]:
    ordered = sorted(instances, key=audit_sort_key)
    return [{**instance, "sequence_no": index} for index, instance in enumerate(ordered, start=1)]


def record_number(project_code: str, config_item_number: str, record_type: str, sequence_no: int) -> str:
    return f"ZDPX-{project_code}/{config_item_number}-QA-{record_type}-{sequence_no:03d}"

