from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ActivityKind = Literal["product", "process", "company", "report", "unknown"]


@dataclass(frozen=True, slots=True)
class ActivityClassification:
    kind: ActivityKind
    report_scope: Literal["phase", "company"] | None
    process_code: str | None = None


PROCESS_PATTERNS = (
    ("项目策划", "PP"),
    ("项目监控", "PMC"),
    ("需求开发与管理", "RDM"),
    ("验证与确认", "VV"),
    ("配置管理", "CM"),
    ("测量与绩效管理", "MPM"),
    ("运行维护", "MT"),
    ("领导作用", "LD"),
    ("实施基础", "II"),
    ("质量保证", "QA"),
)


def classify_activity(name: str) -> ActivityClassification:
    compact = "".join(name.split())
    if compact.startswith(("拟制质量审核报告", "拟制质量工作总结报告")):
        return ActivityClassification("report", None)
    if compact.startswith("产品审核-"):
        return ActivityClassification("product", "phase")
    if compact.startswith("审核"):
        for token, code in PROCESS_PATTERNS:
            if token in compact:
                if code == "QA":
                    return ActivityClassification("company", "company", code)
                return ActivityClassification("process", "phase", code)
    return ActivityClassification("unknown", None)

