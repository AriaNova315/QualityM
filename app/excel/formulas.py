from __future__ import annotations

from dataclasses import dataclass

from openpyxl.utils.cell import quote_sheetname


@dataclass(frozen=True, slots=True)
class Formula:
    """A formula created by QualityM rather than supplied as business text."""

    expression: str

    def __post_init__(self) -> None:
        if not self.expression.startswith("="):
            raise ValueError("Formula expressions must start with '='")


def internal_reference(sheet_name: str, cell_reference: str) -> str:
    return f"{quote_sheetname(sheet_name)}!{cell_reference}"


def external_reference(link_index: int, sheet_name: str, cell_reference: str) -> str:
    if link_index < 1:
        raise ValueError("External workbook link indexes start at 1")
    external_sheet = quote_sheetname(f"[{link_index}]{sheet_name}")
    return f"{external_sheet}!{cell_reference}"


def write_formula(cell, formula: Formula) -> None:
    cell.value = formula.expression


def write_literal_cell(cell, value: object) -> None:
    """Write untrusted business text without allowing formula interpretation."""

    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        cell.value = f"'{value}"
    else:
        cell.value = value
