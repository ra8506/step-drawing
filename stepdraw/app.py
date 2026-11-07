"""Desktop GUI."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog, QFormLayout, QGroupBox,
    QLineEdit, QMainWindow, QMessageBox, QProgressDialog, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .drawing import SCALES, SHEETS, Options, build_sheet
from .export import export, paint

STEP_FILTER = "STEP files (*.step *.stp *.STEP *.STP);;All files (*)"


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


def run_in_thread(owner, fn, on_done, on_fail, on_progress=None):
    th = QThread(owner)
    w = Worker(fn)
    w.moveToThread(th)
    th.started.connect(w.run)
    w.done.connect(on_done)
    w.failed.connect(on_fail)
    if on_progress:
        w.progress.connect(on_progress)
    w.done.connect(th.quit)
    w.failed.connect(th.quit)
    th.finished.connect(w.deleteLater)
    th.finished.connect(th.deleteLater)
    owner._workers = getattr(owner, "_workers", []) + [(th, w)]
    th.start()


# ----------------------------------------------------------------------------- preview
class SheetView(QWidget):
    def __init__(self):
        super().__init__()
        self.sheet = None
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        self._drag = None
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.message = "Open a STEP file to start  (File ▸ Open, or drag a .step file here)"

    def set_sheet(self, sheet, reset=False):
        self.sheet = sheet
        if reset:
            self.zoom, self.offset = 1.0, QPointF(0, 0)
        self.update()

    def _fit(self):
        if not self.sheet:
            return 1.0
        return min((self.width() - 20) / self.sheet.width, (self.height() - 20) / self.sheet.height)

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
        p.fillRect(int(x) + 4, int(y) + 4, int(w), int(h), QColor(0, 0, 0, 60))
        p.translate(x, y)
        paint(self.sheet, p, k)

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        new = max(0.2, min(40.0, self.zoom * f))
        # zoom about the cursor
        c = QPointF(self.width() / 2, self.height() / 2) + self.offset
        pos = e.position()
        self.offset += (pos - c) * (1 - new / self.zoom)
        self.zoom = new
        self.update()

    def mousePressEvent(self, e):
        self._drag = e.position()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons():
            self.offset += e.position() - self._drag
            self._drag = e.position()
            self.update()

    def mouseReleaseEvent(self, _):
        self._drag = None

    def mouseDoubleClickEvent(self, _):
        self.zoom, self.offset = 1.0, QPointF(0, 0)
        self.update()


# ----------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1400, 900)
        self.setAcceptDrops(True)
        self.settings = QSettings("STEP Drawing", "STEP Drawing")
        self.shape = None
        self.path: Path | None = None
        self.cache: dict = {}
        self.sheet = None

        self.view = SheetView()
        self.setCentralWidget(self.view)
        self._build_actions()
        self._build_panel()
        self._load_settings()
        self.statusBar().showMessage(f"{APP_NAME} {__version__}")
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

        act("Open STEP…", self.open_dialog, "Ctrl+O")
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

        vm = m.addMenu("&View")
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

    def closeEvent(self, e):
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
        )

    # --- loading
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p.lower().endswith((".step", ".stp")):
                self.open_path(p)
                return

    def open_dialog(self):
        start = self.settings.value("last_dir", str(Path.home()))
        p, _ = QFileDialog.getOpenFileName(self, "Open STEP file", start, STEP_FILTER)
        if p:
            self.open_path(p)

    def open_path(self, p):
        self.path = Path(p)
        self.settings.setValue("last_dir", str(self.path.parent))
        self.fields["title"].blockSignals(True)
        self.fields["title"].setText(self.path.stem.replace("_", " "))
        self.fields["title"].blockSignals(False)
        self.reload()

    def reload(self):
        if not self.path:
            return
        from .geometry import load_step, shape_info

        path, yup = self.path, self.yup_cb.isChecked()
        self.view.message = f"Loading {path.name}…"
        self.view.set_sheet(None)
        self.statusBar().showMessage(f"Loading {path.name}…")

        def work(progress):
            shape = load_step(path, y_up=yup)
            return shape, shape_info(shape)

        def done(res):
            self.shape, info = res
            self.cache.clear()
            sx, sy, sz = info["size"]
            self.statusBar().showMessage(
                f"{path.name}  ·  {info['solids']} solid(s)  ·  {sx:.1f} × {sy:.1f} × {sz:.1f} mm")
            self.setWindowTitle(f"{path.name} – {APP_NAME}")
            self.rebuild(reset_view=True)

        run_in_thread(self, work, done, self._fail)

    def _fail(self, msg):
        self.view.message = "Could not open this file."
        self.view.set_sheet(None)
        self.statusBar().showMessage("Error")
        QMessageBox.critical(self, APP_NAME, msg)

    # --- drawing
    def schedule_rebuild(self, *_):
        if self.shape is not None:
            self._rebuild_timer.start()

    def rebuild(self, reset_view=False):
        if self.shape is None:
            return
        from .geometry import project

        opt = self.options()
        if not opt.views:
            self.view.set_sheet(None)
            return
        needed = []
        for n in opt.views:
            src = "left" if (n == "right" and opt.projection == "first") else n
            key = (src, opt.hidden_lines)
            if key not in self.cache:
                needed.append((n, key))

        def finish():
            views = {}
            for n in opt.views:
                src = "left" if (n == "right" and opt.projection == "first") else n
                views[n] = self.cache[(src, opt.hidden_lines)]
            self.sheet = build_sheet(views, opt, self.path.stem if self.path else "")
            self.view.set_sheet(self.sheet, reset=reset_view)
            for a in self.export_actions:
                a.setEnabled(True)

        if not needed:
            finish()
            return

        shape = self.shape
        self.statusBar().showMessage("Projecting views…")

        def work(progress):
            out = {}
            for n, (src, hidden) in needed:
                progress(f"Projecting {n} view…")
                out[(src, hidden)] = project(shape, src, with_hidden=hidden)
            return out

        def done(res):
            self.cache.update(res)
            self.statusBar().clearMessage()
            finish()

        run_in_thread(self, work, done, self._fail, lambda m: self.statusBar().showMessage(m))

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
            if not silent:
                QMessageBox.warning(self, APP_NAME, f"Could not check for updates:\n{msg}")

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
            f"<b>{APP_NAME}</b> {__version__}<br><br>Turns STEP models into dimensioned 2D drawings."
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
    args = [a for a in sys.argv[1:] if a.lower().endswith((".step", ".stp"))]
    if args:
        QTimer.singleShot(0, lambda: w.open_path(args[0]))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
