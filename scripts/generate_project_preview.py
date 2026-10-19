from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.importers.project_workbook import parse_project_workbook


DEFAULT_SOURCE = ROOT / "项目相关信息表.xlsx"
DEFAULT_JSON = ROOT / "tests" / "fixtures" / "project-preview.golden.json"
DEFAULT_HTML = ROOT / "tests" / "fixtures" / "project-preview.golden.html"


def canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def preview_html(payload: dict) -> str:
    project = payload["project"]
    phase_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(phase['wbs'])}</td>"
        f"<td>{html.escape(phase['name'])}</td>"
        f"<td>{html.escape(phase['start_date'] or '—')}</td>"
        f"<td>{html.escape(phase['end_date'] or '—')}</td>"
        f"<td>{html.escape(phase['source']['sheet'])} 第 {phase['source']['row']} 行</td>"
        "</tr>"
        for phase in payload["phases"]
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>QualityM 项目导入预览基线</title>
<style>body{{font:14px 'Microsoft YaHei',sans-serif;margin:32px;color:#19324a}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd8e2;padding:8px;text-align:left}}th{{background:#eaf0f6}}.summary{{display:flex;gap:24px;margin:18px 0}}</style></head>
<body>
<h1>{html.escape(str(project['project_name']))}</h1>
<p>项目代号：{html.escape(str(project['project_code']))}｜项目组长：{html.escape(str(project['project_leader']))}｜项目QA：{html.escape(str(project['qa_person']))}</p>
<div class="summary"><strong>阶段 {len(payload['phases'])}</strong><strong>跟踪节点 {len(payload['tracking_nodes'])}</strong><strong>质量活动 {len(payload['quality_activities'])}</strong><strong>配置项 {len(payload['config_items'])}</strong></div>
<table><thead><tr><th>WBS</th><th>阶段名称</th><th>开始日期</th><th>结束日期</th><th>来源</th></tr></thead><tbody>{phase_rows}</tbody></table>
<p>排除顶层活动：公司质量验证（来源：项目进度计划 第 139 行）</p>
</body></html>\n"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate QualityM project preview golden artifacts")
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    args = parser.parse_args()
    payload = parse_project_workbook(args.source)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(canonical_json(payload), encoding="utf-8")
    args.html.write_text(preview_html(payload), encoding="utf-8")
    print(
        f"Generated preview: {len(payload['phases'])} phases, "
        f"{len(payload['quality_activities'])} quality activities, "
        f"{len(payload['config_items'])} config items"
    )


if __name__ == "__main__":
    main()

