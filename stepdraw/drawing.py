"""Sheet layout, dimensioning and title block.

Produces a `Sheet`: a flat list of entities in sheet millimetres (origin bottom-left, y up)
that every exporter (PDF/SVG/DXF/preview) renders the same way.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field

from .geometry import View2D

Pt = tuple[float, float]

SHEETS = {  # name: (width, height) in mm, landscape
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "ANSI A": (279.4, 215.9),
    "ANSI B": (431.8, 279.4),
    "ANSI C": (558.8, 431.8),
    "ANSI D": (863.6, 558.8),
}

SCALES = [  # (factor, label) largest first
    (10.0, "10:1"), (5.0, "5:1"), (2.0, "2:1"), (1.0, "1:1"), (0.5, "1:2"), (0.2, "1:5"),
    (0.1, "1:10"), (0.05, "1:20"), (0.02, "1:50"), (0.01, "1:100"), (0.005, "1:200"),
    (0.002, "1:500"), (0.001, "1:1000"),
]

TEXT_H = 3.5  # ISO 129 standard dimension text height
ARROW_L = 3.0
ARROW_W = 1.0
EXT_GAP = 1.5
EXT_OVER = 2.0
DIM_STEP = 8.0  # distance between stacked dimension lines
FIRST_DIM = 10.0  # distance of first dimension line from the part


# ----------------------------------------------------------------------------- entities
@dataclass
class Polyline:
    pts: list[Pt]
    layer: str  # VISIBLE, HIDDEN, CENTER, DIM, BORDER, TITLE
    closed: bool = False


@dataclass
class Text:
    x: float
    y: float
    text: str
    height: float = TEXT_H
    layer: str = "DIM"
    halign: str = "center"  # left | center | right
    valign: str = "bottom"  # bottom | middle | top
    angle: float = 0.0  # degrees, counter-clockwise


@dataclass
class Solid:  # filled polygon (arrowheads)
    pts: list[Pt]
    layer: str = "DIM"


@dataclass
class Sheet:
    width: float
    height: float
    entities: list = field(default_factory=list)
    scale_label: str = "1:1"
    notes: list[str] = field(default_factory=list)

    def add(self, e):
        self.entities.append(e)


@dataclass
class Options:
    sheet: str = "A3"
    projection: str = "third"  # third (US/ANSI) | first (ISO/EU)
    views: tuple[str, ...] = ("front", "top", "right", "iso")
    hidden_lines: bool = True
    overall_dims: bool = True
    hole_dims: bool = True
    hole_positions: bool = True
    radius_dims: bool = True
    center_marks: bool = True
    units: str = "mm"  # mm | in
    decimals: int = 1
    scale: str = "auto"  # auto or a label from SCALES
    title: str = ""
    drawing_no: str = ""
    material: str = ""
    drawn_by: str = ""
    company: str = ""
    revision: str = "A"


# ----------------------------------------------------------------------------- formatting
def fmt(value_mm: float, opt: Options) -> str:
    v = value_mm / 25.4 if opt.units == "in" else value_mm
    d = opt.decimals if opt.units == "mm" else max(opt.decimals, 3)
    s = f"{v:.{d}f}"
    if "." in s and opt.units == "mm":
        s = s.rstrip("0").rstrip(".")
    return s


def text_width(s: str, h: float = TEXT_H) -> float:
    return 0.6 * h * len(s)


# ----------------------------------------------------------------------------- primitives
def _arrow(sheet: Sheet, tip: Pt, direction: Pt):
    dx, dy = direction
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    bx, by = tip[0] - ux * ARROW_L, tip[1] - uy * ARROW_L
    px, py = -uy * ARROW_W / 2 * 1.0, ux * ARROW_W / 2 * 1.0
    sheet.add(Solid([tip, (bx + px, by + py), (bx - px, by - py)]))


def linear_dim(sheet: Sheet, p1: Pt, p2: Pt, orient: str, at: float, label: str):
    """Horizontal (orient='h', dimension line at y=`at`) or vertical (orient='v', x=`at`) dimension."""
    if orient == "h":
        a, b = (p1[0], at), (p2[0], at)
        for p in (p1, p2):
            sgn = 1 if at > p[1] else -1
            sheet.add(Polyline([(p[0], p[1] + sgn * EXT_GAP), (p[0], at + sgn * EXT_OVER)], "DIM"))
    else:
        a, b = (at, p1[1]), (at, p2[1])
        for p in (p1, p2):
            sgn = 1 if at > p[0] else -1
            sheet.add(Polyline([(p[0] + sgn * EXT_GAP, p[1]), (at + sgn * EXT_OVER, p[1])], "DIM"))
    length = math.dist(a, b)
    if length < 1e-6:
        return
    d = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    tw = text_width(label)
    if length >= 2 * ARROW_L + 1:
        sheet.add(Polyline([a, b], "DIM"))
        _arrow(sheet, a, (-d[0], -d[1]))
        _arrow(sheet, b, d)
    else:  # too small: arrows outside pointing in
        ext = ARROW_L + 2
        sheet.add(Polyline([(a[0] - d[0] * ext, a[1] - d[1] * ext), (b[0] + d[0] * ext, b[1] + d[1] * ext)], "DIM"))
        _arrow(sheet, a, d)
        _arrow(sheet, b, (-d[0], -d[1]))
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    if orient == "h":
        x = mid[0] if length > tw + 2 else b[0] + ARROW_L + 2 + tw / 2
        sheet.add(Text(x, mid[1] + 1.0, label))
    else:
        y = mid[1] if length > tw + 2 else b[1] + ARROW_L + 2 + tw / 2
        sheet.add(Text(mid[0] - 1.0, y, label, angle=90.0))


def leader(sheet: Sheet, cx: float, cy: float, r: float, angle_deg: float, label: str, length: float = 12.0):
    """Leader from a circle's edge outwards to a horizontal shoulder with the label."""
    t = math.radians(angle_deg)
    tip = (cx + r * math.cos(t), cy + r * math.sin(t))
    knee = (cx + (r + length) * math.cos(t), cy + (r + length) * math.sin(t))
    right = math.cos(t) >= 0
    tw = text_width(label)
    end = (knee[0] + (tw + 2 if right else -(tw + 2)), knee[1])
    sheet.add(Polyline([tip, knee, end], "DIM"))
    _arrow(sheet, tip, (tip[0] - knee[0], tip[1] - knee[1]))
    sheet.add(Text(knee[0] + (1 if right else -1), knee[1] + 0.8, label, halign="left" if right else "right"))


