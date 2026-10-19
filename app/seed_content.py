from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_FAMILY_TEXT = {
    "noncompliant": (
        "必要内容覆盖不完整",
        "相关工作产品未完整覆盖本检查项要求的必要内容和适用场景，当前结果不能支撑该项要求已经全部落实。",
        "补齐遗漏内容和适用场景，核对相关输入后组织复核，并保留复核结论。",
    ),
    "evidence-insufficient": (
        "审核依据与结论不能相互支撑",
        "现有文档、记录或审批痕迹未形成与审核结论直接对应的完整证据链，结论缺少可复查依据。",
        "补充与结论直接对应的文档、记录或审批证据，标明来源和版本后重新确认。",
    ),
    "consistency-gap": (
        "相关内容存在一致性缺口",
        "相关工作产品与计划、上游输入或配套记录之间缺少一致性核对，存在术语、数据口径或状态不一致。",
        "逐项核对相关输入和配套记录，统一术语、状态与数据口径，并记录差异处理结果。",
    ),
    "traceability-gap": (
        "双向追踪关系不完整",
        "当前记录缺少清晰的来源、去向或对应关系，无法稳定追溯到相关输入和验证结果。",
        "建立并复核双向追踪关系，补充唯一标识、来源和验证去向，确保变更后仍可追溯。",
    ),
    "criteria-not-verifiable": (
        "判定准则缺乏可验证性",
        "现有条件、指标或通过准则不够明确，执行人员难以据此得到可重复、可复核的判定结果。",
        "将条件、输入、预期结果和通过准则改写为可执行、可观察、可复核的表述。",
    ),
    "change-not-synchronized": (
        "变更内容未完成同步闭环",
        "内容调整后，相关版本、基线、引用或执行记录未完成同步检查，仍可能继续使用过期信息。",
        "识别变更影响范围，同步更新相关文档和记录，完成评审、批准与基线状态核对。",
    ),
    "review-evidence-gap": (
        "评审与批准记录不完整",
        "评审意见、问题处理、批准结论或参与人员记录不完整，过程完成情况无法复查。",
        "补齐评审输入、意见处理、批准结论和参与记录，确认遗留问题均有明确处置状态。",
    ),
    "process-execution-gap": (
        "实际执行与既定过程存在偏差",
        "实际执行记录与既定计划、职责或流程要求未能对应，偏差原因、影响及处理结果不清晰。",
        "对照既定过程补齐执行记录；对合理偏差说明原因、影响和批准情况，并跟踪纠正结果。",
    ),
    "responsibility-boundary": (
        "职责与接口边界不清晰",
        "责任主体、协作接口或完成条件界定不清，可能造成任务遗漏、重复处理或交付责任争议。",
        "明确责任人、协作方、输入输出和完成条件，并在相关计划或记录中保持一致。",
    ),
    "risk-handling-gap": (
        "风险与异常处理安排不足",
        "异常条件、约束或风险处置覆盖不足，出现偏差时缺少明确的响应措施和升级路径。",
        "补充异常条件、风险触发标准、处置责任和跟踪方式，并验证关键风险已有可执行措施。",
    ),
}

_KEYWORDS = {
    "traceability-gap": ("追踪", "追溯", "对应", "接口", "需求", "配置项", "覆盖"),
    "criteria-not-verifiable": ("测试", "验证", "确认", "准则", "判定", "验收", "指标", "方法"),
    "change-not-synchronized": ("变更", "更改", "修订", "版本", "基线", "状态"),
    "review-evidence-gap": ("评审", "审核", "批准", "审批", "签署", "会议", "记录"),
    "process-execution-gap": ("过程", "活动", "执行", "监控", "跟踪", "计划", "实施"),
    "responsibility-boundary": ("职责", "责任", "人员", "组织", "接口", "资源", "分工"),
    "risk-handling-gap": ("风险", "异常", "约束", "安全", "问题", "偏差", "应急"),
    "consistency-gap": ("一致", "准确", "正确", "完整", "协调", "符合", "合理"),
}


def _ranked_families(content: str, checklist_key: str, starred: bool) -> list[str]:
    scores: list[tuple[int, int, str]] = []
    for order, (family, keywords) in enumerate(_KEYWORDS.items()):
        score = sum(content.count(keyword) for keyword in keywords)
        if family == "process-execution-gap" and checklist_key.startswith("process."):
            score += 1
        scores.append((-score, order, family))
    ranked = [family for negative_score, _, family in sorted(scores) if negative_score < 0]
    for fallback in ("consistency-gap", "traceability-gap", "criteria-not-verifiable"):
        if fallback not in ranked:
            ranked.append(fallback)

    compound_markers = len(re.findall(r"[、，,；;]|以及|并且|或", content))
    desired = 3
    if starred or len(content) >= 36 or compound_markers >= 2:
        desired = 4
    if len(content) >= 60 or compound_markers >= 4:
        desired = 5
    if len(content) >= 90 and compound_markers >= 5:
        desired = 6
    return ranked[: max(1, desired - 2)]


def generate_issue_templates(
    checklist_key: str,
    group_key: str,
    item_key: str,
    content: str,
    starred: bool,
) -> list[dict[str, Any]]:
    """Build deterministic, reviewable issue candidates for one checklist item."""
    severity = "严重" if starred else "一般"
    issue_prefix = f"{checklist_key}.{group_key}.{item_key}"
    families = ["noncompliant", "evidence-insufficient"]
    families.extend(
        family
        for family in _ranked_families(content, checklist_key, starred)
        if family not in families
    )
    issues: list[dict[str, Any]] = []
    for index, family in enumerate(families):
        title, description, suggestion = _FAMILY_TEXT[family]
        issues.append(
            {
                "stable_key": f"{issue_prefix}.{family}",
                "title": title,
                "description": description,
                "corrective_suggestion": suggestion,
                "default_severity": severity,
                "placeholders": [],
                # Compatibility-only seed field. Runtime import clears/ignores it.
                "is_default": index == 0,
            }
        )
    return issues


def issue_family(stable_key: str) -> str:
    return stable_key.rsplit(".", maxsplit=1)[-1]


def issue_family_counts(issue_keys: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for stable_key in issue_keys:
        family = issue_family(stable_key)
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))
