"""Gerber (RS-274X) import: a PCB board-outline layer becomes a flat board solid.

The board outline (KiCad Edge.Cuts, Altium .GKO/.GM1, Eagle .GML …) is read as a set of line and arc
draws, chained into closed loops and extruded to the board thickness. The outer loop is the board edge;
loops inside it are cutouts and holes. Arcs stay true circles, so round holes get Ø callouts like they
would from a STEP model, and the rest of the drawing pipeline is unchanged.

Supported: %FS (leading/trailing zero suppression, absolute), %MO / G70 / G71, %AD (apertures are read
but only their centre line matters for an outline), D01/D02/D03, G01/G02/G03, G74/G75, G36/G37 regions,
and repeated modal coordinates. Aperture macros, step-and-repeat and polarity are ignored.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

# Board-outline layers by CAD tool: KiCad .gbr/.gm1, Altium .gko and mechanical layers .gm1-.gm32,
# Eagle .gml, generic .ger/.pho/.art. Copper/mask/silk extensions are recognised only to say "wrong layer".
OUTLINE_EXTS = (".gbr", ".ger", ".gko", ".gm", ".gml", ".pho", ".art") + tuple(f".gm{i}" for i in range(1, 33))
NOT_OUTLINE_EXTS = {".gtl": "top copper", ".gbl": "bottom copper", ".gto": "top silkscreen",
                    ".gbo": "bottom silkscreen", ".gts": "top solder mask", ".gbs": "bottom solder mask",
                    ".gtp": "top paste", ".gbp": "bottom paste", ".gpt": "top pad", ".gpb": "bottom pad"}
NOT_OUTLINE_EXTS.update({f".g{i}": "inner copper" for i in range(1, 33)})
GERBER_EXTS = OUTLINE_EXTS + tuple(NOT_OUTLINE_EXTS)


def looks_like_gerber(path: str | Path) -> bool:
    """True for a Gerber file, judged by extension or (for unknown extensions) by its first few KB."""
    if Path(path).suffix.lower() in GERBER_EXTS:
        return True
    try:
        with open(path, "rb") as f:
            head = f.read(4096).decode("ascii", errors="replace")
    except OSError:
        return False
    return "ISO-10303-21" not in head and bool(re.search(r"%FS[LTD]?[AI]?.*X\d\dY\d\d", head))


DEFAULT_THICKNESS = 1.6  # mm, the usual FR-4 board

Pt = tuple[float, float]


class GerberError(Exception):
    pass


@dataclass
class Seg:
    """A straight line (center None) or circular arc from a to b, in mm."""
    a: Pt
    b: Pt
    center: Pt | None = None
    ccw: bool = True

    def reversed(self) -> "Seg":
        return Seg(self.b, self.a, self.center, not self.ccw)

    @property
    def full_circle(self) -> bool:
        return self.center is not None and _close(self.a, self.b, 1e-9)

    def mid(self) -> Pt:
        if self.center is None:
            return ((self.a[0] + self.b[0]) / 2, (self.a[1] + self.b[1]) / 2)
        cx, cy = self.center
        r = math.hypot(self.a[0] - cx, self.a[1] - cy)
        t0 = math.atan2(self.a[1] - cy, self.a[0] - cx)
        sweep = _sweep(self)
        t = t0 + sweep / 2
        return (cx + r * math.cos(t), cy + r * math.sin(t))

    def points(self, n: int = 32) -> list[Pt]:
        if self.center is None:
            return [self.a, self.b]
        cx, cy = self.center
        r = math.hypot(self.a[0] - cx, self.a[1] - cy)
        t0 = math.atan2(self.a[1] - cy, self.a[0] - cx)
        sw = _sweep(self)
        k = max(2, int(n * abs(sw) / (2 * math.pi)) + 1)
        return [(cx + r * math.cos(t0 + sw * i / k), cy + r * math.sin(t0 + sw * i / k)) for i in range(k + 1)]


def _close(p: Pt, q: Pt, tol: float) -> bool:
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def _sweep(s: Seg) -> float:
    """Signed sweep angle of an arc (positive = counter-clockwise)."""
    cx, cy = s.center
    t0 = math.atan2(s.a[1] - cy, s.a[0] - cx)
    t1 = math.atan2(s.b[1] - cy, s.b[0] - cx)
    if s.full_circle:
        return 2 * math.pi if s.ccw else -2 * math.pi
    d = t1 - t0
    if s.ccw:
        while d <= 0:
            d += 2 * math.pi
    else:
        while d >= 0:
            d -= 2 * math.pi
    return d


# ----------------------------------------------------------------------------- parsing
_WORD = re.compile(r"([A-Z])([+-]?[0-9.]+)")


def parse(text: str) -> list[Seg]:
    """Return every line/arc draw (D01, plus region contours) in the file, in millimetres."""
    int_x = dec_x = int_y = dec_y = None
    trailing = False
    scale = 1.0  # file units -> mm
    interp = "G01"
    multi = True  # G75 is the only mode allowed by the current spec; G74 is legacy
    x = y = 0.0
    segs: list[Seg] = []

    def num(s: str, dec: int, integ: int) -> float:
        if "." in s:
            return float(s) * scale
        sign = -1.0 if s.startswith("-") else 1.0
        digits = s.lstrip("+-")
        if trailing:
            digits = digits.ljust(integ + dec, "0")
        return sign * int(digits or "0") / 10 ** dec * scale

    # split into extended (%...%) commands and ordinary word-commands ending in '*'
    pos = 0
    text = text.replace("\r", "").replace("\n", "")
    while pos < len(text):
        if text[pos] == "%":
            end = text.find("%", pos + 1)
            if end < 0:
                break
            for block in text[pos + 1:end].split("*"):
                block = block.strip()
                if block.startswith("FS"):
                    m = re.match(r"FS([LTD]?)([AI]?).*X(\d)(\d)Y(\d)(\d)", block)
                    if not m:
                        raise GerberError(f"Unsupported format statement: %{block}%")
                    trailing = m.group(1) == "T"
                    if m.group(2) == "I":
                        raise GerberError("Incremental Gerber coordinates are not supported.")
                    int_x, dec_x, int_y, dec_y = (int(m.group(i)) for i in range(3, 7))
                elif block.startswith("MOIN"):
                    scale = 25.4
                elif block.startswith("MOMM"):
                    scale = 1.0
            pos = end + 1
            continue
        end = text.find("*", pos)
        if end < 0:
            break
        cmd = text[pos:end].strip()
        pos = end + 1
        if not cmd or cmd.startswith("G04") or cmd.startswith("G4 "):
            continue
        if cmd in ("M02", "M00", "M2"):
            break
        words = _WORD.findall(cmd)
        nx, ny, i_off, j_off, d = x, y, 0.0, 0.0, None
        for letter, val in words:
            if letter == "G":
                g = int(float(val))
                if g in (1, 2, 3):
                    interp = f"G0{g}"
                elif g == 70:
                    scale = 25.4
                elif g == 71:
                    scale = 1.0
                elif g == 74:
                    multi = False
                elif g == 75:
                    multi = True
                elif g == 4:
                    break  # comment
            elif letter in "XYIJ":
                if int_x is None:
                    raise GerberError("Coordinates appear before the %FS format statement.")
                v = num(val, dec_x if letter in "XI" else dec_y, int_x if letter in "XI" else int_y)
                if letter == "X":
                    nx = v
                elif letter == "Y":
                    ny = v
                elif letter == "I":
                    i_off = v
                else:
                    j_off = v
            elif letter == "D":
                d = int(float(val))
        if d is None and any(w[0] in "XY" for w in words):
            d = 1  # deprecated modal D01
        if d == 1:
            if interp == "G01":
                if not _close((x, y), (nx, ny), 1e-9):
                    segs.append(Seg((x, y), (nx, ny)))
            else:
                ccw = interp == "G03"
                if multi:
                    c = (x + i_off, y + j_off)
                else:
                    c = _single_quadrant_center((x, y), (nx, ny), abs(i_off), abs(j_off), ccw)
                segs.append(Seg((x, y), (nx, ny), c, ccw))
        if d in (1, 2, 3):
            x, y = nx, ny
    if int_x is None:
        raise GerberError("This doesn't look like a Gerber (RS-274X) file: no %FS format statement.")
    return segs


def _single_quadrant_center(a: Pt, b: Pt, i: float, j: float, ccw: bool) -> Pt:
    """G74: I/J are unsigned; pick the centre that makes an arc of at most 90° in the right direction."""
    best, err = None, None
    for sx in (1, -1):
        for sy in (1, -1):
            c = (a[0] + sx * i, a[1] + sy * j)
            s = Seg(a, b, c, ccw)
            e = abs(math.hypot(a[0] - c[0], a[1] - c[1]) - math.hypot(b[0] - c[0], b[1] - c[1]))
            if abs(_sweep(s)) <= math.pi / 2 + 1e-6 and (err is None or e < err):
                best, err = c, e
    return best or (a[0] + i, a[1] + j)


# ----------------------------------------------------------------------------- loops
def _dedupe(segs: list[Seg], tol: float) -> list[Seg]:
    """Drop segments drawn twice (either direction); CAD tools often overlap outline layers."""
    out: list[Seg] = []
    for s in segs:
        for t in out:
            same = (_close(s.a, t.a, tol) and _close(s.b, t.b, tol) and s.ccw == t.ccw) or \
                   (_close(s.a, t.b, tol) and _close(s.b, t.a, tol) and (s.ccw != t.ccw or s.center is None))
            if same and (s.center is None) == (t.center is None) and \
                    (s.center is None or _close(s.center, t.center, tol)):
                break
        else:
            out.append(s)
    return out


def chain(segs: list[Seg], tol: float = 0.02) -> list[list[Seg]]:
    """Join segments end to end into closed loops. Open chains are dropped."""
    segs = _dedupe(segs, tol)
    loops = [[s] for s in segs if s.full_circle]
    rest = [s for s in segs if not s.full_circle]
    used = [False] * len(rest)
    for start in range(len(rest)):
        if used[start]:
            continue
        used[start] = True
        loop = [rest[start]]
        while not _close(loop[-1].b, loop[0].a, tol):
            end = loop[-1].b
            for k, s in enumerate(rest):
                if used[k]:
                    continue
                if _close(s.a, end, tol):
                    nxt = s
                elif _close(s.b, end, tol):
                    nxt = s.reversed()
                else:
                    continue
                used[k] = True
                loop.append(nxt)
                break
            else:
                loop = None
                break
        if loop:
            for k in range(len(loop)):  # snap joints so the wire closes exactly
                nb = loop[(k + 1) % len(loop)]
                loop[k] = Seg(loop[k].a, nb.a, loop[k].center, loop[k].ccw)
            loops.append(loop)
    return loops


def _area(loop: list[Seg]) -> float:
    pts = [p for s in loop for p in s.points()[:-1]]
    return 0.5 * sum(pts[k][0] * pts[k - 1][1] - pts[k - 1][0] * pts[k][1] for k in range(len(pts)))


def _inside(p: Pt, loop: list[Seg]) -> bool:
    pts = [q for s in loop for q in s.points()[:-1]]
    x, y = p
    inside = False
    for k in range(len(pts)):
        (x1, y1), (x2, y2) = pts[k - 1], pts[k]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def outline(segs: list[Seg]) -> tuple[list[Seg], list[list[Seg]]]:
    """Pick the board edge (largest loop) and the loops inside it (cutouts / holes)."""
    loops = [lp for lp in chain(segs) if abs(_area(lp)) > 1e-6]
    if not loops:
        raise GerberError("No closed board outline found. Open the board outline layer "
                          "(Edge.Cuts, .GKO, .GM1 or .GML), not a copper or silkscreen layer.")
    loops.sort(key=lambda lp: abs(_area(lp)), reverse=True)
    outer = loops[0]
    inner = [lp for lp in loops[1:] if _inside(lp[0].mid(), outer)]
    return outer, inner


# ----------------------------------------------------------------------------- solid
def _wire(loop: list[Seg]):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    def P(p):
        return gp_Pnt(p[0], p[1], 0.0)

    w = BRepBuilderAPI_MakeWire()
    for s in loop:
        if s.full_circle:
            cx, cy = s.center
            r = math.hypot(s.a[0] - cx, s.a[1] - cy)
            edge = BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(gp_Pnt(cx, cy, 0), gp_Dir(0, 0, 1)), r)).Edge()
        elif s.center is None:
            edge = BRepBuilderAPI_MakeEdge(P(s.a), P(s.b)).Edge()
        else:
            edge = BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(P(s.a), P(s.mid()), P(s.b)).Value()).Edge()
        w.Add(edge)
    if not w.IsDone():
        raise GerberError("The board outline has gaps or overlaps and can't be closed.")
    return w.Wire()


def board_solid(segs: list[Seg], thickness: float = DEFAULT_THICKNESS):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    outer, inner = outline(segs)
    if _area(outer) < 0:
        outer = [s.reversed() for s in reversed(outer)]
    mk = BRepBuilderAPI_MakeFace(_wire(outer), True)
    for lp in inner:
        if _area(lp) > 0:  # holes run clockwise
            lp = [s.reversed() for s in reversed(lp)]
        mk.Add(_wire(lp))
    if not mk.IsDone():
        raise GerberError("Could not build the board shape from the outline.")
    return BRepPrimAPI_MakePrism(mk.Face(), gp_Vec(0, 0, thickness)).Shape()


def _wrong_layer(path: Path, text: str) -> str | None:
    kind = NOT_OUTLINE_EXTS.get(path.suffix.lower())
    m = re.search(r"%TF\.FileFunction,([A-Za-z]+)", text)
    if m and m.group(1).lower() in ("copper", "soldermask", "legend", "paste", "plated", "nonplated"):
        kind = kind or m.group(1).lower()
    elif m:  # the file says what it is (Profile, Mechanical, Other ...): trust it over the extension
        kind = None
    if kind:
        return (f"{path.name} is the {kind} layer. Open the board outline instead: the .GKO (keep-out) "
                f"or the mechanical layer that holds the board shape (.GM1, .GM2 …), or KiCad's Edge_Cuts.gbr.")
    return None


def load_gerber(path: str | Path, thickness: float = DEFAULT_THICKNESS):
    """Read a Gerber board outline and return the board as a solid (mm), top face up (+Z)."""
    path = Path(path)
    try:
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError as e:
        raise GerberError(f"Could not read {path}: {e}") from e
    wrong = _wrong_layer(path, text)
    if wrong:
        raise GerberError(wrong)
    return board_solid(parse(text), thickness)
