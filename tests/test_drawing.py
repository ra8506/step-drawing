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
