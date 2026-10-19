from __future__ import annotations

import hashlib
import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.seeds import SeedValidationError, analyze_seed_quality, validate_seed_payload
from app.config import Settings
from app.database import connect, initialize_database, transaction
from scripts.extract_checklist_seeds import extract_library, write_canonical_json
from scripts.build_seed_review_report import build_report


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "seeds" / "checklist-library.json"
COVERAGE_PATH = ROOT / "seeds" / "checklist-library.coverage.json"
SCHEMA_PATH = ROOT / "seeds" / "schema" / "checklist-library.schema.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_extracted_library_matches_authoritative_baseline() -> None:
    payload, report = extract_library()

    assert report["checklist_count"] == 25
    assert report["product_checklist_count"] == 15
    assert report["process_checklist_count"] == 10
    assert report["group_count"] == 28
    assert report["visible_group_count"] == 4
    assert report["item_count"] == 361
    assert report["starred_item_count"] == 97
    assert report["issue_template_count"] == 970
    assert report["minimum_issue_templates_per_item"] == 2
    assert report["maximum_issue_templates_per_item"] == 4
    assert report["template_count_distribution"] == {
        "2": 131,
        "3": 212,
        "4": 18,
        "5": 0,
        "6": 0,
    }
    assert report["quality"]["finding_count"] == 0
    validate_seed_payload(payload, SCHEMA_PATH)


def test_explicit_process_groups_and_local_numbering_are_preserved() -> None:
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    by_key = {checklist["stable_key"]: checklist for checklist in payload["checklists"]}

    requirements = by_key["process.requirements-development-management"]
    assert [group["name"] for group in requirements["groups"]] == [
        "需求开发与确认",
        "需求管理",
    ]
    assert all(group["display_heading"] for group in requirements["groups"])

    vv = by_key["process.verification-validation"]
    assert [group["name"] for group in vv["groups"]] == [
        "通用检查项",
        "工作产品评审过程",
        "测试过程",
    ]
    assert [group["display_heading"] for group in vv["groups"]] == [False, True, True]
    assert [group["items"][0]["display_no"] for group in vv["groups"]] == ["1", "1", "1"]
    assert len(
        {
            item["stable_key"]
            for group in vv["groups"]
            for item in group["items"]
        }
    ) == 25


def test_each_item_has_practical_candidate_range_and_starred_issues_are_severe() -> None:
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    items = [
        item
        for checklist in payload["checklists"]
        for group in checklist["groups"]
        for item in group["items"]
    ]
    assert all(2 <= len(item["issue_templates"]) <= 6 for item in items)
    assert all(
        item["issue_templates"][0]["is_default"]
        and all(not issue["is_default"] for issue in item["issue_templates"][1:])
        for item in items
    )
    assert all(
        issue["default_severity"]
        in ({"严重"} if item["starred"] else {"轻微", "一般"})
        for item in items
        for issue in item["issue_templates"]
    )
    assert all(
        item["content"] not in issue["description"]
        for item in items
        for issue in item["issue_templates"]
    )
    assert all(
        "是否" not in issue["description"] and "有无" not in issue["description"]
        for item in items
        for issue in item["issue_templates"]
    )


def test_every_checklist_uses_curated_specific_issues() -> None:
    payload, _ = extract_library()
    items = [
        item
        for checklist in payload["checklists"]
        for group in checklist["groups"]
        for item in group["items"]
    ]
    issues = [issue for item in items for issue in item["issue_templates"]]

    assert len(items) == 361
    assert all(2 <= len(item["issue_templates"]) <= 4 for item in items)
    assert not {
        "必要内容覆盖不完整",
        "审核依据与结论不能相互支撑",
        "相关内容存在一致性缺口",
        "双向追踪关系不完整",
        "判定准则缺乏可验证性",
        "实际执行与既定过程存在偏差",
    } & {issue["title"] for issue in issues}

    checklist = next(
        value
        for value in payload["checklists"]
        if value["stable_key"] == "product.software-development-plan"
    )
    development_items = [item for group in checklist["groups"] for item in group["items"]]
    development_issues = [
        issue for item in development_items for issue in item["issue_templates"]
    ]
    assert len(development_items) == 25
    assert len(development_issues) == 92
    first = development_items[0]["issue_templates"]
    assert [(issue["title"], issue["default_severity"]) for issue in first] == [
        ("未按规定模板编制软件开发计划", "一般"),
        ("模板格式要素填写不规范", "轻微"),
    ]
    starred = [item for item in development_items if item["starred"]]
    assert starred and all(
        issue["default_severity"] == "严重"
        for item in starred
        for issue in item["issue_templates"]
    )