def center_mark(sheet: Sheet, cx: float, cy: float, r: float):
    e = r + 2.0
    sheet.add(Polyline([(cx - e, cy), (cx + e, cy)], "CENTER"))
    sheet.add(Polyline([(cx, cy - e), (cx, cy + e)], "CENTER"))


# ----------------------------------------------------------------------------- planning
@dataclass
class _Plan:
    view: View2D
    bounds: tuple[float, float, float, float]
    overall_h: bool = False  # width dimension below
    overall_v: bool = False  # height dimension on the left
    pos_x: list[float] = field(default_factory=list)  # hole centre x coordinates (model units)
    pos_y: list[float] = field(default_factory=list)
    leaders: list[tuple] = field(default_factory=list)  # (circle, label)

    @property
    def w(self):
        return self.bounds[2] - self.bounds[0]

    @property
    def h(self):
        return self.bounds[3] - self.bounds[1]

    def pads(self) -> tuple[float, float, float, float]:
        """Space (sheet mm, independent of scale) needed around the view: left, right, bottom, top."""
        lead = 22.0 if self.leaders else 6.0
        left = FIRST_DIM + 6 if self.overall_v else 6.0
        bottom = (FIRST_DIM + 8 if self.overall_h else 6.0) + 6  # + view label
        top = (FIRST_DIM + DIM_STEP * (len(self.pos_x) - 1) + 6) if self.pos_x else 6.0
        right = (FIRST_DIM + DIM_STEP * (len(self.pos_y) - 1) + 6) if self.pos_y else 6.0
        return left, max(right, lead), bottom, max(top, lead)


