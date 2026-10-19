from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal


MappingSource = Literal["project", "user", "builtin_exact", "builtin_alias"]


@dataclass(frozen=True, slots=True)
class MappingCandidate:
    stable_key: str
    priority: int
    source: MappingSource


class MappingError(ValueError):
    def __init__(self, code: str, subject: str) -> None:
        super().__init__(subject)
        self.code = code
        self.subject = subject


PROCESS_TEMPLATE_BY_CODE = {
    "PP": "process.project-planning",
    "PMC": "process.project-monitoring",
    "RDM": "process.requirements-development-management",
    "VV": "process.verification-validation",
    "CM": "process.configuration-management",
    "MPM": "process.measurement-performance-management",
    "MT": "process.operation-maintenance",
    "LD": "process.leadership",
    "II": "process.implementation-infrastructure",
    "QA": "process.quality-assurance",
}

PRODUCT_EXACT = {
    "软件研制任务书": "product.software-task-book",
    "软件开发计划": "product.software-development-plan",
    "软件配置管理计划": "product.configuration-management-plan",
    "软件质量保证计划": "product.quality-assurance-plan",
    "软件测量分析计划": "product.measurement-analysis-plan",
    "软件测量与分析计划": "product.measurement-analysis-plan",
    "软件需求规格说明": "product.software-requirements-specification",
    "软件设计说明": "product.software-design",
    "软件测试计划": "product.software-test-plan",
    "软件单元测试计划": "product.software-test-plan",
    "软件测试说明": "product.software-test-description",
    "软件测试报告": "product.software-test-report",
    "软件单元测试报告": "product.software-test-report",
    "软件用户手册": "product.software-user-manual",
}

PRODUCT_ALIASES = (
    ("接口设计说明", "product.software-design"),
    ("单元测试计划", "product.software-test-plan"),
    ("单元测试报告", "product.software-test-report"),
    ("测试说明", "product.software-test-description"),
    ("测试报告", "product.software-test-report"),
    ("测试计划", "product.software-test-plan"),
)


def normalize_product_name(name: str, project_name: str | None = None) -> str:
    compact = re.sub(r"\s+", "", name)
    if project_name:
        compact_project = re.sub(r"\s+", "", project_name)
        if compact.startswith(compact_project):
            compact = compact[len(compact_project):]
    return compact


def choose_mapping(candidates: Iterable[MappingCandidate], subject: str) -> str:
    return choose_mapping_candidate(candidates, subject).stable_key


def choose_mapping_candidate(
    candidates: Iterable[MappingCandidate], subject: str
) -> MappingCandidate:
    values = list(candidates)
    if not values:
        raise MappingError("IMPORT_UNKNOWN_CHECKLIST_MAPPING", subject)
    highest = max(candidate.priority for candidate in values)
    winners = {candidate.stable_key for candidate in values if candidate.priority == highest}
    if len(winners) != 1:
        raise MappingError("IMPORT_AMBIGUOUS_CHECKLIST_MAPPING", subject)
    return next(candidate for candidate in values if candidate.stable_key in winners and candidate.priority == highest)


def builtin_product_candidates(name: str, project_name: str) -> list[MappingCandidate]:
    normalized = normalize_product_name(name, project_name)
    compact_name = re.sub(r"\s+", "", name)
    candidates: list[MappingCandidate] = []
    exact_matches = [
        (token, stable_key) for token, stable_key in PRODUCT_EXACT.items()
        if normalized == token or normalized.endswith(token) or compact_name.endswith(token)
    ]
    if exact_matches:
        longest = max(len(token) for token, _ in exact_matches)
        candidates.extend(
            MappingCandidate(stable_key, 200, "builtin_exact")
            for token, stable_key in exact_matches if len(token) == longest
        )
    for token, stable_key in PRODUCT_ALIASES:
        if token in normalized:
            candidates.append(MappingCandidate(stable_key, 100, "builtin_alias"))
    return candidates


def process_template(process_code: str) -> str:
    try:
        return PROCESS_TEMPLATE_BY_CODE[process_code]
    except KeyError as exc:
        raise MappingError("IMPORT_UNKNOWN_CHECKLIST_MAPPING", process_code) from exc
