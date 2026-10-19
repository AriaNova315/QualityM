from __future__ import annotations

import pytest

from app.domain.issue_template_policy import (
    IssuePolicyViolation,
    allowed_severities,
    coverage_diagnostic,
    validate_close_confirmation,
    validate_enabled_template_addition,
    validate_issue_severity,
)


@pytest.mark.parametrize(
    ("starred", "severity"),
    ((True, "严重"), (False, "轻微"), (False, "一般")),
)
def test_allowed_issue_severity_matrix(starred: bool, severity: str) -> None:
    validate_issue_severity(starred=starred, severity=severity)


@pytest.mark.parametrize(
    ("starred", "severity", "code"),
    (
        (True, "轻微", "ISSUE_SEVERITY_STARRED_REQUIRED"),
        (True, "一般", "ISSUE_SEVERITY_STARRED_REQUIRED"),
        (False, "严重", "ISSUE_SEVERITY_NONSTARRED_INVALID"),
        (False, "未知", "ISSUE_SEVERITY_INVALID"),
    ),
)
def test_rejected_issue_severity_matrix(
    starred: bool, severity: str, code: str
) -> None:
    with pytest.raises(IssuePolicyViolation) as error:
        validate_issue_severity(starred=starred, severity=severity)
    assert error.value.code == code


def test_template_count_and_coverage_rules() -> None:
    validate_enabled_template_addition(3)
    with pytest.raises(IssuePolicyViolation) as limit:
        validate_enabled_template_addition(4)
    assert limit.value.code == "ISSUE_TEMPLATE_ENABLED_LIMIT"
    with pytest.raises(IssuePolicyViolation) as legacy:
        validate_enabled_template_addition(5)
    assert legacy.value.code == "ISSUE_TEMPLATE_LEGACY_OVER_LIMIT"
    assert coverage_diagnostic(0)["code"] == "ISSUE_TEMPLATE_COVERAGE_EMPTY"
    assert coverage_diagnostic(5)["code"] == "ISSUE_TEMPLATE_LEGACY_OVER_LIMIT"
    assert coverage_diagnostic(2) is None
    assert allowed_severities(True) == ("严重",)


def test_close_confirmation_requires_actual_corrective_and_verification_facts() -> None:
    with pytest.raises(IssuePolicyViolation) as incomplete:
        validate_close_confirmation(
            {
                "confirmed": True,
                "actual_close_date": "2026-01-01",
                "verification_date": "2026-01-01",
                "verification_conclusion": "通过",
            }
        )
    assert incomplete.value.code == "ISSUE_CLOSE_CONFIRMATION_INCOMPLETE"
    validate_close_confirmation(
        {
            "confirmed": True,
            "corrective_action": "已完成整改",
            "actual_close_date": "2026-01-01",
            "verification_date": "2026-01-01",
            "verification_conclusion": "通过",
        }
    )