def _unique(values, tol=1e-3):
    out: list[float] = []
    for v in sorted(values):
        if not out or abs(v - out[-1]) > tol:
            out.append(v)
    return out


def _axis(vec) -> tuple[int, float]:
    """Model axis index (0=X,1=Y,2=Z) and sign for an axis-aligned view direction."""
    i = max(range(3), key=lambda k: abs(vec[k]))
    return i, (1.0 if vec[i] > 0 else -1.0)


MAX_LEVELS = 8  # per view axis; beyond this the view is too busy for baseline dims


def _plan_views(views: dict[str, View2D], opt: Options) -> dict[str, _Plan]:
    plans = {n: _Plan(v, v.bounds()) for n, v in views.items()}
    ortho = [n for n in ("front", "top", "right") if n in plans]
    if opt.overall_dims and ortho:
        main = "front" if "front" in plans else ortho[0]
        plans[main].overall_h = plans[main].overall_v = True
        # depth: shown once, on the top view (vertical) or else on the side view (horizontal)
        if main == "front":
            if "top" in plans:
                plans["top"].overall_v = True
            elif "right" in plans:
                plans["right"].overall_h = True

    # Cylinder silhouettes (centre +/- r along each model axis) are implied by diameter callouts.
    implied: set[tuple[int, float]] = set()
    for n in ortho:
        v = plans[n].view
        (ui, us), (vi, vs) = _axis(v.u_axis), _axis(v.v_axis)
        for c in v.circles:
            if c.full:
                for d in (-c.r, c.r):
                    implied.add((ui, round(us * (c.cx + d), 2)))
                    implied.add((vi, round(vs * (c.cy + d), 2)))

    done: set[tuple[int, float]] = set()  # (model axis, model coordinate) already dimensioned
    for n in ortho:  # also mark overall extents as done
        p = plans[n]
        (ui, us), (vi, vs) = _axis(p.view.u_axis), _axis(p.view.v_axis)
        x0, y0, x1, y1 = p.bounds
        for x in (x0, x1):
            done.add((ui, round(us * x, 2)))
        for y in (y0, y1):
            done.add((vi, round(vs * y, 2)))

    seen_d: set[float] = set()
    seen_r: set[float] = set()
    for n in ortho:
        p = plans[n]
        full = [c for c in p.view.circles if c.full]
        arcs = [c for c in p.view.circles if not c.full]
        if opt.hole_dims:
            by_r: dict[float, list] = {}
            for c in full:
                by_r.setdefault(round(c.r, 3), []).append(c)
            for r, cs in sorted(by_r.items(), reverse=True):
                if r in seen_d:
                    continue
                seen_d.add(r)
                n_holes = len({(round(c.cx, 3), round(c.cy, 3)) for c in cs})
                label = ("%d× " % n_holes if n_holes > 1 else "") + "Ø" + fmt(2 * r, opt)
                rep = max(cs, key=lambda c: (c.cx + c.cy))  # top/right-most for a clean leader
                p.leaders.append((rep, label))
        if opt.radius_dims:
            for c in sorted(arcs, key=lambda c: -c.r):
                r = round(c.r, 3)
                if r in seen_r or r in seen_d:
                    continue
                seen_r.add(r)
                p.leaders.append((c, "R" + fmt(c.r, opt)))
                if len(seen_r) >= 6:
                    break
        if opt.hole_positions:
            x0, y0, x1, y1 = p.bounds
            (ui, us), (vi, vs) = _axis(p.view.u_axis), _axis(p.view.v_axis)
            round_part = any(abs(2 * c.r - (x1 - x0)) < 1e-3 or abs(2 * c.r - (y1 - y0)) < 1e-3 for c in full)
            hole_x = [] if round_part else [c.cx for c in full]
            hole_y = [] if round_part else [c.cy for c in full]
            # step / shoulder edges: straight visible edges parallel to a view axis
            edge_x, edge_y = [], []
            for (a, b) in p.view.lines + p.view.hidden_lines:
                if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) > 1e-6:
                    edge_x.append(a[0])
                elif abs(a[1] - b[1]) < 1e-6 and abs(a[0] - b[0]) > 1e-6:
                    edge_y.append(a[1])

            def pick(holes, edges, lo, hi, ai, sg):
                out = []
                for v in _unique(holes + [e for e in edges if (ai, round(sg * e, 2)) not in implied]):
                    key = (ai, round(sg * v, 2))
                    if 1e-3 < v - lo < hi - lo - 1e-3 and key not in done:
                        out.append(v)
                return out if len(out) <= MAX_LEVELS else [v for v in _unique(holes) if lo < v < hi][:MAX_LEVELS]

            p.pos_x = pick(hole_x, edge_x, x0, x1, ui, us)
            p.pos_y = pick(hole_y, edge_y, y0, y1, vi, vs)
            for v in p.pos_x:
                done.add((ui, round(us * v, 2)))
            for v in p.pos_y:
                done.add((vi, round(vs * v, 2)))
    return plans


