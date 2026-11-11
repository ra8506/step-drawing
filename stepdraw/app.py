"""Desktop GUI."""
from __future__ import annotations

import copy
import json
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QSettings, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog,
    QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .drawing import SCALES, SHEETS, Options, Tol, build_sheet, dim_text
from .export import export, paint
from .gerber import GERBER_EXTS

OPEN_EXTS = (".step", ".stp") + GERBER_EXTS
STEP_FILTER = ("Models (" + " ".join(f"*{e} *{e.upper()}" for e in OPEN_EXTS) + ");;"
               "STEP files (*.step *.stp *.STEP *.STP);;"
               "Gerber board outline (" + " ".join(f"*{e} *{e.upper()}" for e in GERBER_EXTS) + ");;"
               "All files (*)")


# ----------------------------------------------------------------------------- background work
class Worker(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except Exception as e:  # surface any error to the UI
            traceback.print_exc()
            self.failed.emit(str(e) or e.__class__.__name__)


class _Relay(QObject):
    """Lives on the GUI thread, so the worker's results are handled there (Qt widgets are not thread-safe)."""

    def __init__(self, on_done, on_fail, on_progress):
        super().__init__()
        self._done, self._fail, self._progress = on_done, on_fail, on_progress

    @Slot(object)
    def done(self, result):
        self._done(result)

    @Slot(str)
    def failed(self, msg):
        self._fail(msg)

    @Slot(str)
    def progress(self, value):
        if self._progress:
            self._progress(value)


def run_in_thread(owner, fn, on_done, on_fail, on_progress=None):
    """Run fn(progress) on a background thread; the callbacks always run on the GUI thread."""
    th = QThread(owner)
    w = Worker(fn)
    relay = _Relay(on_done, on_fail, on_progress)  # created here, on the GUI thread
    w.moveToThread(th)
    th.started.connect(w.run)
    w.done.connect(relay.done)
    w.failed.connect(relay.failed)
    w.progress.connect(relay.progress)
    w.done.connect(th.quit)
    w.failed.connect(th.quit)
    th.finished.connect(w.deleteLater)
    th.finished.connect(th.deleteLater)
    owner._workers = getattr(owner, "_workers", []) + [(th, w, relay)]
    th.start()


class _Bridge(QObject):
    """Carries worker-process results back onto the GUI thread."""
    done = Signal(object, object)  # tag, future


# ----------------------------------------------------------------------------- preview
class SheetView(QWidget):
    """Sheet preview: scroll to zoom, drag to pan, hover + click a dimension to edit it."""
    dimClicked = Signal(object, object)  # DimRef, global QPoint
    dimDelete = Signal(object)

    def __init__(self):
        super().__init__()
        self.sheet = None
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self._drag = None
        self._moved = False
        self._k = 1.0
        self._org = QPointF(0, 0)
        self.hover = None
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.message = "Open a STEP file or Gerber board outline to start  (File ▸ Open, or drag a file here)"

    def set_sheet(self, sheet, reset=False):
        self.sheet = sheet
        self.hover = None
        if reset:
            self.zoom, self.offset = 1.0, QPointF(0, 0)
        self.update()

    def _fit(self):
        if not self.sheet:
            return 1.0
        return min((self.width() - 20) / self.sheet.width, (self.height() - 20) / self.sheet.height)

    def _to_sheet(self, pos):
        return (pos.x() - self._org.x()) / self._k, self.sheet.height - (pos.y() - self._org.y()) / self._k

    def dim_at(self, pos):
        if not self.sheet:
            return None
        x, y = self._to_sheet(pos)
        pad = max(1.0, 4 / self._k)
        best = None
        for d in self.sheet.dims:
            x0, y0, x1, y1 = d.bbox
            if x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad:
                area = (x1 - x0) * (y1 - y0)
                if best is None or area < best[0]:
                    best = (area, d)
        return best[1] if best else None

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#5b6670"))
        if not self.sheet:
            p.setPen(QColor("white"))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        k = self._fit() * self.zoom
        w, h = self.sheet.width * k, self.sheet.height * k
        x = (self.width() - w) / 2 + self.offset.x()
        y = (self.height() - h) / 2 + self.offset.y()
        self._k, self._org = k, QPointF(x, y)
        p.fillRect(int(x) + 4, int(y) + 4, int(w), int(h), QColor(0, 0, 0, 60))
        p.save()
        p.translate(x, y)
        paint(self.sheet, p, k)
        p.restore()
        if self.hover is not None:
            x0, y0, x1, y1 = self.hover.bbox
            H = self.sheet.height
            p.setPen(QColor(30, 110, 220))
            p.setBrush(QColor(30, 110, 220, 40))
            p.drawRect(int(x + x0 * k - 3), int(y + (H - y1) * k - 3), int((x1 - x0) * k + 6), int((y1 - y0) * k + 6))

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        new = max(0.2, min(40.0, self.zoom * f))
        c = QPointF(self.width() / 2, self.height() / 2) + self.offset
        pos = e.position()
        self.offset += (pos - c) * (1 - new / self.zoom)
        self.zoom = new
        self.update()

    def mousePressEvent(self, e):
        self._drag = e.position()
        self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons():
            delta = e.position() - self._drag
            if self._moved or abs(delta.x()) + abs(delta.y()) > 3:
                self._moved = True
                self.offset += delta
                self._drag = e.position()
                self.update()
            return
        d = self.dim_at(e.position())
        if d is not self.hover:
            self.hover = d
            self.setCursor(Qt.PointingHandCursor if d else Qt.ArrowCursor)
            self.setToolTip("Click to set a tolerance or delete this dimension" if d else "")
            self.update()

    def mouseReleaseEvent(self, e):
        clicked = self._drag is not None and not self._moved
        self._drag = None
        if clicked:
            d = self.dim_at(e.position())
            if d is not None:
                self.dimClicked.emit(d, e.globalPosition().toPoint())

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.hover is not None:
            self.dimDelete.emit(self.hover)
        else:
            super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, _):
        self.zoom, self.offset = 1.0, QPointF(0, 0)
        self.update()


