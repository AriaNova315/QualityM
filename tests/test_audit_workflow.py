from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest

from app.application.audit_service import (
    AuditWorkflowError,
    delete_issue,
    get_audit,
    set_item_conclusion,
    update_audit,
    update_issue,
)
from app.application.import_preview_service import create_preview
from app.application.project_import_service import commit_preview
from app.config import Settings
from app.database import initialize_database
from app.main import create_app


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "项目相关信息表.xlsx"


def run(coroutine):
    return asyncio.run(coroutine)


def committed_project(settings: Settings):
    run(
        initialize_database(
            settings.database_path,
            settings.migrations_dir,
            settings.bootstrap_seed_path,
            settings.seed_schema_path,
        )
    )
    preview = run(
        create_preview(
            settings.database_path,
            settings.import_temp_dir,
            SOURCE.name,
            SOURCE.read_bytes(),
            settings.import_preview_ttl_seconds,
            settings.upload_max_bytes,
        )
    )
    return run(commit_preview(settings.database_path, preview.token, {}, {}, False, "0.0"))


def test_commit_creates_complete_immutable_candidate_snapshots(test_settings: Settings) -> None:
    committed = committed_project(test_settings)
    with sqlite3.connect(test_settings.database_path) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM audit_instances ai
            WHERE ai.project_version_id = ? AND NOT EXISTS (
                SELECT 1 FROM checklist_instance_groups cig WHERE cig.audit_instance_id = ai.id
            )
            """,
            (committed.project_version_id,),
        ).fetchone()[0] == 0
        item_count = connection.execute(
            "SELECT COUNT(*) FROM checklist_instance_items"
        ).fetchone()[0]
        candidate_count = connection.execute(
            "SELECT COUNT(*) FROM checklist_instance_issue_templates"
        ).fetchone()[0]
        assert item_count > 361
        assert candidate_count > item_count * 2
        assert connection.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT cii.id, COUNT(ciit.id) AS candidate_count
                FROM checklist_instance_items cii
                JOIN checklist_instance_issue_templates ciit
                  ON ciit.checklist_instance_item_id = cii.id
                GROUP BY cii.id HAVING candidate_count < 2 OR candidate_count > 5
            )
            """
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM nonconformities").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM checklist_instance_items WHERE conclusion = '符合'"
        ).fetchone()[0] == item_count
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_instances WHERE snapshot_schema_version=2 AND user_edited=0"
        ).fetchone()[0] == committed.audit_instance_count

        snapshot = connection.execute(
            "SELECT id, source_issue_template_id, title_snapshot FROM checklist_instance_issue_templates LIMIT 1"
        ).fetchone()
        connection.execute(
            "UPDATE issue_templates SET title = '全局模板已修改' WHERE id = ?", (snapshot[1],)
        )
        connection.commit()
        assert connection.execute(
            "SELECT title_snapshot FROM checklist_instance_issue_templates WHERE id = ?",
            (snapshot[0],),
        ).fetchone()[0] == snapshot[2]


