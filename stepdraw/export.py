"""Exporters: SVG, DXF and PDF (PDF/preview share the QPainter renderer)."""
from __future__ import annotations

import math
from html import escape
from pathlib import Path

from .drawing import Polyline, Sheet, Solid, Text

STYLE = {  # layer: (stroke width mm, dash pattern mm or None, colour)
    "VISIBLE": (0.5, None, "#000000"),
    "HIDDEN": (0.25, (3.0, 1.5), "#000000"),
    "CENTER": (0.18, (8.0, 1.5, 1.5, 1.5), "#000000"),
    "DIM": (0.18, None, "#000000"),
    "BORDER": (0.7, None, "#000000"),
    "TITLE": (0.35, None, "#000000"),
}


# ----------------------------------------------------------------------------- SVG
def to_svg(sheet: Sheet) -> str:
    W, H = sheet.width, sheet.height
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        '<g fill="none" stroke-linecap="round" stroke-linejoin="round">',
    ]

    def fy(y):
        return H - y

    for e in sheet.entities:
        if isinstance(e, Polyline):
            w, dash, col = STYLE[e.layer]
            pts = " ".join(f"{x:.3f},{fy(y):.3f}" for x, y in e.pts)
            tag = "polygon" if e.closed else "polyline"
            da = f' stroke-dasharray="{",".join(str(d) for d in dash)}"' if dash else ""
            out.append(f'<{tag} points="{pts}" stroke="{col}" stroke-width="{w}"{da}/>')
        elif isinstance(e, Solid):
            pts = " ".join(f"{x:.3f},{fy(y):.3f}" for x, y in e.pts)
            out.append(f'<polygon points="{pts}" fill="#000000" stroke="none"/>')
    out.append("</g>")
    out.append('<g font-family="Arial, Helvetica, sans-serif" fill="#000000">')
    for e in sheet.entities:
        if isinstance(e, Text):
            anchor = {"left": "start", "center": "middle", "right": "end"}[e.halign]
            base = {"bottom": "auto", "middle": "central", "top": "hanging"}[e.valign]
            x, y = e.x, fy(e.y)
            rot = f' transform="rotate({-e.angle:.3f} {x:.3f} {y:.3f})"' if e.angle else ""
            out.append(
                f'<text x="{x:.3f}" y="{y:.3f}" font-size="{e.height * 1.4:.3f}" text-anchor="{anchor}" '
                f'dominant-baseline="{base}"{rot}>{escape(e.text)}</text>'
            )
    out.append("</g></svg>")
    return "\n".join(out)


def save_svg(sheet: Sheet, path: str | Path):
    Path(path).write_text(to_svg(sheet), encoding="utf-8")


# ----------------------------------------------------------------------------- DXF
def save_dxf(sheet: Sheet, path: str | Path):
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # millimetres (sheet space)
    doc.header["$LTSCALE"] = 1.0
    lw = {"VISIBLE": 50, "HIDDEN": 25, "CENTER": 18, "DIM": 18, "BORDER": 70, "TITLE": 35}
    lt = {"HIDDEN": "HIDDEN", "CENTER": "CENTER"}
    for name in STYLE:
        doc.layers.add(name, color=7, linetype=lt.get(name, "Continuous"), lineweight=lw[name])
    msp = doc.modelspace()
    align = {
        ("left", "bottom"): TextEntityAlignment.BOTTOM_LEFT,
        ("center", "bottom"): TextEntityAlignment.BOTTOM_CENTER,
        ("right", "bottom"): TextEntityAlignment.BOTTOM_RIGHT,
        ("left", "middle"): TextEntityAlignment.MIDDLE_LEFT,
        ("center", "middle"): TextEntityAlignment.MIDDLE_CENTER,
        ("right", "middle"): TextEntityAlignment.MIDDLE_RIGHT,
        ("left", "top"): TextEntityAlignment.TOP_LEFT,
        ("center", "top"): TextEntityAlignment.TOP_CENTER,
        ("right", "top"): TextEntityAlignment.TOP_RIGHT,
    }
    for e in sheet.entities:
        if isinstance(e, Polyline):
            attribs = {"layer": e.layer}
            if e.layer == "HIDDEN":
                attribs["ltscale"] = 0.4
            elif e.layer == "CENTER":
                attribs["ltscale"] = 0.3
            if len(e.pts) == 2 and not e.closed:
                msp.add_line(e.pts[0], e.pts[1], dxfattribs=attribs)
            else:
                msp.add_lwpolyline(e.pts, close=e.closed, dxfattribs=attribs)
        elif isinstance(e, Solid):
            hatch = msp.add_hatch(color=7, dxfattribs={"layer": "DIM"})
            hatch.paths.add_polyline_path(e.pts, is_closed=True)
        elif isinstance(e, Text):
            t = msp.add_text(e.text, height=e.height, rotation=e.angle, dxfattribs={"layer": e.layer, "style": "OpenSans"})
            t.set_placement((e.x, e.y), align=align[(e.halign, e.valign)])
    doc.saveas(str(path))


