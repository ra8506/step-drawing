"""Command line: stepdraw-cli part.step -o part.pdf [-o part.dxf] [--sheet A3] ..."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv=None) -> int:
    from .drawing import SHEETS, Options, make_drawing
    from .geometry import load_step

    ap = argparse.ArgumentParser(prog="stepdraw-cli", description="Make a 2D drawing from a STEP file")
    ap.add_argument("step")
    ap.add_argument("-o", "--out", action="append", help="output file (.pdf .svg .dxf .png); repeatable")
    ap.add_argument("--sheet", default="A3", choices=list(SHEETS))
    ap.add_argument("--projection", default="third", choices=["third", "first"])
    ap.add_argument("--units", default="mm", choices=["mm", "in"])
    ap.add_argument("--no-hidden", action="store_true")
    ap.add_argument("--no-iso", action="store_true")
    ap.add_argument("--y-up", action="store_true", help="model uses Y as the up axis")
    ap.add_argument("--title", default="")
    ap.add_argument("--company", default="")
    ap.add_argument("--note", action="append", default=[], help="drawing note; repeatable")
    ap.add_argument("--confidential", action="store_true", help="add the proprietary / confidential notice")
    ap.add_argument("--general-tol", default="", help='general tolerance, e.g. "±0.1"')
    a = ap.parse_args(argv)

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    from .export import export

    shape = load_step(a.step, y_up=a.y_up)
    views = ("front", "top", "right") + (() if a.no_iso else ("iso",))
    opt = Options(sheet=a.sheet, projection=a.projection, units=a.units, hidden_lines=not a.no_hidden,
                  views=views, title=a.title, company=a.company, notes="\n".join(a.note),
                  confidential=a.confidential, general_tol=a.general_tol)
    sheet = make_drawing(shape, opt, Path(a.step).stem)
    outs = a.out or [str(Path(a.step).with_suffix(".pdf"))]
    for o in outs:
        export(sheet, o)
        if sys.stdout:  # None under pythonw
            print("wrote", o, "scale", sheet.scale_label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