# ----------------------------------------------------------------------------- layout
def _grid(opt: Options, names) -> dict[str, tuple[int, int]]:
    """(column, row) of each view; row 0 is the bottom row."""
    if opt.projection == "first":
        g = {"front": (0, 1), "top": (0, 0), "right": (1, 1), "iso": (1, 0)}
    else:
        g = {"front": (0, 0), "top": (0, 1), "right": (1, 0), "iso": (1, 1)}
    return {n: g[n] for n in names}


def build_sheet(views: dict[str, View2D], opt: Options, source_name: str = "") -> Sheet:
    W, H = SHEETS[opt.sheet]
    sheet = Sheet(W, H)
    margin = 10.0
    tb_h = 34.0
    plans = _plan_views(views, opt)
    grid = _grid(opt, plans)

    # area for views: inside border, above title block
    ax0, ay0, ax1, ay1 = margin + 4, margin + tb_h + 4, W - margin - 4, H - margin - 4

    cols = {0: [], 1: []}
    rows = {0: [], 1: []}
    for n, (c, r) in grid.items():
        cols[c].append(n)
        rows[r].append(n)

    def needed(s):
        widths, heights = [], []
        for c in (0, 1):
            if cols[c]:
                widths.append(max(plans[n].w * s + plans[n].pads()[0] + plans[n].pads()[1] for n in cols[c]))
        for r in (0, 1):
            if rows[r]:
                heights.append(max(plans[n].h * s + plans[n].pads()[2] + plans[n].pads()[3] for n in rows[r]))
        return sum(widths), sum(heights)

    # pick scale
    if opt.scale != "auto":
        scale, label = next((f, l) for f, l in SCALES if l == opt.scale)
    else:
        scale, label = SCALES[-1]
        for f, l in SCALES:
            w, h = needed(f)
            if w <= ax1 - ax0 and h <= ay1 - ay0:
                scale, label = f, l
                break
    sheet.scale_label = label

    # column widths / row heights at the chosen scale, then centre the block in the area
    col_w = {c: max((plans[n].w * scale + sum(plans[n].pads()[:2]) for n in cols[c]), default=0) for c in (0, 1)}
    row_h = {r: max((plans[n].h * scale + sum(plans[n].pads()[2:]) for n in rows[r]), default=0) for r in (0, 1)}
    total_w, total_h = col_w[0] + col_w[1], row_h[0] + row_h[1]
    bx = ax0 + max(0.0, (ax1 - ax0 - total_w) / 2)
    by = ay0 + max(0.0, (ay1 - ay0 - total_h) / 2)
    col_x = {0: bx, 1: bx + col_w[0]}
    row_y = {0: by, 1: by + row_h[0]}

    # Views sharing a column share an x origin (projection alignment); same for rows.
    col_left = {c: max((plans[n].pads()[0] for n in cols[c]), default=0) for c in (0, 1)}
    row_bot = {r: max((plans[n].pads()[2] for n in rows[r]), default=0) for r in (0, 1)}
    for n, (c, r) in grid.items():
        p = plans[n]
        ox = col_x[c] + col_left[c]
        oy = row_y[r] + row_bot[r]
        if n == "iso":  # iso isn't projection-aligned: centre it in its cell
            ox = col_x[c] + (col_w[c] - p.w * scale) / 2
            oy = row_y[r] + (row_h[r] - p.h * scale) / 2
        _draw_view(sheet, p, ox, oy, scale, opt)

    _border_and_title(sheet, opt, margin, tb_h, source_name)
    return sheet