def test_audit_editing_enforces_invariants_and_optimistic_revision(test_settings: Settings) -> None:
    committed_project(test_settings)
    with sqlite3.connect(test_settings.database_path) as connection:
        row = connection.execute(
            """
            SELECT ai.id, ai.revision, cii.id, ciit.id
            FROM audit_instances ai
            JOIN checklist_instance_groups cig ON cig.audit_instance_id = ai.id
            JOIN checklist_instance_items cii ON cii.instance_group_id = cig.id
            JOIN checklist_instance_issue_templates ciit
              ON ciit.checklist_instance_item_id = cii.id
            WHERE cii.starred_snapshot = 0
            ORDER BY ai.id, cii.id, ciit.display_order, ciit.id LIMIT 1
            """
        ).fetchone()
    audit_id, revision, item_id, candidate_id = map(int, row)
    changed = run(
        set_item_conclusion(
            test_settings.database_path,
            item_id,
            revision,
            "不符合",
            [candidate_id],
            False,
        )
    )
    assert changed["revision"] == revision + 1

    with pytest.raises(AuditWorkflowError) as stale:
        run(
            set_item_conclusion(
                test_settings.database_path, item_id, revision, "符合", [], True
            )
        )
    assert stale.value.code == "AUDIT_REVISION_CONFLICT"

    audit = run(get_audit(test_settings.database_path, audit_id))
    item = next(
        item
        for group in audit["groups"]
        for item in group["items"]
        if item["id"] == item_id
    )
    issue = item["issues"][0]
    assert issue["actual_close_date"] == issue["planned_close_date"]
    assert issue["verification_date"] == issue["planned_close_date"]
    assert issue["verification_conclusion"] == "纠正措施已实施，不符合项问题均按期关闭。"
    audit_warning = next(
        diagnostic
        for diagnostic in audit["diagnostics"]
        if diagnostic["code"] == "AUDIT_UNCONFIRMED"
    )
    issue_warning = next(
        diagnostic
        for diagnostic in audit["diagnostics"]
        if diagnostic["location"].get("issue_id") == issue["id"]
    )
    assert audit_warning["message"] == "审核记录尚未确认"
    assert issue["title"] in issue_warning["message"]
    assert "关闭已确认" in issue_warning["resolution"]
    with pytest.raises(AuditWorkflowError) as premature_confirmation:
        run(
            update_audit(
                test_settings.database_path,
                audit_id,
                audit["revision"],
                {"confirmation_state": "confirmed"},
            )
        )
    assert premature_confirmation.value.code == "AUDIT_CONFIRMATION_INVALID"
    with pytest.raises(AuditWorkflowError) as outside_phase:
        run(
            update_issue(
                test_settings.database_path,
                issue["id"],
                audit["revision"],
                {"verification_date": "2099-01-01"},
            )
        )
    assert outside_phase.value.code == "AUDIT_ISSUE_DATE_INVALID"
    with pytest.raises(AuditWorkflowError) as missing_title:
        run(
            update_issue(
                test_settings.database_path,
                issue["id"],
                audit["revision"],
                {"title": "   "},
            )
        )
    assert missing_title.value.code == "AUDIT_ISSUE_INVALID"
    assert str(missing_title.value) == "问题标题不能为空"
    updated = run(
        update_issue(
            test_settings.database_path,
            issue["id"],
            audit["revision"],
            {"severity": "轻微", "confirmed": True},
        )
    )
    assert updated["revision"] == audit["revision"] + 1
    audit = run(get_audit(test_settings.database_path, audit_id))
    current_issue = next(
        issue
        for group in audit["groups"]
        for item in group["items"]
        for issue in item["issues"]
        if issue["id"] == updated["issue_id"]
    )
    assert (current_issue["severity"], current_issue["confirmed"], current_issue["suggested_closed"]) == (
        "轻微", 1, 0
    )
    assert audit["user_edited"] == 1

    with pytest.raises(AuditWorkflowError) as last_issue:
        run(
            delete_issue(
                test_settings.database_path, current_issue["id"], audit["revision"], False
            )
        )
    assert last_issue.value.code == "CHECKLIST_LAST_ISSUE_REQUIRES_CONCLUSION_CHANGE"
    deleted = run(
        delete_issue(
            test_settings.database_path, current_issue["id"], audit["revision"], True
        )
    )
    audit = run(get_audit(test_settings.database_path, audit_id))
    changed_item = next(
        item
        for group in audit["groups"]
        for item in group["items"]
        if item["id"] == deleted["item_id"]
    )
    assert changed_item["conclusion"] == "符合"
    assert changed_item["issues"] == []