def test_quality_report_rejects_mechanical_and_near_duplicate_templates() -> None:
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    issues = payload["checklists"][0]["groups"][0]["items"][0]["issue_templates"]
    issues[0]["title"] = "检查项要求未满足"
    issues[1].update(
        {
            "title": issues[0]["title"],
            "description": issues[0]["description"],
            "corrective_suggestion": issues[0]["corrective_suggestion"],
        }
    )

    quality = analyze_seed_quality(payload)
    codes = {finding["code"] for finding in quality["findings"]}
    assert "SEED_MECHANICAL_NEGATION" in codes
    assert "SEED_NEAR_DUPLICATE_TEMPLATE" in codes


def test_source_locations_trace_every_item_to_original_cell() -> None:
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    for checklist in payload["checklists"]:
        assert checklist["source_workbook"] in {"工作产品检查单.xlsx", "过程检查单.xlsx"}
        for group in checklist["groups"]:
            for item in group["items"]:
                row = item["source"]["row"]
                assert item["source"]["content_cell"] == f"B{row}"


def test_extraction_is_byte_for_byte_deterministic(tmp_path: Path) -> None:
    payload, report = extract_library()
    seed_a = tmp_path / "a.json"
    seed_b = tmp_path / "b.json"
    report_a = tmp_path / "a-report.json"
    report_b = tmp_path / "b-report.json"
    write_canonical_json(seed_a, payload)
    write_canonical_json(seed_b, payload)
    write_canonical_json(report_a, report)
    write_canonical_json(report_b, report)

    assert seed_a.read_bytes() == seed_b.read_bytes() == SEED_PATH.read_bytes()
    assert report_a.read_bytes() == report_b.read_bytes() == COVERAGE_PATH.read_bytes()


def test_schema_rejects_unknown_fields() -> None:
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    payload["checklists"][0]["unexpected"] = True

    with pytest.raises(SeedValidationError, match="JSON Schema violation"):
        validate_seed_payload(payload, SCHEMA_PATH)


def test_validation_command_imports_full_library() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/validate_seed_library.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        "checklists": 25,
        "groups": 28,
        "issue_templates": 970,
        "items": 361,
        "maximum_issue_templates_per_item": 4,
        "minimum_issue_templates_per_item": 2,
        "quality_findings": 0,
        "seed_version": "2.2.0",
    }


def test_packaged_seed_version_has_immutable_content_fingerprint() -> None:
    """Changing packaged seed content requires a new seed_version."""
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    assert (
        payload["seed_version"],
        hashlib.sha256(SEED_PATH.read_bytes()).hexdigest(),
    ) == (
        "2.2.0",
        "689e7c18c6454fdc81da2852953b59fed656d19f6cc781754c790b0d14376ca2",
    )


def test_business_review_report_covers_every_checklist() -> None:
    payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    coverage = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    report = build_report(payload, coverage)

    assert report.count("\n### ") == 25
    assert "业务抽查通过前，不进入 Step 7" in report
    assert "内容质量扫描：0 个发现" in report


def test_mapping_rule_can_reference_an_imported_template(test_settings: Settings) -> None:
    asyncio.run(
        initialize_database(
            test_settings.database_path,
            test_settings.migrations_dir,
            test_settings.bootstrap_seed_path,
            test_settings.seed_schema_path,
        )
    )

    async def insert_mapping() -> None:
        async with connect(test_settings.database_path) as connection:
            template = await (
                await connection.execute(
                    "SELECT id FROM checklist_templates WHERE stable_key = ?",
                    ("product.software-design",),
                )
            ).fetchone()
            assert template is not None
            async with transaction(connection):
                await connection.execute(
                    """
                    INSERT INTO mapping_rules(
                        target_kind, match_kind, pattern, checklist_template_id,
                        priority, source
                    ) VALUES ('product', 'exact', '软件设计说明', ?, 100, 'builtin')
                    """,
                    (template["id"],),
                )

    asyncio.run(insert_mapping())
    with sqlite3.connect(test_settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM mapping_rules").fetchone()[0] == 1
