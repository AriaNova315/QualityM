from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from app.diagnostics import Diagnostic
from app.domain.activity_rules import classify_activity
from app.domain.calendar import CalendarCoverageError, planned_close_date, require_calendar_coverage
from app.domain.mapping import (
    MappingCandidate,
    MappingError,
    builtin_product_candidates,
    choose_mapping,
    process_template,
)
from app.domain.numbering import assign_sequences, record_number


@dataclass(frozen=True, slots=True)
class ExpansionResult:
    instances: list[dict[str, Any]]
    diagnostics: list[Diagnostic]


def _as_date(value: str) -> date:
    return date.fromisoformat(value)


def _nearest_tracking_node(
    phase_wbs: str,
    audit_date: str,
    tracking_nodes: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = [
        node for node in tracking_nodes
        if node["phase_wbs"] == phase_wbs and node["node_date"] >= audit_date
    ]
    return min(candidates, key=lambda node: (node["node_date"], node["source_order"]), default=None)


def expand_audit_instances(
    payload: dict[str, Any],
    project_mapping_overrides: dict[str, str] | None = None,
    user_exact_mappings: dict[str, list[str]] | None = None,
    config_item_number: str = "0.0",
    frozen_product_mappings: dict[str, str | None] | None = None,
) -> ExpansionResult:
    project_mapping_overrides = project_mapping_overrides or {}
    user_exact_mappings = user_exact_mappings or {}
    diagnostics: list[Diagnostic] = []
    raw_instances: list[dict[str, Any]] = []
    project = payload["project"]
    phases = {phase["wbs"]: phase for phase in payload["phases"]}
    config_by_name: dict[str, list[dict[str, Any]]] = {}
    for item in payload["config_items"]:
        config_by_name.setdefault(item["name"], []).append(item)

    for activity in payload["quality_activities"]:
        classification = classify_activity(activity["name"])
        if classification.kind == "report":
            continue
        if classification.kind == "unknown":
            diagnostics.append(
                Diagnostic(
                    "IMPORT_UNKNOWN_QUALITY_ACTIVITY", "blocker", "activity", activity["source"],
                    f"无法分类质量活动：{activity['name']}", "请修正活动名称或补充分类规则。",
                )
            )
            continue
        phase = phases.get(activity["phase_wbs"])
        if phase is None:
            continue
        audit_date = activity["end_date"]
        if audit_date is None:
            diagnostics.append(
                Diagnostic(
                    "IMPORT_MISSING_AUDIT_DATE", "blocker", "activity", activity["source"],
                    "质量活动缺少完成日期", "请补充质量活动计划完成日期。",
                )
            )
            continue
        if audit_date < phase["start_date"] or audit_date > phase["end_date"]:
            diagnostics.append(
                Diagnostic(
                    "IMPORT_AUDIT_DATE_OUTSIDE_PHASE", "blocker", "activity", activity["source"],
                    f"审核日期 {audit_date} 不在阶段日期范围内", "请修正审核活动或阶段日期。",
                )
            )
            continue

        node = _nearest_tracking_node(activity["phase_wbs"], audit_date, payload["tracking_nodes"])
        try:
            require_calendar_coverage(_as_date(audit_date))
        except CalendarCoverageError as exc:
            diagnostics.append(
                Diagnostic(
                    "IMPORT_CALENDAR_YEAR_UNSUPPORTED", "blocker", "activity", activity["source"],
                    str(exc), "请更新 QualityM 的法定工作日日历后重新导入。",
                )
            )
            continue

        targets: Iterable[dict[str, Any] | None]
        if classification.kind == "product":
            if not activity["linked_items"]:
                diagnostics.append(
                    Diagnostic(
                        "IMPORT_PRODUCT_AUDIT_WITHOUT_CONFIG_ITEM", "blocker", "activity",
                        activity["source"], "产品审核没有关联配置项", "请在关联配置项列选择工作产品。",
                    )
                )
                continue
            resolved: list[dict[str, Any]] = []
            for linked_name in activity["linked_items"]:
                matches = config_by_name.get(linked_name, [])
                if len(matches) != 1:
                    diagnostics.append(
                        Diagnostic(
                            "IMPORT_CONFIG_ITEM_REFERENCE_NOT_UNIQUE", "blocker", "activity",
                            activity["source"], f"关联配置项无法唯一匹配：{linked_name}",
                            "请修正关联配置项或配置项清单中的重复记录。",
                        )
                    )
                else:
                    resolved.append(matches[0])
            targets = resolved
        else:
            targets = [None]

        for config_item in targets:
            subject = config_item["name"] if config_item is not None else activity["name"]
            try:
                if classification.kind == "product":
                    if frozen_product_mappings is not None:
                        template_key = frozen_product_mappings.get(subject)
                        if not template_key:
                            raise MappingError("IMPORT_UNKNOWN_CHECKLIST_MAPPING", subject)
                    else:
                        candidates = builtin_product_candidates(subject, project["project_name"])
                        candidates.extend(
                            MappingCandidate(value, 300, "user")
                            for value in user_exact_mappings.get(subject, [])
                        )
                        if subject in project_mapping_overrides:
                            candidates.append(
                                MappingCandidate(project_mapping_overrides[subject], 400, "project")
                            )
                        template_key = choose_mapping(candidates, subject)
                else:
                    template_key = process_template(classification.process_code or "")
            except MappingError as exc:
                diagnostics.append(
                    Diagnostic(
                        exc.code, "blocker", "activity", activity["source"],
                        f"无法确定检查单：{subject}", "请选择检查单，并可保存为全局精确映射。",
                    )
                )
                continue

            close_result = planned_close_date(
                "product" if classification.kind == "product" else "process",
                _as_date(audit_date),
                _as_date(node["node_date"]) if node else None,
                _as_date(phase["end_date"]),
            )
            if close_result.compressed:
                diagnostics.append(
                    Diagnostic(
                        "AUDIT_CLOSE_DATE_COMPRESSED", "warning", "activity", activity["source"],
                        f"计划关闭日期压缩为 {close_result.planned_close_date.isoformat()}",
                        "关闭边界取最近后续跟踪节点或阶段结束日期。",
                    )
                )
            raw_instances.append(
                {
                    "phase_wbs": phase["wbs"],
                    "phase_name": phase["name"],
                    "phase_order": phase["source_order"],
                    "source_wbs": activity["wbs"],
                    "activity_name": activity["name"],
                    "activity_source": activity["source"],
                    "audit_kind": classification.kind,
                    "report_scope": classification.report_scope,
                    "process_code": classification.process_code,
                    "checklist_stable_key": template_key,
                    "config_item_name": config_item["name"] if config_item else None,
                    "config_item_identifier": config_item["identifier"] if config_item else None,
                    "config_item_version": config_item["version"] if config_item else None,
                    "config_source_order": config_item["source_order"] if config_item else None,
                    "audit_date": audit_date,
                    "tracking_node_wbs": node["wbs"] if node else None,
                    "planned_close_date": close_result.planned_close_date.isoformat(),
                    "auditor": activity["resources"][0] if activity["resources"] else project["qa_person"],
                }
            )

    instances = assign_sequences(raw_instances)
    instances = [
        {
            **instance,
            "checklist_number": record_number(
                project["project_code"], config_item_number, "检查单", instance["sequence_no"]
            ),
            "audit_report_number": record_number(
                project["project_code"], config_item_number, "审核报告", instance["sequence_no"]
            ),
        }
        for instance in instances
    ]
    return ExpansionResult(instances, diagnostics)
