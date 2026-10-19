from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


Severity = Literal["blocker", "warning"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: Severity
    scope: str
    location: dict[str, Any]
    message: str
    resolution: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

