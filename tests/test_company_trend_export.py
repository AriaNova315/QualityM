from __future__ import annotations

from dataclasses import replace
import asyncio
from pathlib import Path
import pytest
from openpyxl import load_workbook

from app.application.export_snapshot_service import load_export_snapshot
from app.application.import_preview_service import create_preview
from app.application.project_import_service import commit_preview
from app.database import initialize_database

from app.excel.company_trend_builder import (
    TREND_SHEET, build_complete_export_package,
)
from app.excel.phase_workbook_builder import build_all_phase_workbooks
from app.excel.validator import validate_company_trend_workbooks


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def export_context(test_settings):
    root = Path(__file__).resolve().parents[1]
    run(initialize_database(test_settings.database_path, test_settings.migrations_dir, test_settings.bootstrap_seed_path, test_settings.seed_schema_path))
    source = root / "项目相关信息表.xlsx"
    preview = run(create_preview(test_settings.database_path, test_settings.import_temp_dir, source.name, source.read_bytes(), test_settings.import_preview_ttl_seconds, test_settings.upload_max_bytes))
    committed = run(commit_preview(test_settings.database_path, preview.token, {}, {}, False, "0.0"))
    return run(load_export_snapshot(test_settings.database_path, committed.project_version_id))


def test_complete_package_has_manifest_company_trend_links_and_charts(export_context, tmp_path) -> None:
    snapshot = export_context
    package = build_complete_export_package(snapshot, tmp_path, build_all_phase_workbooks)
    assert package.output_dir.parent == tmp_path
    assert package.company_workbook and package.company_workbook.is_file()
    assert package.trend_workbook.is_file()
    assert set(package.manifest.read_text(encoding="utf-8").splitlines()) == {
        path.name for path in (*package.phase_workbooks, package.company_workbook, package.trend_workbook)
    }
    validate_company_trend_workbooks(package.company_workbook, package.trend_workbook)
    for path in (*package.phase_workbooks, package.company_workbook, package.trend_workbook):
        exported = load_workbook(path, data_only=False, keep_links=True)
        try:
            assert all(not sheet.protection.sheet for sheet in exported.worksheets)
        finally:
            exported.close()
    workbook = load_workbook(package.trend_workbook, data_only=False, keep_links=True)
    try:
        assert len(workbook[TREND_SHEET]._charts) == 2
        assert len(workbook._external_links) == sum(
            any(audit.report_scope == "phase" for audit in phase.audits)
            for phase in snapshot.phases
        )
        assert workbook[TREND_SHEET]["B4"].data_type == "f"
        assert workbook[TREND_SHEET]["Q3"].value == "类别"
        assert workbook[TREND_SHEET]["Q11"].value == "LD"
        assert workbook[TREND_SHEET]["R4"].value == f"=F{4 + len(snapshot.phases)}"
        assert workbook[TREND_SHEET]["S4"].value == f"=IFERROR(R4/$B${4 + len(snapshot.phases)},0)"
        assert workbook[TREND_SHEET]["S4"].number_format == "0.0%"
        line, pie = workbook[TREND_SHEET]._charts
        assert len(line.ser) == 2
        assert len(pie.ser) == 1
    finally:
        workbook.close()


def test_no_company_activity_skips_company_workbook_and_empty_phase_has_no_link(export_context, tmp_path) -> None:
    snapshot = export_context
    phases = tuple(replace(phase, audits=()) if phase.sort_order == 1 else phase for phase in snapshot.phases)
    phases = tuple(replace(phase, audits=tuple(a for a in phase.audits if a.report_scope == "phase")) for phase in phases)
    package = build_complete_export_package(replace(snapshot, phases=phases), tmp_path, build_all_phase_workbooks)
    assert package.company_workbook is None
    workbook = load_workbook(package.trend_workbook, data_only=False, keep_links=True)
    try:
        assert workbook[TREND_SHEET]["B4"].value == 0
        assert len(workbook._external_links) == sum(
            any(audit.report_scope == "phase" for audit in phase.audits)
            for phase in phases
        )
    finally:
        workbook.close()


def test_trend_conclusion_uses_manual_override_and_empty_override_uses_formula(export_context, tmp_path) -> None:
    overridden = replace(export_context, trend_conclusion_override="人工审核结论")
    package = build_complete_export_package(overridden, tmp_path / "override", build_all_phase_workbooks)
    workbook = load_workbook(package.trend_workbook, data_only=False, keep_links=True)
    try:
        conclusion_row = 4 + len(overridden.phases) + 2
        assert workbook[TREND_SHEET].cell(conclusion_row, 1).value == "人工审核结论"
    finally:
        workbook.close()

    package = build_complete_export_package(export_context, tmp_path / "formula", build_all_phase_workbooks)
    workbook = load_workbook(package.trend_workbook, data_only=False, keep_links=True)
    try:
        conclusion_row = 4 + len(export_context.phases) + 2
        assert workbook[TREND_SHEET].cell(conclusion_row, 1).data_type == "f"
    finally:
        workbook.close()
