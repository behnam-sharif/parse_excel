# === image_cleaner.py ===
from __future__ import annotations

from typing import Any, List, Tuple

def run_macro_cleaning(input_path: str) -> Tuple[str, List[dict[str, Any]]]:
    """
    Opens the target Excel file, injects a VBA macro to save all shapes info,
    deletes all shapes, and saves a cleaned version.
    """
    from pathlib import Path
    import xlwings as xw
    import os

    # Use absolute paths
    input_path = Path(input_path).resolve()
    cleaned_path = Path("data/cleaned/cleaned.xlsx").resolve()
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)

    # Delete existing cleaned.xlsx if it exists and is not locked
    if cleaned_path.exists():
        try:
            cleaned_path.unlink()
        except PermissionError:
            try:
                if os.name == 'nt':  # Windows
                    os.system('taskkill /F /IM EXCEL.EXE 2>nul')
                    import time
                    time.sleep(1)
                    cleaned_path.unlink()
            except:
                pass

    # Read VBA code -- look relative to this file, then in cwd
    _this_dir = Path(__file__).resolve().parent.parent
    _candidates = [
        _this_dir / "remove_shapes.vba",
        Path("remove_shapes.vba").resolve(),
    ]
    vba_path = None
    for _cand in _candidates:
        if _cand.exists():
            vba_path = _cand
            break
    if vba_path is None:
        raise FileNotFoundError(
            f"remove_shapes.vba not found. Searched: {[str(c) for c in _candidates]}"
        )

    with open(vba_path, "r", encoding="utf-8") as f:
        VBA_CODE = f.read()

    app = None
    wb = None
    try:
        # Create new Excel application
        app = xw.App(visible=False, add_book=False)
        app.display_alerts = False
        app.screen_updating = False

        # Open the workbook
        wb = app.books.open(str(input_path), read_only=False)

        # Enumerate shapes BEFORE deletion (for shapes manifest)
        shapes_data = []
        try:
            for sheet in wb.sheets:
                for shape in sheet.shapes:
                    entry = {
                        "sheet_name": sheet.name,
                        "shape_name": shape.name,
                        "shape_type": str(shape.type),
                        "text": getattr(shape, 'text', None),
                        "top": shape.top,
                        "left": shape.left,
                        "width": shape.width,
                        "height": shape.height,
                    }
                    try: entry["top_left_cell"] = str(shape.top_left_cell)
                    except: pass
                    try: entry["hyperlink"] = shape.hyperlink
                    except: pass
                    try: entry["rotation"] = shape.rotation
                    except: pass
                    try: entry["alt_text"] = shape.alt_text
                    except: pass
                    shapes_data.append(entry)
        except Exception:
            pass  # Non-fatal: shape enumeration may fail on some workbooks

        # Inject VBA macro
        wb.api.VBProject.VBComponents.Add(1).CodeModule.AddFromString(VBA_CODE)

        # Run the macro
        macro = wb.macro("SaveAndRemoveAllShapes")
        macro()

        # Optionally, remove the macro module (for a clean file)
        for vbcomp in wb.api.VBProject.VBComponents:
            if vbcomp.Name == "Module1":
                wb.api.VBProject.VBComponents.Remove(vbcomp)
                break

        # Save to a temporary file first to avoid lock issues
        import tempfile
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx', dir=str(cleaned_path.parent))
        temp_path = temp_file.name
        temp_file.close()

        # Save to temp file
        wb.save(str(temp_path))
        wb.close()
        wb = None

        # Close Excel app before moving file
        app.quit()
        app = None

        # Now move temp file to final location
        import shutil
        if cleaned_path.exists():
            cleaned_path.unlink()
        shutil.move(temp_path, str(cleaned_path))

        return str(cleaned_path), shapes_data
    except Exception as e:
        if wb:
            try:
                wb.close(save_changes=False)
            except:
                pass
        raise e
    finally:
        if wb:
            try:
                wb.close(save_changes=False)
            except:
                pass
        if app:
            try:
                app.quit()
            except:
                pass
