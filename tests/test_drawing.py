import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from stepdraw.drawing import Options, Text, make_drawing  # noqa: E402
from stepdraw.geometry import load_step, project  # noqa: E402

SAMPLES = Path(__file__).parent / "samples"


@pytest.fixture(scope="session", autouse=True)
def qapp():
    from PySide6.QtGui import QGuiApplication

    return QGuiApplication.instance() or QGuiApplication([])


def labels(name, **kw):
    sheet = make_drawing(load_step(SAMPLES / f"{name}.step"), Options(**kw), name)
    return sheet, {e.text for e in sheet.entities if isinstance(e, Text)}


def test_plate_dimensions():
    sheet, t = labels("plate")
    assert {"120", "80", "10", "4× Ø8", "Ø30", "15", "60", "105", "40", "65"} <= t
    assert sheet.scale_label == "1:1"


def test_bracket_dimensions():
    _, t = labels("bracket")
    assert {"100", "60", "50", "2× Ø10", "Ø16", "8", "30", "52"} <= t


def test_shaft_dimensions():
    _, t = labels("shaft")
    assert {"Ø40", "Ø24", "Ø12", "80", "40", "30"} <= t


def test_fillets_and_hidden_depth():
    _, t = labels("filleted")
    assert {"R8", "Ø20", "Ø10", "10", "90", "50", "20"} <= t


def test_inches():
    _, t = labels("plate", units="in")
    assert "4.724" in t  # 120 mm


def test_hidden_lines_not_drawn_over_visible():
    v = project(load_step(SAMPLES / "plate.step"), "front")
    assert len(v.hidden) == 10  # 5 holes x 2 silhouettes; back edges removed


@pytest.mark.parametrize("ext", [".pdf", ".svg", ".dxf", ".png"])
def test_exports(tmp_path, ext):
    from stepdraw.export import export

    sheet, _ = labels("bracket", projection="first", sheet="A4")
    out = tmp_path / f"b{ext}"
    export(sheet, out)
    assert out.stat().st_size > 1000


def test_dxf_is_valid(tmp_path):
    import ezdxf

    from stepdraw.export import save_dxf

    sheet, _ = labels("shaft")
    save_dxf(sheet, tmp_path / "s.dxf")
    doc = ezdxf.readfile(tmp_path / "s.dxf")
    assert not doc.audit().has_errors


def test_version_compare():
    from stepdraw.updater import is_newer

    assert is_newer("0.2.0", "0.1.9")
    assert is_newer("v1.0.0", "0.9.9")
    assert not is_newer("0.1.0", "0.1.0")


def test_tolerances_and_deletion():
    from stepdraw.drawing import Tol

    tols = {"front:overall:h": Tol("sym", 0.05, 0.05), "front:overall:v": Tol("dev", 0.2, 0.1),
            "top:dia:4.000": Tol("limits", 0.02, 0.01)}
    sheet, t = labels("plate", tolerances=tols, deleted={"top:pos:h:105.000"})
    assert "120 ±0.05" in t
    assert {"+0.2", "-0.1", "4× Ø8.02", "4× Ø7.99"} <= t
    assert "105" not in t
    keys = {d.key for d in sheet.dims}
    assert "top:pos:h:105.000" not in keys and "front:overall:h" in keys


def test_notes_and_confidential():
    _, t = labels("plate", notes="Break sharp edges\n\nDeburr", confidential=True, company="Acme",
                  general_tol="±0.1")
    assert {"NOTES:", "1.", "2.", "BREAK SHARP EDGES", "DEBURR", "CONFIDENTIAL · PROPRIETARY"} <= t
    assert any("ACME" in x for x in t)
    assert any("TOLERANCES ±0.1" in x for x in t)


def test_worker_processes():
    from stepdraw.worker import Engine

    eng = Engine(SAMPLES / "plate.step", False, workers=1)
    try:
        assert eng.info().result(timeout=120)["solids"] == 1
        fast = eng.project("top", True, fast=True).result(timeout=120)
        exact = eng.project("top", True).result(timeout=120)
        assert fast.visible and not fast.circles
        assert len([c for c in exact.circles if c.full]) == 5
    finally:
        eng.stop()


@pytest.mark.parametrize("sheet_size", ["A4", "A3", "ANSI B"])
def test_confidential_notice_fits_its_box(sheet_size):
    from stepdraw.drawing import Polyline, text_width

    sheet, _ = labels("plate", confidential=True, sheet=sheet_size, company="A Very Long Company Name Incorporated")
    lines = [e for e in sheet.entities if isinstance(e, Text) and e.max_width]
    box = [e for e in sheet.entities if isinstance(e, Polyline) and e.closed and e.layer == "TITLE"][0]
    right = max(p[0] for p in box.pts)
    assert lines
    for t in lines:
        assert t.x + min(text_width(t.text, t.height), t.max_width) <= right - 1.0


# ----------------------------------------------------------------------------- Gerber import
def test_gerber_board_drawing():
    from stepdraw.geometry import load_model, shape_info

    shape = load_model(SAMPLES / "board_outline.gbr")
    sx, sy, sz = shape_info(shape)["size"]
    assert (round(sx, 3), round(sy, 3), round(sz, 3)) == (100, 60, 1.6)
    sheet = make_drawing(shape, Options(), "board")
    t = {e.text for e in sheet.entities if isinstance(e, Text)}
    assert {"100", "60", "1.6", "4× Ø3.2", "R2", "5", "95"} <= t


def test_gerber_inches_trailing_zeros_and_single_quadrant():
    from stepdraw.gerber import board_solid, parse
    from stepdraw.geometry import shape_info

    # 2 x 1 inch board, %FSTAX24Y24% (trailing zeros omitted, so X019 = 1.9 in), legacy G74 corner arc
    src = """%FSTAX24Y24*%%MOIN*%%ADD10C,0.01*%D10*G01*
    X0Y0D02*X019Y0D01*G74*G03*X02Y001I0J001D01*G01*X02Y01D01*X0Y01D01*X0Y0D01*M02*"""
    segs = parse(src)
    assert segs[0].b == pytest.approx((1.9 * 25.4, 0.0))
    assert segs[1].center == pytest.approx((1.9 * 25.4, 0.1 * 25.4))
    sx, sy, _ = shape_info(board_solid(segs, 1.0))["size"]
    assert abs(sx - 50.8) < 1e-3 and abs(sy - 25.4) < 1e-3


def test_gerber_errors():
    from stepdraw.gerber import GerberError, outline, parse

    with pytest.raises(GerberError, match="no %FS"):
        parse("hello*")
    open_path = parse("%FSLAX26Y26*%%MOMM*%X0Y0D02*X1000000Y0D01*X1000000Y1000000D01*M02*")
    with pytest.raises(GerberError, match="board outline layer"):
        outline(open_path)


def test_cli_gerber(tmp_path):
    from stepdraw.cli import main

    out = tmp_path / "board.dxf"
    assert main([str(SAMPLES / "board_outline.gbr"), "-o", str(out), "--board-thickness", "0.8"]) == 0
    assert out.stat().st_size > 1000
