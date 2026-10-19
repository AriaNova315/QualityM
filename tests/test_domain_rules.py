from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.domain.activity_rules import classify_activity
from app.domain.audit_expansion import expand_audit_instances
from app.domain.calendar import (
    CalendarCoverageError,
    is_workday,
    next_workday,
    planned_close_date,
)
from app.domain.mapping import MappingCandidate, MappingError, choose_mapping, choose_mapping_candidate
from app.domain.numbering import record_number
from app.importers.project_workbook import parse_project_workbook
from scripts.generate_audit_instances import build_audit_instance_payload, canonical_json


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "项目相关信息表.xlsx"
GOLDEN_INSTANCES = ROOT / "tests" / "fixtures" / "audit-instances.golden.json"


@pytest.mark.parametrize(
    "name, kind, scope, code",
    [
        ("产品审核-软件开发计划", "product", "phase", None),
        ("审核项目策划过程", "process", "phase", "PP"),
        ("审核配置管理-阶段", "process", "phase", "CM"),
        ("审核质量保证-公司", "company", "company", "QA"),
        ("审核质量保证过程", "company", "company", "QA"),
        ("拟制质量审核报告-阶段", "report", None, None),
    ],
)
def test_activity_classification(name: str, kind: str, scope: str | None, code: str | None) -> None:
    result = classify_activity(name)
    assert (result.kind, result.report_scope, result.process_code) == (kind, scope, code)


def test_mapping_priority_and_ambiguity() -> None:
    assert choose_mapping(
        [
            MappingCandidate("builtin", 100, "builtin_alias"),
            MappingCandidate("user", 300, "user"),
            MappingCandidate("project", 400, "project"),
        ],
        "产品",
    ) == "project"
    with pytest.raises(MappingError) as ambiguous:
        choose_mapping(
            [MappingCandidate("a", 300, "user"), MappingCandidate("b", 300, "user")],
            "产品",
        )
    assert ambiguous.value.code == "IMPORT_AMBIGUOUS_CHECKLIST_MAPPING"


def test_mapping_resolution_preserves_the_winning_source_for_preview() -> None:
    selected = choose_mapping_candidate(
        [
            MappingCandidate("builtin", 200, "builtin_exact"),
            MappingCandidate("user", 300, "user"),
        ],
        "产品",
    )
    assert (selected.stable_key, selected.source) == ("user", "user")


def test_legal_workday_rules_include_holidays_and_makeup_weekends() -> None:
    assert not is_workday(date(2026, 2, 17))
    assert is_workday(date(2026, 2, 28))
    assert next_workday(date(2026, 2, 13)) == date(2026, 2, 15)
    with pytest.raises(CalendarCoverageError):
        is_workday(date(2027, 1, 4))


def test_close_date_uses_kind_and_hard_boundaries() -> None:
    product = planned_close_date("product", date(2026, 3, 6), None, date(2026, 3, 20))
    process = planned_close_date("process", date(2026, 3, 6), None, date(2026, 3, 20))
    compressed = planned_close_date(
        "product", date(2026, 3, 11), date(2026, 3, 11), date(2026, 3, 11)
    )
    assert product.planned_close_date == date(2026, 3, 9)
    assert process.planned_close_date == date(2026, 3, 6)
    assert compressed.planned_close_date == date(2026, 3, 11)
    assert compressed.compressed is True


def test_current_sample_expands_to_locked_instance_baseline() -> None:
    payload = parse_project_workbook(SOURCE)
    result = expand_audit_instances(payload)

    assert len(result.instances) == 52
    assert sum(instance["audit_kind"] == "product" for instance in result.instances) == 14
    assert sum(instance["audit_kind"] == "process" for instance in result.instances) == 37
    assert sum(instance["report_scope"] == "company" for instance in result.instances) == 1
    assert [instance["sequence_no"] for instance in result.instances] == list(range(1, 53))
    assert not any("拟制质量" in instance["activity_name"] for instance in result.instances)
    assert all(instance["phase_wbs"] != "5" for instance in result.instances)
    assert [d.code for d in result.diagnostics] == ["AUDIT_CLOSE_DATE_COMPRESSED"]

    interface = next(
        instance for instance in result.instances
        if instance["config_item_name"] and instance["config_item_name"].endswith("接口设计说明")
    )
    unit_plan = next(
        instance for instance in result.instances
        if instance["config_item_name"] and instance["config_item_name"].endswith("单元测试计划")
    )
    company = next(instance for instance in result.instances if instance["report_scope"] == "company")
    assert interface["checklist_stable_key"] == "product.software-design"
    assert unit_plan["checklist_stable_key"] == "product.software-test-plan"
    assert company["source_wbs"] == "4.6.10"
    assert company["phase_wbs"] == "4"
    assert company["process_code"] == "QA"


def test_expansion_is_stable_and_numbers_match_rule() -> None:
    payload = parse_project_workbook(SOURCE)
    first = expand_audit_instances(payload).instances
    second = expand_audit_instances(payload).instances
    assert first == second
    assert first[0]["checklist_number"] == "ZDPX-03046/0.0-QA-检查单-001"
    assert first[0]["audit_report_number"] == "ZDPX-03046/0.0-QA-审核报告-001"
    assert record_number("03046", "1.2", "质量报告", 3) == "ZDPX-03046/1.2-QA-质量报告-003"


def test_tracking_node_and_phase_fallback_boundaries_are_deterministic() -> None:
    instances = expand_audit_instances(parse_project_workbook(SOURCE)).instances
    first = instances[0]
    compressed = next(
        instance
        for instance in instances
        if instance["activity_source"] == {"sheet": "项目进度计划", "row": 134}
    )

    assert first["tracking_node_wbs"] is None
    assert first["planned_close_date"] == "2025-12-25"
    assert compressed["tracking_node_wbs"] == "4.4.3"
    assert compressed["planned_close_date"] == "2026-03-11"


def test_audit_instance_golden_artifact_is_current() -> None:
    payload = build_audit_instance_payload(SOURCE)
    assert payload["summary"] == {
        "instance_count": 52,
        "product_count": 14,
        "process_count": 37,
        "company_count": 1,
    }
    assert GOLDEN_INSTANCES.read_text(encoding="utf-8") == canonical_json(payload)


def test_unknown_product_mapping_is_a_blocker() -> None:
    payload = parse_project_workbook(SOURCE)
    payload["quality_activities"][0]["linked_items"] = ("未知工作产品",)
    payload["config_items"].append(
        {
            "source": {"sheet": "配置项清单", "row": 99}, "source_order": 99,
            "sequence": 99, "name": "未知工作产品", "identifier": "X", "version": "V1.0",
            "item_type": "文档", "phase_wbs": "1", "phase_wbs_formula": None,
            "review_date": None,
        }
    )
    result = expand_audit_instances(payload)
    assert any(d.code == "IMPORT_UNKNOWN_CHECKLIST_MAPPING" and d.severity == "blocker" for d in result.diagnostics)
