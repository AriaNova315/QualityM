from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


SUPPORTED_YEARS = frozenset({2024, 2025, 2026})

HOLIDAYS = frozenset({
    date(2024, 1, 1),
    *[date(2024, 2, day) for day in range(10, 18)],
    date(2024, 4, 4), date(2024, 4, 5), date(2024, 4, 6),
    date(2024, 5, 1), date(2024, 5, 2), date(2024, 5, 3),
    date(2024, 6, 10),
    date(2024, 9, 15), date(2024, 9, 16), date(2024, 9, 17),
    *[date(2024, 10, day) for day in range(1, 8)],
    date(2025, 1, 1),
    date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30), date(2025, 1, 31),
    date(2025, 2, 1), date(2025, 2, 2), date(2025, 2, 3), date(2025, 2, 4),
    date(2025, 4, 4), date(2025, 4, 5), date(2025, 4, 6),
    *[date(2025, 5, day) for day in range(1, 6)],
    date(2025, 5, 31), date(2025, 6, 1), date(2025, 6, 2),
    *[date(2025, 10, day) for day in range(1, 9)],
    date(2026, 1, 1), date(2026, 1, 2),
    *[date(2026, 2, day) for day in range(17, 25)],
    date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6),
    *[date(2026, 5, day) for day in range(1, 6)],
    date(2026, 6, 19), date(2026, 6, 20), date(2026, 6, 21),
    *[date(2026, 10, day) for day in range(1, 8)],
})

MAKEUP_WORKDAYS = frozenset({
    date(2024, 2, 4), date(2024, 2, 18), date(2024, 4, 7),
    date(2024, 4, 28), date(2024, 5, 11), date(2024, 9, 14),
    date(2024, 9, 29), date(2024, 10, 12),
    date(2025, 1, 26), date(2025, 2, 8), date(2025, 4, 27),
    date(2025, 9, 28), date(2025, 10, 11),
    date(2026, 1, 4), date(2026, 2, 15), date(2026, 2, 28),
    date(2026, 4, 12), date(2026, 5, 9), date(2026, 9, 27),
    date(2026, 10, 10),
})


class CalendarCoverageError(ValueError):
    pass


def require_calendar_coverage(value: date) -> None:
    if value.year not in SUPPORTED_YEARS:
        raise CalendarCoverageError(f"法定工作日日历不覆盖 {value.year} 年")


def is_workday(value: date) -> bool:
    require_calendar_coverage(value)
    if value in MAKEUP_WORKDAYS:
        return True
    if value in HOLIDAYS:
        return False
    return value.weekday() < 5


def next_workday(value: date) -> date:
    candidate = value + timedelta(days=1)
    while not is_workday(candidate):
        candidate += timedelta(days=1)
    return candidate


@dataclass(frozen=True, slots=True)
class CloseDateResult:
    planned_close_date: date
    compressed: bool


def planned_close_date(
    audit_kind: str,
    audit_date: date,
    tracking_node_date: date | None,
    phase_end_date: date,
) -> CloseDateResult:
    suggested = next_workday(audit_date) if audit_kind == "product" else audit_date
    boundary = tracking_node_date if tracking_node_date is not None else phase_end_date
    capped = min(suggested, boundary, phase_end_date)
    return CloseDateResult(capped, capped != suggested)