def _draw_view(sheet: Sheet, p: _Plan, ox: float, oy: float, s: float, opt: Options):
    x0, y0, x1, y1 = p.bounds

    def T(pt):
        return (ox + (pt[0] - x0) * s, oy + (pt[1] - y0) * s)

    if opt.hidden_lines:
        for pl in p.view.hidden:
            sheet.add(Polyline([T(q) for q in pl], "HIDDEN"))
    for pl in p.view.visible:
        sheet.add(Polyline([T(q) for q in pl], "VISIBLE"))

    if p.view.name == "iso":
        sheet.add(Text(ox + p.w * s / 2, oy - 7, "ISOMETRIC VIEW", 3.0, "TITLE"))
        return

    L, B = T((x0, y0))
    R, Tp = T((x1, y1))
    if opt.center_marks:
        for c in p.view.circles:
            if c.full:
                cx, cy = T((c.cx, c.cy))
                center_mark(sheet, cx, cy, c.r * s)

    if p.overall_h:
        linear_dim(sheet, (L, B), (R, B), "h", B - FIRST_DIM, fmt(x1 - x0, opt))
    if p.overall_v:
        linear_dim(sheet, (L, B), (L, Tp), "v", L - FIRST_DIM, fmt(y1 - y0, opt))
    # hole positions: baseline (datum = left / bottom edge), stacked above / to the right
    for i, x in enumerate(p.pos_x):
        cx, _ = T((x, y0))
        linear_dim(sheet, (L, Tp), (cx, Tp), "h", Tp + FIRST_DIM + i * DIM_STEP, fmt(x - x0, opt))
    for i, y in enumerate(p.pos_y):
        _, cy = T((x0, y))
        linear_dim(sheet, (R, B), (R, cy), "v", R + FIRST_DIM + i * DIM_STEP, fmt(y - y0, opt))

    angles = [45, 135, 30, 150, 60, 120, 20, 160]
    for i, (c, label) in enumerate(p.leaders):
        cx, cy = T((c.cx, c.cy))
        mx, my = (L + R) / 2, (B + Tp) / 2
        if math.hypot(cx - mx, cy - my) > 1.0:  # point the leader away from the middle of the view
            ang = math.degrees(math.atan2(cy - my, cx - mx))
            ang = (round((ang - 45) / 90) * 90 + 45) % 360  # snap to a diagonal
        else:
            ang = angles[i % len(angles)]
        length = 8 + 3 * (i % 3)
        if c.full and math.hypot(cx - mx, cy - my) > 1.0:  # run the leader out past the part outline
            t = math.radians(ang)
            base = length
            while length < 40:
                kx, ky = cx + (c.r * s + length) * math.cos(t), cy + (c.r * s + length) * math.sin(t)
                if not (L - 1 < kx < R + 1 and B - 1 < ky < Tp + 1):
                    # don't run into the stacked position dimensions on the right / top
                    if (kx >= R + 1 and p.pos_y) or (ky >= Tp + 1 and p.pos_x):
                        length = base
                    break
                length += 2
        leader(sheet, cx, cy, c.r * s, ang, label, length=length)

    sheet.add(Text((L + R) / 2, B - (FIRST_DIM + 8 if p.overall_h else 6), p.view.name.upper() + " VIEW", 3.0, "TITLE", valign="top"))


