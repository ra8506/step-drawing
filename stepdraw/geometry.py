"""STEP loading and hidden-line projection with OpenCascade (OCP)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_EDGE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

Pt = tuple[float, float]

# Each view: eye direction (towards viewer) and the model axis that maps to the sheet's +x.
# Model is assumed Z-up (the usual STEP convention); "Y-up" models are rotated on load.
VIEW_DIRS = {
    "front": ((0, -1, 0), (1, 0, 0)),
    "back": ((0, 1, 0), (-1, 0, 0)),
    "top": ((0, 0, 1), (1, 0, 0)),
    "bottom": ((0, 0, -1), (1, 0, 0)),
    "right": ((1, 0, 0), (0, 1, 0)),
    "left": ((-1, 0, 0), (0, -1, 0)),
    "iso": ((1, -1, 1), (1, 1, 0)),
}


class StepLoadError(Exception):
    pass


def load_step(path: str | Path, y_up: bool = False) -> TopoDS_Shape:
    """Read a STEP file into one shape (lengths converted to millimetres)."""
    Interface_Static.SetCVal_s("xstep.cascade.unit", "MM")
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise StepLoadError(f"Could not read STEP file: {path}")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape is None or shape.IsNull():
        raise StepLoadError("The STEP file contains no geometry.")
    if y_up:
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCP.gp import gp_Ax1, gp_Trsf

        t = gp_Trsf()
        t.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)), math.pi / 2)
        shape = BRepBuilderAPI_Transform(shape, t, True).Shape()
    return shape


def load_model(path: str | Path, y_up: bool = False, board_thickness: float | None = None) -> TopoDS_Shape:
    """Load a STEP model, or build a board solid from a Gerber board-outline file."""
    from .gerber import DEFAULT_THICKNESS, GERBER_EXTS, load_gerber

    if Path(path).suffix.lower() in GERBER_EXTS:
        return load_gerber(path, board_thickness or DEFAULT_THICKNESS)
    return load_step(path, y_up=y_up)


def shape_info(shape: TopoDS_Shape) -> dict:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    a, b = box.CornerMin(), box.CornerMax()
    x0, y0, z0, x1, y1, z1 = a.X(), a.Y(), a.Z(), b.X(), b.Y(), b.Z()
    n = 0
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        n += 1
        exp.Next()
    return {"size": (x1 - x0, y1 - y0, z1 - z0), "solids": n}


@dataclass
class Circle2D:
    cx: float
    cy: float
    r: float
    full: bool  # full circle (hole/boss) vs. arc (fillet/round)


@dataclass
class View2D:
    name: str
    visible: list[list[Pt]] = field(default_factory=list)
    hidden: list[list[Pt]] = field(default_factory=list)
    circles: list[Circle2D] = field(default_factory=list)
    lines: list[tuple[Pt, Pt]] = field(default_factory=list)  # straight visible segments
    hidden_lines: list[tuple[Pt, Pt]] = field(default_factory=list)  # straight hidden segments
    exact: tuple[float, float, float, float] | None = None
    u_axis: tuple[float, float, float] = (1.0, 0.0, 0.0)  # model direction of sheet +x
    v_axis: tuple[float, float, float] = (0.0, 0.0, 1.0)  # model direction of sheet +y

    def bounds(self) -> tuple[float, float, float, float]:
        if self.exact:
            return self.exact
        xs = [p[0] for pl in self.visible for p in pl] or [0.0]
        ys = [p[1] for pl in self.visible for p in pl] or [0.0]
        return min(xs), min(ys), max(xs), max(ys)


def _projector(name: str) -> HLRAlgo_Projector:
    (dx, dy, dz), (xx, xy, xz) = VIEW_DIRS[name]
    return HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(dx, dy, dz), gp_Dir(xx, xy, xz)))


def _edges(compound):
    if compound is None or compound.IsNull():
        return
    exp = TopExp_Explorer(compound, TopAbs_EDGE)
    while exp.More():
        yield TopoDS.Edge(exp.Current())
        exp.Next()


def _discretize(edge, deflection: float) -> list[Pt]:
    curve = BRepAdaptor_Curve(edge)
    if curve.GetType() == GeomAbs_Line:
        a, b = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        return [(a.X(), a.Y()), (b.X(), b.Y())]
    disc = GCPnts_QuasiUniformDeflection(curve, deflection)
    if not disc.IsDone() or disc.NbPoints() < 2:
        a, b = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        return [(a.X(), a.Y()), (b.X(), b.Y())]
    return [(disc.Value(i).X(), disc.Value(i).Y()) for i in range(1, disc.NbPoints() + 1)]


def project(shape: TopoDS_Shape, name: str, with_hidden: bool = True, smooth_edges: bool = False) -> View2D:
    """Hidden-line removal projection of `shape` for the named view."""
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(_projector(name))
    algo.Update()
    algo.Hide()
    hlr = HLRBRep_HLRToShape(algo)

    info = shape_info(shape)
    deflection = max(max(info["size"]) / 2000.0, 1e-3)

    view = View2D(name)
    (dx, dy, dz), (xx, xy, xz) = VIEW_DIRS[name]
    view.u_axis = (float(xx), float(xy), float(xz))
    view.v_axis = (dy * xz - dz * xy, dz * xx - dx * xz, dx * xy - dy * xx)  # N x X
    vis = [hlr.VCompound(), hlr.OutLineVCompound()]
    hid = [hlr.HCompound(), hlr.OutLineHCompound()]
    if smooth_edges:
        vis.append(hlr.Rg1LineVCompound())
        hid.append(hlr.Rg1LineHCompound())

    arcs: dict[tuple, list] = {}
    for comp in vis:
        for e in _edges(comp):
            pts = _discretize(e, deflection)
            view.visible.append(pts)
            c = BRepAdaptor_Curve(e)
            t = c.GetType()
            if t == GeomAbs_Line or _is_straight(pts, deflection * 2):
                view.lines.append((pts[0], pts[-1]))  # includes circles seen edge-on
            if t == GeomAbs_Circle:
                circ = c.Circle()
                loc = circ.Location()
                key = (round(loc.X(), 3), round(loc.Y(), 3), round(circ.Radius(), 3))
                span = abs(c.LastParameter() - c.FirstParameter())
                arcs.setdefault(key, []).append(span)
    box = Bnd_Box()
    for comp in vis:
        if comp is not None and not comp.IsNull():
            BRepBndLib.AddOptimal_s(comp, box, False, False)
    if not box.IsVoid():
        a, b = box.CornerMin(), box.CornerMax()
        view.exact = (a.X(), a.Y(), b.X(), b.Y())
    for (cx, cy, r), spans in arcs.items():
        view.circles.append(Circle2D(cx, cy, r, full=sum(spans) > 2 * math.pi - 1e-3))

    if with_hidden and name != "iso":
        for comp in hid:
            for e in _edges(comp):
                pts = _discretize(e, deflection)
                view.hidden.append(pts)
                if _is_straight(pts, deflection * 2):
                    view.hidden_lines.append((pts[0], pts[-1]))
        view.hidden = _drop_covered(view.hidden, view.visible, tol=max(deflection * 4, 1e-3))
    return view


def _is_straight(pts: list[Pt], tol: float) -> bool:
    if len(pts) < 2 or math.dist(pts[0], pts[-1]) < tol:
        return False
    return all(_seg_dist(p, pts[0], pts[-1]) <= tol for p in pts)


def _seg_dist(p, a, b) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def _drop_covered(hidden: list[list[Pt]], visible: list[list[Pt]], tol: float) -> list[list[Pt]]:
    """Remove hidden polylines that lie on top of visible ones (visible lines take precedence)."""
    if not hidden or not visible:
        return hidden
    segs = [(pl[i], pl[i + 1]) for pl in visible for i in range(len(pl) - 1)]
    xs = [p[0] for pl in visible for p in pl]
    ys = [p[1] for pl in visible for p in pl]
    cell = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6) / 64.0
    grid: dict[tuple[int, int], list[int]] = {}
    for idx, (a, b) in enumerate(segs):
        for gx in range(int(math.floor((min(a[0], b[0]) - tol) / cell)), int(math.floor((max(a[0], b[0]) + tol) / cell)) + 1):
            for gy in range(int(math.floor((min(a[1], b[1]) - tol) / cell)), int(math.floor((max(a[1], b[1]) + tol) / cell)) + 1):
                grid.setdefault((gx, gy), []).append(idx)

    def covered(p) -> bool:
        key = (int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell)))
        return any(_seg_dist(p, *segs[i]) <= tol for i in grid.get(key, ()))

    out = []
    for pl in hidden:
        samples = list(pl)
        for i in range(len(pl) - 1):  # midpoints catch long straight runs
            samples.append(((pl[i][0] + pl[i + 1][0]) / 2, (pl[i][1] + pl[i + 1][1]) / 2))
        if not all(covered(p) for p in samples):
            out.append(pl)
    return out


def project_fast(shape: TopoDS_Shape, name: str, with_hidden: bool = False) -> View2D:
    """Quick mesh-based projection (HLRBRep_PolyAlgo) for an instant preview.

    Much faster than exact HLR on big parts, but curves become polylines, so no holes/arcs are detected."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape

    info = shape_info(shape)
    size = max(info["size"]) or 1.0
    BRepMesh_IncrementalMesh(shape, size / 300.0, False, 0.5, True)
    algo = HLRBRep_PolyAlgo(shape)
    algo.Projector(_projector(name))
    algo.Update()
    hlr = HLRBRep_PolyHLRToShape()
    hlr.Update(algo)
    view = View2D(name)
    (dx, dy, dz), (xx, xy, xz) = VIEW_DIRS[name]
    view.u_axis = (float(xx), float(xy), float(xz))
    view.v_axis = (dy * xz - dz * xy, dz * xx - dx * xz, dx * xy - dy * xx)
    for comp in (hlr.VCompound(), hlr.OutLineVCompound()):
        for e in _edges(comp):
            view.visible.append(_discretize(e, size / 1000.0))
    if with_hidden and name != "iso":
        for comp in (hlr.HCompound(), hlr.OutLineHCompound()):
            for e in _edges(comp):
                view.hidden.append(_discretize(e, size / 1000.0))
    return view
