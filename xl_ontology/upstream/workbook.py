"""Upstream loader: workbook (wraps core.workbook_accessor.WorkbookAccessor).

The WorkbookView exposes a small surface tailored to xl_ontology's
needs (cell value + format + formula text + sheet enumeration). The
underlying WorkbookAccessor handles the actual openpyxl plumbing
including the read_only=False values workbook + lazy formula cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from core.workbook_accessor import WorkbookAccessor


@dataclass
class CellSnapshot:
    """Plain accessor — what xl_ontology tools see for a single cell."""

    sheet: str
    row: int  # 1-indexed (Excel-style)
    col: int  # 1-indexed
    value: Any
    is_formula: bool
    formula: Optional[str] = None


@dataclass
class CellFormatSnapshot:
    bold: bool = False
    italic: bool = False
    fill_color: Optional[str] = None
    border_top: bool = False
    border_bottom: bool = False
    border_left: bool = False
    border_right: bool = False
    number_format: str = "General"
    font_size: Optional[float] = None
    is_merged: bool = False
    has_data_validation: bool = False


class WorkbookView:
    """Read-only wrapper over core.workbook_accessor.WorkbookAccessor."""

    def __init__(self, accessor: WorkbookAccessor) -> None:
        self._acc = accessor

    @classmethod
    def open(cls, workbook_path: Path | str) -> "WorkbookView":
        return cls(WorkbookAccessor(str(workbook_path)))

    @property
    def workbook_path(self) -> str:
        return self._acc.workbook_path

    def close(self) -> None:
        self._acc.close()

    def __enter__(self) -> "WorkbookView":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # Sheet enumeration — touch the accessor's lazy loader and read
    # the workbook's `sheetnames`. The accessor exposes the values
    # workbook via the private `_get_wb_values` method (single-leading
    # underscore = "module-private" not "do not touch").
    def sheet_names(self) -> List[str]:
        wb = self._acc._get_wb_values()  # noqa: SLF001 — module-private API
        return list(wb.sheetnames) if wb else []

    # Cell access — WorkbookView's row/col are 1-indexed (openpyxl
    # convention). The underlying accessor's is_formula/get_formula
    # take 0-indexed coords (it builds the cache via iter_rows
    # enumerate). Translate at the boundary.
    def get_cell(self, sheet: str, row: int, col: int) -> CellSnapshot:
        ws = self._acc.get_worksheet(sheet)
        if ws is None:
            return CellSnapshot(
                sheet=sheet, row=row, col=col, value=None, is_formula=False
            )
        cell = ws.cell(row=row, column=col)
        r0, c0 = row - 1, col - 1
        is_formula = self._acc.is_formula(sheet, r0, c0)
        formula = self._acc.get_formula(sheet, r0, c0) if is_formula else None
        return CellSnapshot(
            sheet=sheet,
            row=row,
            col=col,
            value=cell.value,
            is_formula=is_formula,
            formula=formula,
        )

    def get_cells_in_rect(
        self, sheet: str, r1: int, c1: int, r2: int, c2: int
    ) -> List[CellSnapshot]:
        out: List[CellSnapshot] = []
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                out.append(self.get_cell(sheet, r, c))
        return out

    def get_cell_format(self, sheet: str, row: int, col: int) -> CellFormatSnapshot:
        ws = self._acc.get_worksheet(sheet)
        if ws is None:
            return CellFormatSnapshot()
        cell = ws.cell(row=row, column=col)
        font = cell.font
        fill = cell.fill
        border = cell.border
        # Detect merged-cell membership by walking the merged ranges.
        is_merged = False
        try:
            for mr in ws.merged_cells.ranges:
                if mr.min_row <= row <= mr.max_row and mr.min_col <= col <= mr.max_col:
                    is_merged = True
                    break
        except Exception:  # pragma: no cover
            pass
        # Data validation: walk ws.data_validations.
        has_dv = False
        try:
            for dv in ws.data_validations.dataValidation:
                for sqref in dv.sqref.ranges if hasattr(dv.sqref, "ranges") else [dv.sqref]:
                    if (
                        sqref.min_row <= row <= sqref.max_row
                        and sqref.min_col <= col <= sqref.max_col
                    ):
                        has_dv = True
                        break
                if has_dv:
                    break
        except Exception:  # pragma: no cover
            pass

        def _color(c: Any) -> Optional[str]:
            try:
                return c.value if c and c.value not in ("00000000", "FFFFFFFF", None) else None
            except Exception:  # pragma: no cover
                return None

        return CellFormatSnapshot(
            bold=bool(font and font.bold),
            italic=bool(font and font.italic),
            fill_color=_color(getattr(fill, "fgColor", None)) if fill else None,
            border_top=bool(border and border.top and border.top.style),
            border_bottom=bool(border and border.bottom and border.bottom.style),
            border_left=bool(border and border.left and border.left.style),
            border_right=bool(border and border.right and border.right.style),
            number_format=cell.number_format or "General",
            font_size=float(font.size) if font and font.size else None,
            is_merged=is_merged,
            has_data_validation=has_dv,
        )

    def is_formula(self, sheet: str, row: int, col: int) -> bool:
        # row/col are 1-indexed (openpyxl); accessor wants 0-indexed.
        return self._acc.is_formula(sheet, row - 1, col - 1)

    def get_formula(self, sheet: str, row: int, col: int) -> Optional[str]:
        return self._acc.get_formula(sheet, row - 1, col - 1)
