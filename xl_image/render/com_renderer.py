"""
Excel COM renderer -- visible-minimized Excel, CopyPicture -> export PNG.

Rendering path:
  1. Open Excel visible but minimized (ScreenUpdating=True so cells render).
  2. Activate the source sheet and select the target Range.
  3. Range.CopyPicture(Appearance=xlScreen, Format=xlBitmap).
  4. Paste onto a dedicated temp worksheet as a Picture shape.
  5. Size the picture to match the original range dimensions.
  6. Create an embedded ChartObject sized identically, paste the picture
     into it, resize to fill, and Chart.Export as PNG.
  7. Delete the temp sheet; close workbook without saving.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..config import XlImageConfig

logger = logging.getLogger(__name__)

_XL_SCREEN = 1
_XL_BITMAP = 2
_XL_MINIMIZED = -4140
_TMP_SHEET = "_xl_img_export_"


class COMRenderer:
    """
    Context manager: opens Excel once (minimized) and renders ranges to PNG.

    Usage:
        with COMRenderer(workbook_path, config) as renderer:
            if renderer.available:
                ok = renderer.render_bbox(sheet_name, r0, c0, r1, c1, output_path)
    """

    def __init__(self, workbook_path: str, config: Optional[XlImageConfig] = None):
        self.workbook_path = str(Path(workbook_path).resolve())
        self.config = config or XlImageConfig()
        self._app = None
        self._wb = None
        self.available: bool = False

    def __enter__(self) -> "COMRenderer":
        try:
            import xlwings as xw
            self._app = xw.App(visible=True)
            self._app.api.DisplayAlerts = False
            try:
                self._app.api.WindowState = _XL_MINIMIZED
            except Exception:
                pass
            self._app.api.ScreenUpdating = True
            self._wb = self._app.books.open(self.workbook_path)
            self.available = True
            logger.info("COM renderer: Excel opened (minimized) for %s", self.workbook_path)
        except Exception as e:
            logger.warning("COM renderer unavailable: %s", e)
            self._cleanup()
        return self

    def __exit__(self, *args) -> None:
        self._cleanup()

    def _cleanup(self) -> None:
        try:
            if self._app is not None:
                self._app.api.DisplayAlerts = False
        except Exception:
            pass
        try:
            if self._wb is not None:
                self._wb.close()
        except Exception:
            pass
        try:
            if self._app is not None:
                self._app.quit()
        except Exception:
            pass
        self._wb = None
        self._app = None

    def render_bbox(
        self,
        sheet_name: str,
        r0: int, c0: int, r1: int, c1: int,
        output_path: str,
        nrows_sheet: Optional[int] = None,
        ncols_sheet: Optional[int] = None,
        min_chart_width_pt: Optional[float] = None,
        min_chart_height_pt: Optional[float] = None,
    ) -> bool:
        """Render a bbox (0-indexed inclusive) to PNG via Excel COM."""
        if not self.available:
            return False
        try:
            return self._render(
                sheet_name, r0, c0, r1, c1, output_path, nrows_sheet, ncols_sheet,
                min_chart_width_pt, min_chart_height_pt,
            )
        except Exception as e:
            logger.warning(
                "COM render failed [%s r%d:c%d-r%d:c%d]: %s",
                sheet_name, r0, c0, r1, c1, e,
            )
            raise

    def _render(
        self,
        sheet_name: str,
        r0: int, c0: int, r1: int, c1: int,
        output_path: str,
        nrows_sheet: Optional[int],
        ncols_sheet: Optional[int],
        min_chart_width_pt: Optional[float] = None,
        min_chart_height_pt: Optional[float] = None,
    ) -> bool:
        if nrows_sheet is not None:
            r1 = min(r1, nrows_sheet - 1)
        if ncols_sheet is not None:
            c1 = min(c1, ncols_sheet - 1)

        ws = self._wb.sheets[sheet_name]
        ws.activate()

        rng = ws.range((r0 + 1, c0 + 1), (r1 + 1, c1 + 1))
        rng_w = float(rng.api.Width)
        rng_h = float(rng.api.Height)

        if min_chart_width_pt:
            rng_w = max(rng_w, min_chart_width_pt)
        if min_chart_height_pt:
            rng_h = max(rng_h, min_chart_height_pt)

        rng.api.CopyPicture(Appearance=_XL_SCREEN, Format=_XL_BITMAP)

        self._remove_tmp_sheet()
        tmp_ws = self._wb.sheets.add(name=_TMP_SHEET)
        tmp_ws.activate()

        tmp_ws.api.Paste()

        pic = tmp_ws.api.Shapes(tmp_ws.api.Shapes.Count)
        pic.Left = 0
        pic.Top = 0
        pic.LockAspectRatio = False
        pic.Width = rng_w
        pic.Height = rng_h

        chart_obj = tmp_ws.api.ChartObjects().Add(0, 0, rng_w, rng_h)
        chart = chart_obj.Chart
        chart.HasLegend = False
        chart.HasTitle = False
        chart.ChartArea.Format.Line.Visible = False
        chart.ChartArea.Format.Fill.Visible = False
        try:
            chart.PlotArea.Format.Fill.Visible = False
            chart.PlotArea.Format.Line.Visible = False
        except Exception:
            pass

        pic.Copy()
        chart.Paste()

        if chart.Shapes.Count > 0:
            inner = chart.Shapes(chart.Shapes.Count)
            inner.Left = 0
            inner.Top = 0
            inner.LockAspectRatio = False
            inner.Width = rng_w
            inner.Height = rng_h

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        chart.Export(str(Path(output_path).resolve()), "PNG")

        chart_obj.Delete()
        self._remove_tmp_sheet()

        return Path(output_path).exists()

    def _remove_tmp_sheet(self) -> None:
        try:
            self._app.api.DisplayAlerts = False
            for s in self._wb.sheets:
                if s.name == _TMP_SHEET:
                    s.delete()
                    break
        except Exception:
            pass
