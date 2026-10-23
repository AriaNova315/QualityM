from __future__ import annotations

from pathlib import PurePosixPath

from openpyxl.packaging.relationship import Relationship
from openpyxl.workbook.external_link.external import (
    ExternalBook, ExternalCell, ExternalRow, ExternalSheetData, ExternalSheetDataSet,
    ExternalLink,
    ExternalSheetNames,
)


def _validated_relative_target(target: str) -> str:
    path = PurePosixPath(target)
    if not target or path.is_absolute() or ".." in path.parts:
        raise ValueError("External workbook target must be a safe relative path")
    if path.name != target:
        raise ValueError("Step 5 external workbooks must be in the same directory")
    if path.suffix.lower() != ".xlsx":
        raise ValueError("External workbook target must be an .xlsx file")
    return target


def add_external_workbook_link(
    workbook,
    *,
    target: str,
    sheet_names: tuple[str, ...],
    cached_cells: dict[str, int] | None = None,
) -> int:
    """Register one OOXML external-workbook relationship.

    Business builders call this adapter instead of manipulating ZIP/XML parts.
    The same-directory target intentionally remains relative so moving the whole
    export directory preserves the link.
    """

    if not sheet_names or any(not name for name in sheet_names):
        raise ValueError("At least one non-empty external sheet name is required")
    relative_target = _validated_relative_target(target)
    external_book = ExternalBook(
        sheetNames=ExternalSheetNames(sheetName=list(sheet_names)),
        id="rId1",
    )
    if cached_cells is not None:
        if len(sheet_names) != 1:
            raise ValueError("Cached external links require exactly one sheet")
        external_book.sheetDataSet = ExternalSheetDataSet(sheetData=[
            ExternalSheetData(sheetId=0, row=[
                ExternalRow(r=int(address[1:]), cell=[ExternalCell(r=address, t="n", v=str(value))])
                for address, value in cached_cells.items()
            ])
        ])
    link = ExternalLink(externalBook=external_book)
    link.file_link = Relationship(
        Id="rId1",
        type="externalLinkPath",
        Target=relative_target,
        TargetMode="External",
    )
    workbook._external_links.append(link)
    return len(workbook._external_links)
