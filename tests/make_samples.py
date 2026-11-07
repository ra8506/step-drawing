"""Generate sample STEP files used by the tests (run once; outputs go to tests/samples)."""
from pathlib import Path

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

OUT = Path(__file__).parent / "samples"


def write(shape, name):
    OUT.mkdir(exist_ok=True)
    w = STEPControl_Writer()
    w.Transfer(shape, STEPControl_AsIs)
    assert w.Write(str(OUT / name)) == IFSelect_RetDone


def cyl(x, y, z, r, h, d=(0, 0, 1)):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(*d)), r, h).Shape()


def plate():
    s = BRepPrimAPI_MakeBox(120.0, 80.0, 10.0).Shape()
    for x, y in [(15, 15), (105, 15), (15, 65), (105, 65)]:
        s = BRepAlgoAPI_Cut(s, cyl(x, y, -1, 4, 12)).Shape()
    s = BRepAlgoAPI_Cut(s, cyl(60, 40, -1, 15, 12)).Shape()
    return s


def bracket():
    base = BRepPrimAPI_MakeBox(100.0, 60.0, 8.0).Shape()
    wall = BRepPrimAPI_MakeBox(gp_Pnt(0, 52, 0), 100.0, 8.0, 50.0).Shape()
    s = BRepAlgoAPI_Fuse(base, wall).Shape()
    s = BRepAlgoAPI_Cut(s, cyl(25, 25, -1, 5, 10)).Shape()
    s = BRepAlgoAPI_Cut(s, cyl(75, 25, -1, 5, 10)).Shape()
    s = BRepAlgoAPI_Cut(s, cyl(50, 50, 30, 8, 12, d=(0, 1, 0))).Shape()
    return s


def shaft():
    s = cyl(0, 0, 0, 20, 30)
    s = BRepAlgoAPI_Fuse(s, cyl(0, 0, 30, 12, 50)).Shape()
    s = BRepAlgoAPI_Cut(s, cyl(0, 0, -1, 6, 90)).Shape()
    return s


if __name__ == "__main__":
    write(plate(), "plate.step")
    write(bracket(), "bracket.step")
    write(shaft(), "shaft.step")
    print("written to", OUT)


def filleted():
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    s = BRepPrimAPI_MakeBox(90.0, 50.0, 20.0).Shape()
    mk = BRepFilletAPI_MakeFillet(s)
    exp = TopExp_Explorer(s, TopAbs_EDGE)
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    while exp.More():
        e = TopoDS.Edge(exp.Current())
        c = BRepAdaptor_Curve(e)
        a, b = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        if abs(a.X() - b.X()) < 1e-6 and abs(a.Y() - b.Y()) < 1e-6:  # vertical edges
            mk.Add(8.0, e)
        exp.Next()
    s = mk.Shape()
    s = BRepAlgoAPI_Cut(s, cyl(45, 25, 10, 10, 11)).Shape()  # blind pocket
    s = BRepAlgoAPI_Cut(s, cyl(45, 25, -1, 5, 30)).Shape()
    return s


if __name__ == "__main__":
    write(filleted(), "filleted.step")
