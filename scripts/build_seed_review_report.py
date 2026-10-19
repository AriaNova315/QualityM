from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "seeds" / "checklist-library.json"
DEFAULT_COVERAGE = ROOT / "seeds" / "checklist-library.coverage.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "step6" / "TEMPLATE_REVIEW.md"


def _representative_item(checklist: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    candidates = [
        (group["name"], item)
        for group in checklist["groups"]
        for item in group["items"]
    ]
    return max(
        candidates,
        key=lambda value: (
            len(value[1]["issue_templates"]),
            int(bool(value[1].get("starred"))),
            len(value[1]["content"]),
        ),
    )


def build_report(seed: dict[str, Any], coverage: dict[str, Any]) -> str:
    quality = coverage["quality"]
    lines = [
        "# QualityM Step 6 初始问题模板库抽查报告",
        "",
        f"种子版本：`{seed['seed_version']}`",
        "",
        "## 覆盖结论",
        "",
        f"- 检查单：{coverage['checklist_count']} 类（工作产品 {coverage['product_checklist_count']}，过程 {coverage['process_checklist_count']}）",
        f"- 分组：{coverage['group_count']} 个；检查项：{coverage['item_count']} 个",
        f"- 问题模板：{coverage['issue_template_count']} 条；每项 {coverage['minimum_issue_templates_per_item']}～{coverage['maximum_issue_templates_per_item']} 条",
        f"- 带星号检查项：{coverage['starred_item_count']} 个，默认严重程度为“严重”",
        f"- 内容质量扫描：{quality['finding_count']} 个发现（机械否定、项内近重复、模板数量和默认模板规则）",
        "- `工作产品评审过程`、`测试过程` 等原始分组及组内编号均保留。",
        "",
        "模板数量分布：",
        "",
        "| 每检查项模板数 | 检查项数 |",
        "|---:|---:|",
    ]
    for count, item_count in coverage["template_count_distribution"].items():
        lines.append(f"| {count} | {item_count} |")
    lines.extend(
        [
            "",
            "问题模型覆盖：",
            "",
            "| 问题模型 | 模板数 |",
            "|---|---:|",
        ]
    )
    for family, count in coverage["issue_family_counts"].items():
        lines.append(f"| `{family}` | {count} |")

    lines.extend(
        [
            "",
            "## 每类检查单代表性模板",
            "",
            "以下每类选择候选数最多、优先带星号且内容较完整的一项，供业务抽查。",
            "",
        ]
    )
    for checklist in seed["checklists"]:
        group_name, item = _representative_item(checklist)
        lines.extend(
            [
                f"### {checklist['name']}",
                "",
                f"- 类型：`{checklist['audit_kind']}`；分组：{group_name}；检查项：{item['display_no']}{'*' if item.get('starred') else ''}",
                f"- 原始检查内容：{item['content']}",
                "",
                "| 默认 | 问题标题 | 问题描述 | 整改建议 | 严重程度 |",
                "|---|---|---|---|---|",
            ]
        )
        for issue in item["issue_templates"]:
            values = [
                "是" if issue.get("is_default") else "否",
                issue["title"],
                issue["description"],
                issue["corrective_suggestion"],
                issue["default_severity"],
            ]
            escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
            lines.append("| " + " | ".join(escaped) + " |")
        lines.append("")

    lines.extend(
        [
            "## 抽查门禁",
            "",
            "请确认代表性模板是否符合实际质量审核语言习惯，重点检查问题是否具体、整改建议是否可执行、严重程度默认值是否合理。业务抽查通过前，不进入 Step 7。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Step 6 template review report")
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    seed = json.loads(args.seed.read_text(encoding="utf-8"))
    coverage = json.loads(args.coverage.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_report(seed, coverage), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