class ToleranceDialog(QDialog):
    KINDS = [("sym", "Symmetric  (±)"), ("dev", "Deviation  (+upper / −lower)"), ("limits", "Limits  (max / min)")]

    def __init__(self, parent, dim, units: str, tol=None):
        super().__init__(parent)
        self.setWindowTitle("Dimension tolerance")
        self.units = units
        k = 25.4 if units == "in" else 1.0
        self.k = k
        f = QFormLayout(self)
        f.addRow(QLabel(f"<b>{dim.label}</b>"))
        self.kind = QComboBox()
        for v, t in self.KINDS:
            self.kind.addItem(t, v)
        self.upper = QDoubleSpinBox()
        self.lower = QDoubleSpinBox()
        for sb in (self.upper, self.lower):
            sb.setDecimals(4 if units == "in" else 3)
            sb.setRange(0, 1000)
            sb.setSingleStep(0.001 if units == "in" else 0.01)
        tol = tol or Tol("sym", 0.1 if units == "mm" else 0.127, 0.1 if units == "mm" else 0.127)
        self.kind.setCurrentIndex([v for v, _ in self.KINDS].index(tol.kind))
        self.upper.setValue(tol.upper / k)
        self.lower.setValue(tol.lower / k)
        f.addRow("Type", self.kind)
        self.lu = QLabel()
        self.ll = QLabel()
        f.addRow(self.lu, self.upper)
        f.addRow(self.ll, self.lower)
        self.preview = QLabel()
        f.addRow("Shows as", self.preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self.dim = dim
        for w in (self.kind,):
            w.currentIndexChanged.connect(self._sync)
        self.upper.valueChanged.connect(self._sync)
        self.lower.valueChanged.connect(self._sync)
        self._sync()

    def tol(self):
        return Tol(self.kind.currentData(), self.upper.value() * self.k, self.lower.value() * self.k)

    def _sync(self, *_):
        kind = self.kind.currentData()
        u = "in" if self.units == "in" else "mm"
        self.lu.setText(f"Tolerance ({u})" if kind == "sym" else f"Upper (+, {u})")
        self.ll.setText(f"Lower (−, {u})")
        self.lower.setVisible(kind != "sym")
        self.ll.setVisible(kind != "sym")
        opt = Options(units=self.units, decimals=3 if self.units == "in" else 2, tolerances={"k": self.tol()})
        main, up, lo = dim_text(self.dim.value, opt, "k")
        self.preview.setText(main if up is None else (f"{up} / {lo}" if main is None else f"{main}  {up} / {lo}"))


# ----------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        self.setAcceptDrops(True)
        self.settings = QSettings("STEP Drawing", "STEP Drawing")
        self.engine = None
        self.gen = 0
        self.fast: dict = {}
        self.pending: set = set()
        self.info_text = f"{APP_NAME} {__version__}"
        self._reset_view = False
        self.bridge = _Bridge()
        self.bridge.done.connect(self._on_result)
        self.path: Path | None = None
        self.cache: dict = {}
        self.sheet = None

        self.edits = self._empty_edits()
        self.undo_stack: list = []
        self.view = SheetView()
        self.view.dimClicked.connect(self.dim_menu)
        self.view.dimDelete.connect(lambda d: self.delete_dim(d))
        self.setCentralWidget(self.view)
        self._build_actions()
        self._build_panel()
        self._load_settings()
        self.statusBar().showMessage(f"{APP_NAME} {__version__}")
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(180)
        self.progress.setTextVisible(False)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancel_work)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_btn)
        self.progress.hide()
        self.cancel_btn.hide()
        self._rebuild_timer = QTimer(self, singleShot=True, interval=150, timeout=self.rebuild)
        if self.settings.value("auto_update", True, type=bool):
            QTimer.singleShot(2500, lambda: self.check_updates(silent=True))

    # --- UI construction
    def _build_actions(self):
        m = self.menuBar()
        fm = m.addMenu("&File")
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)

        def act(text, slot, shortcut=None, menu=fm, toolbar=True):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            menu.addAction(a)
            if toolbar:
                tb.addAction(a)
            return a

        act("Open STEP / Gerber…", self.open_dialog, "Ctrl+O")
        fm.addSeparator()
        tb.addSeparator()
        self.export_actions = [
            act("Export PDF…", lambda: self.export_dialog(".pdf"), "Ctrl+E"),
            act("Export DXF…", lambda: self.export_dialog(".dxf")),
            act("Export SVG…", lambda: self.export_dialog(".svg")),
            act("Export PNG…", lambda: self.export_dialog(".png"), toolbar=False),
        ]
        for a in self.export_actions:
            a.setEnabled(False)
        fm.addSeparator()
        act("Exit", self.close, "Ctrl+Q", toolbar=False)

        em = m.addMenu("&Edit")
        act("Undo dimension change", self.undo, "Ctrl+Z", menu=em, toolbar=False)

        vm = m.addMenu("&View")
        act("Reload part", self.reload, "F5", menu=vm, toolbar=False)
        act("Fit to window", lambda: self.view.mouseDoubleClickEvent(None), "Ctrl+0", menu=vm, toolbar=False)

        hm = m.addMenu("&Help")
        act("Check for updates…", lambda: self.check_updates(silent=False), menu=hm, toolbar=False)
        self.auto_update = QAction("Check for updates at startup", self, checkable=True)
        self.auto_update.toggled.connect(lambda v: self.settings.setValue("auto_update", v))
        hm.addAction(self.auto_update)
        act("About", self.about, menu=hm, toolbar=False)

    def _build_panel(self):
        panel = QWidget()
        lay = QVBoxLayout(panel)

        g = QGroupBox("Sheet")
        f = QFormLayout(g)
        self.sheet_cb = QComboBox()
        self.sheet_cb.addItems(list(SHEETS))
        self.proj_cb = QComboBox()
        self.proj_cb.addItem("Third angle (US / ANSI)", "third")
        self.proj_cb.addItem("First angle (ISO / Europe)", "first")
        self.scale_cb = QComboBox()
        self.scale_cb.addItem("Auto", "auto")
        for _, label in SCALES:
            self.scale_cb.addItem(label, label)
        self.units_cb = QComboBox()
        self.units_cb.addItem("Millimetres", "mm")
        self.units_cb.addItem("Inches", "in")
        self.dec_sb = QSpinBox()
        self.dec_sb.setRange(0, 4)
        f.addRow("Size", self.sheet_cb)
        f.addRow("Projection", self.proj_cb)
        f.addRow("Scale", self.scale_cb)
        f.addRow("Units", self.units_cb)
        f.addRow("Decimals", self.dec_sb)
        lay.addWidget(g)

        g = QGroupBox("Views")
        v = QVBoxLayout(g)
        self.view_cbs = {}
        for key, label in [("front", "Front"), ("top", "Top"), ("right", "Side"), ("iso", "Isometric")]:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self.view_cbs[key] = cb
            v.addWidget(cb)
        self.hidden_cb = QCheckBox("Show hidden lines")
        self.yup_cb = QCheckBox("Model is Y-up (rotate)")
        v.addWidget(self.hidden_cb)
        v.addWidget(self.yup_cb)
        lay.addWidget(g)

        g = QGroupBox("Dimensions")
        v = QVBoxLayout(g)
        self.dim_cbs = {}
        for key, label in [("overall_dims", "Overall size"), ("hole_positions", "Feature positions"),
                           ("hole_dims", "Hole diameters"), ("radius_dims", "Radii"), ("center_marks", "Centre marks")]:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self.dim_cbs[key] = cb
            v.addWidget(cb)
        hint = QLabel("Click a dimension on the drawing to add a tolerance or delete it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666;")
        v.addWidget(hint)
        self.restore_btn = QPushButton("Restore deleted dimensions")
        self.restore_btn.clicked.connect(self.restore_deleted)
        self.clear_tol_btn = QPushButton("Clear all tolerances")
        self.clear_tol_btn.clicked.connect(self.clear_tolerances)
        v.addWidget(self.restore_btn)
        v.addWidget(self.clear_tol_btn)
        f2 = QFormLayout()
        self.general_tol = QLineEdit()
        self.general_tol.setPlaceholderText("e.g. ±0.1")
        f2.addRow("General tolerance", self.general_tol)
        v.addLayout(f2)
        lay.addWidget(g)

        g = QGroupBox("Notes")
        v = QVBoxLayout(g)
        self.confidential_cb = QCheckBox("Confidential / proprietary notice")
        self.confidential_cb.setToolTip("Adds a proprietary statement (uses the Company field) and a "
                                        "CONFIDENTIAL · PROPRIETARY header")
        v.addWidget(self.confidential_cb)
        v.addWidget(QLabel("Drawing notes (one per line):"))
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText("e.g.\nBreak all sharp edges\nDeburr\nFinish: anodise black")
        self.notes_edit.setFixedHeight(90)
        v.addWidget(self.notes_edit)
        lay.addWidget(g)

        g = QGroupBox("Title block")
        f = QFormLayout(g)
        self.fields = {}
        for key, label in [("title", "Title"), ("drawing_no", "Drawing no."), ("material", "Material"),
                           ("drawn_by", "Drawn by"), ("company", "Company"), ("revision", "Revision")]:
            e = QLineEdit()
            self.fields[key] = e
            f.addRow(label, e)
        lay.addWidget(g)
        lay.addStretch(1)

        for w in [self.sheet_cb, self.proj_cb, self.scale_cb, self.units_cb]:
            w.currentIndexChanged.connect(self.schedule_rebuild)
        self.dec_sb.valueChanged.connect(self.schedule_rebuild)
        for cb in list(self.view_cbs.values()) + list(self.dim_cbs.values()) + [self.hidden_cb]:
            cb.toggled.connect(self.schedule_rebuild)
        self.yup_cb.toggled.connect(self.reload)
        for e in self.fields.values():
            e.textChanged.connect(self.schedule_rebuild)
        self.general_tol.textChanged.connect(self.schedule_rebuild)
        self.confidential_cb.toggled.connect(self.schedule_rebuild)
        self.notes_edit.textChanged.connect(self.schedule_rebuild)

        scroll = QScrollArea()
        scroll.setWidget(panel)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(300)
        dock = QDockWidget("Drawing settings", self)
        dock.setWidget(scroll)
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    # --- settings
    def _load_settings(self):
        s = self.settings

        def setcombo(cb, val):
            i = cb.findData(val)
            if i < 0:
                i = cb.findText(val)
            if i >= 0:
                cb.setCurrentIndex(i)

        setcombo(self.sheet_cb, s.value("sheet", "A3"))
        setcombo(self.proj_cb, s.value("projection", "third"))
        setcombo(self.units_cb, s.value("units", "mm"))
        self.dec_sb.setValue(int(s.value("decimals", 1)))
        self.hidden_cb.setChecked(s.value("hidden", True, type=bool))
        for k in ("drawn_by", "company"):
            self.fields[k].setText(s.value(k, ""))
        self.fields["revision"].setText("A")
        self.general_tol.setText(s.value("general_tol", ""))
        self.confidential_cb.setChecked(s.value("confidential", False, type=bool))
        self.notes_edit.setPlainText(s.value("notes", ""))
        self._update_edit_buttons()
        self.auto_update.setChecked(s.value("auto_update", True, type=bool))

    def _save_settings(self):
        s = self.settings
        s.setValue("sheet", self.sheet_cb.currentText())
        s.setValue("projection", self.proj_cb.currentData())
        s.setValue("units", self.units_cb.currentData())
        s.setValue("decimals", self.dec_sb.value())
        s.setValue("hidden", self.hidden_cb.isChecked())
        for k in ("drawn_by", "company"):
            s.setValue(k, self.fields[k].text())
        s.setValue("general_tol", self.general_tol.text())
        s.setValue("confidential", self.confidential_cb.isChecked())
        s.setValue("notes", self.notes_edit.toPlainText())
        self._save_edits()

    def closeEvent(self, e):
        self._stop_engine()
        self._save_settings()
        super().closeEvent(e)

    def options(self) -> Options:
        return Options(
            sheet=self.sheet_cb.currentText(),
            projection=self.proj_cb.currentData(),
            views=tuple(k for k, cb in self.view_cbs.items() if cb.isChecked()),
            hidden_lines=self.hidden_cb.isChecked(),
            units=self.units_cb.currentData(),
            decimals=self.dec_sb.value(),
            scale=self.scale_cb.currentData(),
            **{k: cb.isChecked() for k, cb in self.dim_cbs.items()},
            **{k: e.text().strip() for k, e in self.fields.items()},
            notes=self.notes_edit.toPlainText(),
            confidential=self.confidential_cb.isChecked(),
            general_tol=self.general_tol.text().strip(),
            tolerances=dict(self.edits["tol"]),
            deleted=set(self.edits["deleted"]),
            leader_angles=dict(self.edits["angles"]),
        )

    # --- per-file dimension edits (tolerances, deletions, leader directions)
    @staticmethod
    def _empty_edits():
        return {"tol": {}, "deleted": set(), "angles": {}}

    def _edits_key(self):
        return "edits/" + str(self.path).replace("\\", "/").replace("/", "|") if self.path else None

    def _load_edits(self):
        self.edits = self._empty_edits()
        self.undo_stack.clear()
        key = self._edits_key()
        raw = self.settings.value(key, "") if key else ""
        if raw:
            try:
                d = json.loads(raw)
                self.edits = {"tol": {k: Tol.from_dict(v) for k, v in d.get("tol", {}).items()},
                              "deleted": set(d.get("deleted", [])),
                              "angles": {k: float(v) for k, v in d.get("angles", {}).items()}}
            except Exception:
                pass
        self._update_edit_buttons()

    def _save_edits(self):
        key = self._edits_key()
        if not key:
            return
        e = self.edits
        if not (e["tol"] or e["deleted"] or e["angles"]):
            self.settings.remove(key)
            return
        self.settings.setValue(key, json.dumps({"tol": {k: v.to_dict() for k, v in e["tol"].items()},
                                                "deleted": sorted(e["deleted"]), "angles": e["angles"]}))

    def _change(self, fn):
        self.undo_stack.append(copy.deepcopy(self.edits))
        fn(self.edits)
        self._save_edits()
        self._update_edit_buttons()
        self.rebuild()

    def undo(self):
        if self.undo_stack:
            self.edits = self.undo_stack.pop()
            self._save_edits()
            self._update_edit_buttons()
            self.rebuild()

    def _update_edit_buttons(self):
        n = len(self.edits["deleted"])
        self.restore_btn.setText(f"Restore deleted dimensions ({n})" if n else "Restore deleted dimensions")
        self.restore_btn.setEnabled(bool(n))
        self.clear_tol_btn.setEnabled(bool(self.edits["tol"]))

    def restore_deleted(self):
        self._change(lambda e: e["deleted"].clear())

    def clear_tolerances(self):
        self._change(lambda e: e["tol"].clear())

    def delete_dim(self, d):
        self._change(lambda e: e["deleted"].add(d.key))
        self.statusBar().showMessage(f"Deleted dimension {d.label}  (Ctrl+Z to undo)", 6000)

    def dim_menu(self, d, gpos):
        m = QMenu(self)
        m.addSection(d.label)
        has_tol = d.key in self.edits["tol"]
        m.addAction("Edit tolerance…" if has_tol else "Add tolerance…", lambda: self.edit_tolerance(d))
        if has_tol:
            m.addAction("Remove tolerance", lambda: self._change(lambda e: e["tol"].pop(d.key, None)))
        if d.angle is not None:
            def turn(e):
                e["angles"][d.key] = (d.angle + 90) % 360
            m.addAction("Turn leader", lambda: self._change(turn))
        m.addSeparator()
        m.addAction("Delete dimension", lambda: self.delete_dim(d))
        m.exec(gpos)

    def edit_tolerance(self, d):
        dlg = ToleranceDialog(self, d, self.units_cb.currentData(), self.edits["tol"].get(d.key))
        if dlg.exec() == QDialog.Accepted:
            t = dlg.tol()
            self._change(lambda e: e["tol"].__setitem__(d.key, t))

    # --- loading
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith(OPEN_EXTS):
                self.open_path(p)
                return

    def open_dialog(self):
        start = self.settings.value("last_dir", str(Path.home()))
        p, _ = QFileDialog.getOpenFileName(self, "Open STEP or Gerber file", start, STEP_FILTER)
        if p:
            self.open_path(p)

    def open_path(self, p):
        self.path = Path(p)
        self.settings.setValue("last_dir", str(self.path.parent))
        self.fields["title"].blockSignals(True)
        self.fields["title"].setText(self.path.stem.replace("_", " "))
        self.fields["title"].blockSignals(False)
        self._load_edits()
        self.reload()

    def reload(self):
        """(Re)open the current file: heavy work runs in worker processes so the window never freezes."""
        if not self.path:
            return
        from .worker import Engine

        self._stop_engine()
        self.gen += 1
        path = self.path
        self.engine = Engine(path, self.yup_cb.isChecked())
        self.cache.clear()
        self.fast.clear()
        self.pending.clear()
        self.sheet = None
        self._reset_view = True
        for a in self.export_actions:
            a.setEnabled(False)
        self.view.message = f"Reading {path.name}…"
        self.view.set_sheet(None)
        self.setWindowTitle(f"{path.name} – {APP_NAME}")
        self._set_busy(f"Reading {path.name}…")
        self._watch(self.engine.info(), ("info", self.gen, None))
        self.rebuild()

    def _stop_engine(self):
        if self.engine is not None:
            self.engine.stop()
            self.engine = None

    def _watch(self, fut, tag):
        fut.add_done_callback(lambda f: self.bridge.done.emit(tag, f))

    def _on_result(self, tag, fut):
        kind, gen, key = tag
        if gen != self.gen or fut.cancelled():
            return
        self.pending.discard((kind, key))
        try:
            res = fut.result()
        except Exception as e:  # worker error or killed process
            if self.engine is None:
                return
            self._stop_engine()
            self._fail(f"Could not process this file:\n{e}" if str(e) else "Could not process this file.")
            return
        if kind == "info":
            sx, sy, sz = res["size"]
            self.info_text = (f"{self.path.name}  ·  {res['solids']} solid(s)  ·  "
                              f"{sx:.1f} × {sy:.1f} × {sz:.1f} mm")
            self.view.message = f"Projecting views of {self.path.name}…"
            self.view.update()
        elif kind == "fast":
            self.fast[key] = res
        else:
            self.cache[key] = res
        self.rebuild()

    def _fail(self, msg):
        self._set_busy(None)
        self.view.message = "Could not open this file."
        self.view.set_sheet(None)
        self.statusBar().showMessage("Error")
        QMessageBox.critical(self, APP_NAME, msg)

    def cancel_work(self):
        self._stop_engine()
        self.gen += 1
        self._set_busy(None)
        if self.sheet is None:
            self.view.message = "Cancelled."
            self.view.set_sheet(None)
        self.statusBar().showMessage("Cancelled. Showing the quick preview without dimensions. "
                                     "Press F5 to try again.", 15000)

    def _set_busy(self, text, done=0, total=0):
        if text is None:
            self.progress.hide()
            self.cancel_btn.hide()
            self.statusBar().showMessage(self.info_text)
            return
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.progress.show()
        self.cancel_btn.show()
        self.statusBar().showMessage(text)

    # --- drawing
    def schedule_rebuild(self, *_):
        if self.engine is not None:
            self._rebuild_timer.start()

    def _view_keys(self, opt):
        out = []
        for n in opt.views:
            src = "left" if (n == "right" and opt.projection == "first") else n
            out.append((n, (src, opt.hidden_lines)))
        return out

    def rebuild(self, reset_view=False):
        if self.engine is None:
            return
        opt = self.options()
        if not opt.views:
            self.view.set_sheet(None)
            return
        keys = self._view_keys(opt)
        missing = [k for _, k in keys if k not in self.cache]
        # quick previews first (they finish in a fraction of a second), then the exact views
        for k in missing:
            if k not in self.fast and ("fast", k) not in self.pending:
                self.pending.add(("fast", k))
                self._watch(self.engine.project(k[0], k[1], fast=True), ("fast", self.gen, k))
        for k in missing:
            if ("exact", k) not in self.pending:
                self.pending.add(("exact", k))
                self._watch(self.engine.project(k[0], k[1]), ("exact", self.gen, k))

        reset = reset_view or self._reset_view
        name = self.path.stem if self.path else ""
        if not missing:
            self.sheet = build_sheet({n: self.cache[k] for n, k in keys}, opt, name)
            self.view.set_sheet(self.sheet, reset=reset)
            self._reset_view = False
            for a in self.export_actions:
                a.setEnabled(True)
            self._set_busy(None)
            return
        if all(k in self.cache or k in self.fast for _, k in keys):
            preview = copy.copy(opt)
            for f in ("overall_dims", "hole_dims", "hole_positions", "radius_dims", "center_marks"):
                setattr(preview, f, False)
            views = {n: self.cache.get(k) or self.fast[k] for n, k in keys}
            self.sheet = build_sheet(views, preview, name)
            self.sheet.dims = []  # not editable until the exact views are in
            self.view.set_sheet(self.sheet, reset=reset)
            self._reset_view = False
            for a in self.export_actions:
                a.setEnabled(False)
        done = len(keys) - len(missing)
        msg = (f"Preview shown. Working out exact views and dimensions ({done}/{len(keys)})…" if self.sheet
               else f"Reading {self.path.name if self.path else 'part'}…")
        self._set_busy(msg, done, len(keys))

    # --- export
    def export_dialog(self, ext):
        if not self.sheet:
            return
        start = str((self.path.with_suffix(ext)) if self.path else Path.home() / f"drawing{ext}")
        filt = {".pdf": "PDF (*.pdf)", ".dxf": "DXF (*.dxf)", ".svg": "SVG (*.svg)", ".png": "PNG image (*.png)"}[ext]
        p, _ = QFileDialog.getSaveFileName(self, "Export drawing", start, filt)
        if not p:
            return
        if not p.lower().endswith(ext):
            p += ext
        try:
            export(self.sheet, p)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Export failed:\n{e}")
            return
        self.statusBar().showMessage(f"Saved {p}", 8000)

    # --- updates
    def check_updates(self, silent: bool):
        from . import updater

        def work(progress):
            return updater.check()

        def done(rel):
            if rel is None:
                if not silent:
                    QMessageBox.information(self, APP_NAME, f"You have the latest version ({__version__}).")
                return
            notes = (rel.notes or "").strip()
            box = QMessageBox(self)
            box.setWindowTitle("Update available")
            box.setText(f"{APP_NAME} {rel.version} is available (you have {__version__}).")
            if notes:
                box.setDetailedText(notes)
            box.setInformativeText("Install it now? The app will restart.")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.button(QMessageBox.Yes).setText("Update now")
            box.button(QMessageBox.No).setText("Later")
            if box.exec() == QMessageBox.Yes:
                self.install_update(rel)

        def fail(msg):
            if silent:
                return
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setIcon(QMessageBox.Information)
            box.setText(f"You have version {__version__}. The app couldn't check for a newer one automatically.")
            box.setInformativeText("Open the releases page on GitHub (sign in if asked) and run the latest Setup.exe "
                                   "to update. Your settings are kept.")
            box.setStandardButtons(QMessageBox.Open | QMessageBox.Close)
            if box.exec() == QMessageBox.Open:
                QDesktopServices.openUrl(QUrl(updater.RELEASES_PAGE))

        run_in_thread(self, work, done, fail)

    def install_update(self, rel):
        from . import updater

        dlg = QProgressDialog("Downloading update…", "Cancel", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setAutoClose(False)
        dlg.show()

        def work(progress):
            return updater.download(rel, lambda d, t: progress(f"{int(d * 100 / t) if t else 0}"))

        def done(path):
            dlg.close()
            self._save_settings()
            updater.run_installer(path)
            QApplication.quit()

        def fail(msg):
            dlg.close()
            QMessageBox.warning(self, APP_NAME, f"Update failed:\n{msg}")

        run_in_thread(self, work, done, fail, lambda pct: dlg.setValue(int(pct)))

    def about(self):
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br><br>Turns STEP models and Gerber board outlines into dimensioned 2D drawings."
            "<br>Geometry kernel: Open CASCADE Technology (LGPL).")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    icon = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent)) / "assets" / "icon.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    w = MainWindow()
    w.show()
    args = [a for a in sys.argv[1:] if a.lower().endswith(OPEN_EXTS)]
    if args:
        QTimer.singleShot(0, lambda: w.open_path(args[0]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