# ----------------------------------------------------------------------------- QPainter (preview + PDF)
def paint(sheet: Sheet, painter, px_per_mm: float):
    """Draw `sheet` with a QPainter whose device units are pixels; origin top-left."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPolygonF

    H = sheet.height
    k = px_per_mm

    def P(x, y):
        return QPointF(x * k, (H - y) * k)

    painter.setRenderHint(painter.RenderHint.Antialiasing, True)
    painter.fillRect(0, 0, int(sheet.width * k) + 1, int(H * k) + 1, QColor("white"))
    pens = {}
    for name, (w, dash, col) in STYLE.items():
        pen = QPen(QColor(col))
        pen.setWidthF(max(w * k, 0.6))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        if dash:
            pen.setDashPattern([d / w for d in dash])  # Qt dash units are pen widths
        pens[name] = pen

    painter.setBrush(Qt.NoBrush)
    for e in sheet.entities:
        if isinstance(e, Polyline):
            painter.setPen(pens[e.layer])
            poly = QPolygonF([P(x, y) for x, y in e.pts])
            if e.closed:
                painter.drawPolygon(poly)
            else:
                painter.drawPolyline(poly)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(QColor("black")))
    for e in sheet.entities:
        if isinstance(e, Solid):
            painter.drawPolygon(QPolygonF([P(x, y) for x, y in e.pts]))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor("black")))
    for e in sheet.entities:
        if isinstance(e, Text):
            font = QFont("Arial")
            font.setPixelSize(max(1, int(round(e.height * 1.4 * k))))
            painter.setFont(font)
            fm = painter.fontMetrics()
            w = fm.horizontalAdvance(e.text)
            dx = {"left": 0, "center": -w / 2, "right": -w}[e.halign]
            dy = {"bottom": -fm.descent(), "middle": (fm.ascent() - fm.descent()) / 2, "top": fm.ascent()}[e.valign]
            painter.save()
            painter.translate(P(e.x, e.y))
            if e.angle:
                painter.rotate(-e.angle)
            painter.drawText(QPointF(dx, dy), e.text)
            painter.restore()


def save_pdf(sheet: Sheet, path: str | Path):
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter

    writer = QPdfWriter(str(path))
    writer.setResolution(600)
    size = QPageSize(QSizeF(sheet.width, sheet.height), QPageSize.Millimeter, "Drawing", QPageSize.ExactMatch)
    writer.setPageLayout(QPageLayout(size, QPageLayout.Portrait, QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter))
    writer.setTitle("Drawing")
    painter = QPainter(writer)
    try:
        paint(sheet, painter, 600 / 25.4)
    finally:
        painter.end()


def save_png(sheet: Sheet, path: str | Path, dpi: int = 150):
    from PySide6.QtGui import QImage, QPainter

    k = dpi / 25.4
    img = QImage(int(math.ceil(sheet.width * k)), int(math.ceil(sheet.height * k)), QImage.Format_RGB32)
    p = QPainter(img)
    try:
        paint(sheet, p, k)
    finally:
        p.end()
    img.save(str(path))


def export(sheet: Sheet, path: str | Path):
    ext = Path(path).suffix.lower()
    {".svg": save_svg, ".dxf": save_dxf, ".pdf": save_pdf, ".png": save_png}[ext](sheet, path)