def test_audit_api_returns_consistent_404_409_and_422(test_settings: Settings) -> None:
    committed = committed_project(test_settings)
    app = create_app(test_settings)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get(f"/api/project-versions/{committed.project_version_id}/audits")
            assert listed.status_code == 200
            audit = next(item for item in listed.json() if item["issue_count"] == 0)
            detail = await client.get(f"/api/audits/{audit['id']}")
            assert detail.status_code == 200
            missing = await client.get("/api/audits/999999")
            assert missing.status_code == 404
            assert missing.json()["detail"]["code"] == "AUDIT_NOT_FOUND"
            stale = await client.patch(
                f"/api/audits/{audit['id']}",
                json={"expected_revision": audit["revision"] + 1, "auditor": "新审核人"},
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "AUDIT_REVISION_CONFLICT"
            unchecked_item = next(
                item
                for group in detail.json()["groups"]
                for item in group["items"]
                if not item["starred_snapshot"] and item["candidate_templates"]
            )
            invalid = await client.put(
                f"/api/checklist-items/{unchecked_item['id']}/conclusion",
                json={
                    "expected_revision": audit["revision"],
                    "conclusion": "不符合",
                    "issue_template_ids": [999999],
                },
            )
            assert invalid.status_code == 422
            assert invalid.json()["detail"]["code"] == "AUDIT_ISSUE_TEMPLATE_INVALID"

            candidate_ids = [candidate["id"] for candidate in unchecked_item["candidate_templates"][:2]]
            made_noncompliant = await client.put(
                f"/api/checklist-items/{unchecked_item['id']}/conclusion",
                json={
                    "expected_revision": audit["revision"],
                    "conclusion": "不符合",
                    "issue_template_ids": candidate_ids,
                },
            )
            assert made_noncompliant.status_code == 200
            detail = await client.get(f"/api/audits/{audit['id']}")
            issues = [
                issue
                for group in detail.json()["groups"]
                for item in group["items"]
                for issue in item["issues"]
            ]
            assert len(issues) == len(candidate_ids) == 2
            issue = issues[0]
            confirmed_issue = await client.patch(
                f"/api/issues/{issue['id']}",
                json={
                    "expected_revision": made_noncompliant.json()["revision"],
                    "severity": "轻微",
                    "confirmed": True,
                },
            )
            assert confirmed_issue.status_code == 200
            premature_audit = await client.patch(
                f"/api/audits/{audit['id']}",
                json={
                    "expected_revision": confirmed_issue.json()["revision"],
                    "confirmation_state": "confirmed",
                },
            )
            assert premature_audit.status_code == 409
            assert premature_audit.json()["detail"]["code"] == "AUDIT_CONFIRMATION_INVALID"

    run(exercise())


def test_custom_issue_sets_noncompliant_and_is_saved_as_template_only_explicitly(
    test_settings: Settings,
) -> None:
    committed = committed_project(test_settings)
    app = create_app(test_settings)

    async def exercise() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            audits = (
                await client.get(
                    f"/api/project-versions/{committed.project_version_id}/audits"
                )
            ).json()
            detail = (await client.get(f"/api/audits/{audits[0]['id']}")).json()
            item = next(
                value
                for group in detail["groups"]
                for value in group["items"]
                if not value["starred_snapshot"]
                and len(value["candidate_templates"]) < 4
            )
            created = await client.post(
                f"/api/checklist-items/{item['id']}/issues",
                json={
                    "expected_revision": detail["revision"],
                    "title": "历史项目中的接口遗漏",
                    "description": "任务书未完整覆盖外部接口约束。",
                    "severity": "一般",
                    "planned_close_date": detail["audit_date"],
                    "corrective_action": "补充接口约束并组织复核",
                },
            )
            assert created.status_code == 201
            refreshed = (await client.get(f"/api/audits/{detail['id']}")).json()
            changed_item = next(
                value
                for group in refreshed["groups"]
                for value in group["items"]
                if value["id"] == item["id"]
            )
            assert changed_item["conclusion"] == "不符合"
            assert len(changed_item["issues"]) == 1
            custom_issue = changed_item["issues"][0]
            assert custom_issue["planned_close_date"] == detail["audit_date"]
            assert custom_issue["actual_close_date"] == detail["audit_date"]
            assert custom_issue["verification_date"] == detail["audit_date"]
            assert custom_issue["verification_conclusion"] == "纠正措施已实施，不符合项问题均按期关闭。"
            assert custom_issue["suggested_closed"] == 1

            before_templates = sum(
                len(value["issues"])
                for checklist in (await client.get("/api/templates/checklists")).json()
                for group in checklist["groups"]
                for value in group["items"]
                if value["id"] == item["source_item_id"]
            )
            saved = await client.post(
                f"/api/issues/{created.json()['issue_id']}/save-as-template",
                json={
                    "title": "外部接口约束覆盖不完整",
                    "description": "工作产品未完整覆盖适用的外部接口约束。",
                    "corrective_suggestion": "补充接口约束并完成相关方复核。",
                    "default_severity": "一般",
                },
            )
            assert saved.status_code == 201
            assert saved.json()["stable_key"].startswith("user.issue.")
            after_templates = sum(
                len(value["issues"])
                for checklist in (await client.get("/api/templates/checklists")).json()
                for group in checklist["groups"]
                for value in group["items"]
                if value["id"] == item["source_item_id"]
            )
            assert after_templates == before_templates + 1

            starred = next(
                value
                for group in refreshed["groups"]
                for value in group["items"]
                if value["starred_snapshot"]
            )
            invalid = await client.post(
                f"/api/checklist-items/{starred['id']}/issues",
                json={
                    "expected_revision": refreshed["revision"],
                    "title": "星标问题",
                    "description": "星标项不能使用一般严重程度。",
                    "severity": "一般",
                },
            )
            assert invalid.status_code == 422
            assert invalid.json()["detail"]["code"] == "ISSUE_SEVERITY_STARRED_REQUIRED"

    run(exercise())
