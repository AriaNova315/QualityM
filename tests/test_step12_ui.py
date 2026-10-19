from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
API_JS = (ROOT / "app" / "static" / "api.js").read_text(encoding="utf-8")
INDEX = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")


def test_step12_exposes_every_core_workflow_from_one_navigation() -> None:
    for label in ("项目", "导入", "审核", "设置", "模板库", "导出"):
        assert f'label: "{label}"' in APP_JS
    for endpoint in (
        "/api/project-versions",
        "/api/imports/preview",
        "/api/audits/",
        "/api/checklist-items/",
        "/api/issues/",
        "/settings",
        "/exports",
        "/api/templates/checklists",
    ):
        assert endpoint in APP_JS


def test_api_module_normalizes_timeout_network_diagnostics_and_conflicts() -> None:
    assert "AbortController" in API_JS
    assert "REQUEST_TIMEOUT" in API_JS
    assert "NETWORK_ERROR" in API_JS
    assert "detail?.diagnostics" in API_JS
    assert "status === 409" in API_JS


def test_duplicate_submit_unsaved_and_delete_confirmation_guards_are_present() -> None:
    assert "if (this.isPending(key)) return null" in APP_JS
    assert 'window.addEventListener("beforeunload"' in APP_JS
    assert "this.dirty" in APP_JS
    assert "delete-preview" in APP_JS
    assert "delete-confirm" in APP_JS
    assert 'deleteConfirmation !== deletePreview?.confirmation_text' in INDEX


def test_import_preview_shows_named_fixed_mapping_suggestions() -> None:
    assert "product_mapping_resolutions" in APP_JS
    assert "自动匹配：${resolution.template_name}" in APP_JS
    assert "当前自动匹配：${resolution.template_name}" in APP_JS
    assert "确认后不会重新按规则替换" in INDEX
    assert "未发现可关联的工作产品" in INDEX
    assert '<h3>工作产品检查单映射</h3>' in INDEX
    assert '<h3 x-show="mappingSubjects.length">工作产品检查单映射</h3>' not in INDEX
    assert "hasUnresolvedMappings" in INDEX
    assert "hasImportBlockers" in INDEX
    assert "diagnosticText(item)" in INDEX
    assert "第 ${location.row} 行" in APP_JS


def test_audit_diagnostics_show_plain_chinese_instead_of_raw_codes() -> None:
    assert 'x-text="item.message || \'当前记录仍需处理\'"' in INDEX
    assert 'x-text="item.resolution || \'请核对并完成当前记录。\'"' in INDEX
    assert '<strong x-text="item.code"></strong><span>当前记录仍需处理</span>' not in INDEX


def test_nullable_editors_are_instantiated_only_after_data_loads() -> None:
    # Regression: ISSUE-001 — hidden Alpine forms evaluated null x-model targets.
    # Found by /qa on 2026-08-21
    # Report: .gstack/qa-reports/qa-report-127-0-0-1-2026-08-21.md
    assert '<template x-if="selectedAudit">' in INDEX
    assert '<template x-if="settings">' in INDEX
    assert 'x-model="selectedAudit.auditor"' in INDEX
    assert 'x-model="settings.config_item_number"' in INDEX


def test_issue_mutations_reload_full_audit_instead_of_rendering_summary() -> None:
    # Regression: ISSUE-002 — issue mutation summaries replaced the full audit editor.
    # Found by /qa on 2026-08-21
    # Report: .gstack/qa-reports/qa-report-127-0-0-1-2026-08-21.md
    assert APP_JS.count("await this.loadAudit(result.audit_id)") == 4
    assert "this.selectedAudit = await apiRequest(`/api/issues/" not in APP_JS


def test_audit_editor_supports_truthful_multi_issue_workflow() -> None:
    assert "selectedCandidateIds" in APP_JS
    assert 'type="checkbox" x-model="item.selectedCandidateIds"' in INDEX
    assert "创建自定义问题" in INDEX
    assert "保存为问题模板" in INDEX
    assert "待确认的建议值" in INDEX
    assert "整改建议：" in INDEX
    assert "实际整改措施" in INDEX
    assert "item.starred_snapshot" in INDEX
    assert "设为默认" not in INDEX


def test_custom_issue_discovery_date_defaults_to_audit_date() -> None:
    assert 'submitted_date: this.selectedAudit.audit_date || ""' in APP_JS
    assert 'submitted_date: issue.submitted_date || null' in APP_JS
    assert '<span>发现日期</span><input type="date" x-model="item.customIssue.submitted_date">' in INDEX
    custom_issue_form = INDEX.split('<h4>自定义问题</h4>', 1)[1].split('</section>', 1)[0]
    assert "item.customIssue.planned_close_date" not in custom_issue_form


def test_issue_save_reports_success_and_failure_in_a_modal() -> None:
    save_issue = APP_JS.split("async saveIssue(issue)", 1)[1].split("findIssueItem", 1)[0]
    assert 'this.showFeedback("success", "保存成功", "问题信息已保存。")' in save_issue
    assert 'this.showFeedback("error", "保存失败"' in save_issue
    assert "this.handleError(error)" not in save_issue
    assert 'x-ref="feedbackDialog"' in INDEX
    assert 'x-text="feedback.message"' in INDEX


def test_conflict_refresh_clears_false_unsaved_state() -> None:
    # Regression: ISSUE-003 — conflict refresh retained a stale beforeunload warning.
    # Found by /qa on 2026-08-21
    # Report: .gstack/qa-reports/qa-report-127-0-0-1-2026-08-21.md
    refresh = APP_JS.split("async refreshSelected()", 1)[1].split("},", 1)[0]
    assert "this.markSaved()" in refresh
