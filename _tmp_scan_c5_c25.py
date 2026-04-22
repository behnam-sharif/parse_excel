import openpyxl
from openpyxl.utils import get_column_letter

path = r"c:\Users\behnamsharif\Documents\xlgen_v3\data\output\streamlit_run_enco\init_output\cleaned_workbook.xlsx"
wb = openpyxl.load_workbook(path, data_only=False)
ws = None
sn = None
for n in wb.sheetnames:
    if n.strip() == "Results":
        ws = wb[n]
        sn = n
        break
if ws is None:
    raise SystemExit("Results sheet not found")
print("sheet:", repr(sn))


def cell_summary(r, c):
    cell = ws.cell(row=r, column=c)
    v = cell.value
    f = cell.data_type == "f"
    s = str(v).strip() if v is not None else ""
    has = bool(s) or f
    preview = (s[:40] + "...") if len(s) > 40 else s
    if f and not s:
        preview = "=formula"
    return has, preview


for label, rlo, rhi in [("rows3-12", 3, 12), ("rows22-32", 22, 32)]:
    print(f"\n=== {label} cols B-H ===")
    for r in range(rlo, rhi + 1):
        bits = []
        for c in range(2, 9):
            h, p = cell_summary(r, c)
            if h:
                bits.append(f"{get_column_letter(c)}{r}={p!r}")
        if bits:
            print("R", r, ":", " | ".join(bits))

wb.close()