def _border_and_title(sheet: Sheet, opt: Options, m: float, tb_h: float, source_name: str):
    W, H = sheet.width, sheet.height
    sheet.add(Polyline([(m, m), (W - m, m), (W - m, H - m), (m, H - m)], "BORDER", closed=True))
    tw = 180.0
    x0, y0 = W - m - tw, m
    sheet.add(Polyline([(x0, y0), (W - m, y0), (W - m, y0 + tb_h), (x0, y0 + tb_h)], "BORDER", closed=True))
    # rows: 3 rows; column splits
    r1, r2 = y0 + tb_h / 3, y0 + 2 * tb_h / 3
    sheet.add(Polyline([(x0, r2), (W - m, r2)], "TITLE"))
    sheet.add(Polyline([(x0, r1), (W - m, r1)], "TITLE"))
    for x in (x0 + 45, x0 + 90, x0 + 135):
        sheet.add(Polyline([(x, y0), (x, r1)], "TITLE"))
    sheet.add(Polyline([(x0 + 120, r1), (x0 + 120, r2)], "TITLE"))

    def cell(x, y, label, value, h=3.0):
        sheet.add(Text(x + 1.5, y + tb_h / 3 - 1.2, label, 1.8, "TITLE", "left", "top"))
        sheet.add(Text(x + 1.5, y + 1.5, value, h, "TITLE", "left", "bottom"))

    title = opt.title or source_name or "UNTITLED"
    sheet.add(Text(x0 + 1.5, r2 + tb_h / 3 - 1.2, "TITLE", 1.8, "TITLE", "left", "top"))
    sheet.add(Text(x0 + 1.5, r2 + 1.2, title.upper(), 3.5, "TITLE", "left", "bottom"))
    cell(x0, r1, "DRAWING NO.", opt.drawing_no or "-")
    cell(x0 + 120, r1, "COMPANY", opt.company or "-")
    cell(x0, y0, "SCALE", sheet.scale_label)
    cell(x0 + 45, y0, "UNITS", "MM" if opt.units == "mm" else "INCH")
    cell(x0 + 90, y0, "DRAWN / DATE", f"{opt.drawn_by or '-'}  {_dt.date.today().isoformat()}", 2.5)
    cell(x0 + 135, y0, "SHEET / REV", f"{opt.sheet}  REV {opt.revision or '-'}", 2.5)
    proj = "THIRD ANGLE PROJECTION" if opt.projection == "third" else "FIRST ANGLE PROJECTION"
    note = proj + (f"   MATERIAL: {opt.material.upper()}" if opt.material else "")
    sheet.add(Text(x0, y0 + tb_h + 2, note, 2.5, "TITLE", "left", "bottom"))
    sheet.add(Text(m + 3, m + 3, "UNLESS OTHERWISE SPECIFIED DIMENSIONS ARE IN "
                   + ("MILLIMETRES" if opt.units == "mm" else "INCHES"), 2.5, "TITLE", "left", "bottom"))


def make_drawing(shape, opt: Options, source_name: str = "", progress=None) -> Sheet:
    from .geometry import project

    views = {}
    for i, n in enumerate(opt.views):
        if progress:
            progress(i, len(opt.views), n)
        # first-angle projection places the view seen from the LEFT to the right of the front view
        src = "left" if (n == "right" and opt.projection == "first") else n
        views[n] = project(shape, src, with_hidden=opt.hidden_lines)
    return build_sheet(views, opt, source_name)
