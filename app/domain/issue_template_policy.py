from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


SEVERITIES = ("轻微", "一般", "严重")
NONSTARRED_SEVERITIES = ("轻微", "一般")
MAX_ENABLED_TEMPLATES = 4


@dataclass(frozen=True, slots=True)
class IssuePolicyViolation(ValueError):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


def allowed_severities(starred: bool) -> tuple[str, ...]:
    return ("严重",) if starred else NONSTARRED_SEVERITIES


def validate_issue_severity(*, starred: bool, severity: str) -> None:
    if severity not in SEVERITIES:
        raise IssuePolicyViolation("ISSUE_SEVERITY_INVALID", "问题严重程度无效")
    if starred and severity != "严重":
        raise IssuePolicyViolation(
            "ISSUE_SEVERITY_STARRED_REQUIRED",
            "带星检查项的问题严重程度必须为严重",
        )
    if not starred and severity == "严重":
        raise IssuePolicyViolation(
            "ISSUE_SEVERITY_NONSTARRED_INVALID",
            "非星检查项的问题严重程度只能为轻微或一般",
        )


def validate_enabled_template_addition(enabled_count: int) -> None:
    if enabled_count >= MAX_ENABLED_TEMPLATES:
        code = (
            "ISSUE_TEMPLATE_LEGACY_OVER_LIMIT"
            if enabled_count > MAX_ENABLED_TEMPLATES
            else "ISSUE_TEMPLATE_ENABLED_LIMIT"
        )
        raise IssuePolicyViolation(code, "每个检查项最多启用四条问题模板")


def coverage_diagnostic(enabled_count: int) -> dict[str, object] | None:
    if enabled_count == 0:
        return {
            "code": "ISSUE_TEMPLATE_COVERAGE_EMPTY",
            "severity": "warning",
            "message": "当前检查项没有启用的问题模板，审核时仍可创建自定义问题。",
        }
    if enabled_count > MAX_ENABLED_TEMPLATES:
        return {
            "code": "ISSUE_TEMPLATE_LEGACY_OVER_LIMIT",
            "severity": "warning",
            "message": "当前检查项启用问题模板超过四条，请逐步停用或合并。",
        }
    return None


def validate_close_confirmation(values: Mapping[str, object]) -> None:
    if not values.get("confirmed"):
        return
    required = (
        "corrective_action",
        "actual_close_date",
        "verification_date",
        "verification_conclusion",
    )
    if any(not values.get(field) for field in required):
        raise IssuePolicyViolation(
            "ISSUE_CLOSE_CONFIRMATION_INCOMPLETE",
            "确认关闭需要完整的实际整改措施、关闭日期和验证信息",
        )
