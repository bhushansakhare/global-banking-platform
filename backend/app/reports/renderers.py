"""Render ReportData to XLSX, CSV and PDF."""
from __future__ import annotations

import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.reports.builders import ReportData

PRIMARY = "2B296E"
DEMO_NOTICE = "CONTAINS DEMO DATA — synthetic records, not real bank data"


def to_xlsx(data: ReportData) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for sec in data.sections or []:
        ws = wb.create_sheet(sec.name[:31].replace("/", "-"))
        ws.append([data.title])
        ws["A1"].font = Font(bold=True, size=14, color=PRIMARY)
        ws.append([data.subtitle])
        if data.contains_demo:
            ws.append([DEMO_NOTICE])
            ws.cell(ws.max_row, 1).font = Font(bold=True, color="B45309")
        ws.append([])
        ws.append(sec.columns)
        hdr = ws.max_row
        for i in range(1, len(sec.columns) + 1):
            c = ws.cell(hdr, i)
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor=PRIMARY)
            c.alignment = Alignment(vertical="center")
        for r in sec.rows:
            ws.append(r)
        for i, col in enumerate(sec.columns, start=1):
            width = max([len(str(col))] + [len(str(r[i - 1])) for r in sec.rows[:500] if i - 1 < len(r)])
            ws.column_dimensions[get_column_letter(i)].width = min(60, max(10, width + 2))
        ws.freeze_panes = ws.cell(hdr + 1, 1)
    if not wb.sheetnames:
        wb.create_sheet("Report").append(["No data"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_csv(data: ReportData) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([data.title])
    w.writerow([data.subtitle])
    if data.contains_demo:
        w.writerow([DEMO_NOTICE])
    for sec in data.sections:
        w.writerow([])
        w.writerow([sec.name])
        w.writerow(sec.columns)
        w.writerows(sec.rows)
    return buf.getvalue().encode("utf-8-sig")


def to_pdf(data: ReportData) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm, title=data.title)
    styles = getSampleStyleSheet()
    cell = styles["BodyText"].clone("cell", fontSize=7, leading=8.5)
    story = [Paragraph(data.title, styles["Title"]), Paragraph(data.subtitle, styles["Normal"])]
    if data.contains_demo:
        story.append(Paragraph(f"<b><font color='#B45309'>{DEMO_NOTICE}</font></b>", styles["Normal"]))
    for sec in data.sections:
        story += [Spacer(1, 6 * mm), Paragraph(sec.name, styles["Heading2"])]
        if not sec.rows:
            story.append(Paragraph("No records for this period.", styles["Italic"]))
            continue
        rows = sec.rows[:2000]
        table = [[Paragraph(f"<b>{c}</b>", cell) for c in sec.columns]] + [
            [Paragraph("" if v is None else (f"{v:,.2f}" if isinstance(v, float) else str(v))[:120], cell) for v in r]
            for r in rows]
        width = doc.width / max(1, len(sec.columns))
        t = Table(table, repeatRows=1, colWidths=[width] * len(sec.columns))
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + PRIMARY)),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F5FA")]),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9DBE7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(t)
        if len(sec.rows) > len(rows):
            story.append(Paragraph(f"… {len(sec.rows) - len(rows)} more rows — export to Excel for the full list.",
                                   styles["Italic"]))
    doc.build(story)
    return buf.getvalue()


RENDERERS = {"xlsx": to_xlsx, "csv": to_csv, "pdf": to_pdf}
MIME = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv",
        "pdf": "application/pdf"}
