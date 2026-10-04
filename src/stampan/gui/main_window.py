"""The main window (SPEC §12): library tree + sheet panel on the left,
image viewer (with its ROI toolbar underneath) in the centre, and the
stamp-level context panel (Detection/Perforation/Colour/Stamp tabs) on the
right."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from stampan.cli import DEFAULT_LIBRARY_ROOT
from stampan.core import project as project_mod
from stampan.core.calibration import reference as ref_mod
from stampan.gui._util import find_sample_for_stamp
from stampan.gui.library_tree import KIND_PROJECT, KIND_SAMPLE, KIND_STAMP, LibraryTree
from stampan.gui.panels import ColourPanel, PerforationPanel, SheetPanel, StampPanel
from stampan.gui.viewer import (
    MODE_ERASE,
    MODE_INCLUDE,
    TOOL_BRUSH,
    TOOL_ELLIPSE,
    TOOL_PAN,
    TOOL_PERF_BOX,
    TOOL_POLYGON,
    TOOL_RECT,
    ImageViewer,
)
from stampan.gui.workers import Worker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Stamp Analyzer")
        self.resize(1400, 900)

        self.library_root: Path = DEFAULT_LIBRARY_ROOT
        self._current_project_id: str | None = None
        self._current_stamp_id: str | None = None

        self.viewer = ImageViewer()
        self.viewer.pixel_info.connect(self._on_pixel_info)
        self.setCentralWidget(self._build_viewer_area())

        # -- left: library tree + sheet-level panel --------------------------
        self.tree = LibraryTree()
        self.tree.selection_changed.connect(self._on_tree_selection)
        self.sheet_panel = SheetPanel(lambda: self.library_root)
        self.sheet_panel.changed.connect(self._reload_tree)

        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.tree, stretch=3)
        left_layout.addWidget(self.sheet_panel, stretch=1)
        tree_dock = QDockWidget("Library", self)
        tree_dock.setWidget(left_container)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, tree_dock)

        # -- right: stamp-level context tabs ---------------------------------
        self.stamp_panel = StampPanel(
            lambda: self.library_root,
            self.viewer,
            lambda: self._load_stamp_image(self._current_project_id, self._current_stamp_id),
        )
        self.perforation_panel = PerforationPanel(lambda: self.library_root, self.viewer)
        self.colour_panel = ColourPanel(lambda: self.library_root, self.viewer)
        for panel in (self.perforation_panel, self.colour_panel, self.stamp_panel):
            panel.changed.connect(self._reload_tree)

        self._context_tabs = QTabWidget()
        self._context_tabs.addTab(self.stamp_panel, "Stamp")
        self._context_tabs.addTab(self.perforation_panel, "Perforation")
        self._context_tabs.addTab(self.colour_panel, "Colour")
        self._context_tabs.currentChanged.connect(self._on_context_tab_changed)
        panel_dock = QDockWidget("Context", self)
        panel_dock.setWidget(self._context_tabs)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, panel_dock)

        self._build_toolbar()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.pixel_readout = QLabel("")
        self.status_bar.addPermanentWidget(self.pixel_readout)
        self.report_progress = QProgressBar()
        self.report_progress.setRange(0, 0)  # indeterminate: we don't have a per-stamp count yet
        self.report_progress.setMaximumWidth(140)
        self.report_progress.setVisible(False)
        self.status_bar.addPermanentWidget(self.report_progress)

        self._reload_tree()

    # -- viewer + its own ROI toolbar, directly underneath -----------------

    def _build_viewer_area(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.viewer, stretch=1)

        roi_bar = QWidget()
        roi_layout = QHBoxLayout(roi_bar)
        roi_layout.setContentsMargins(4, 2, 4, 2)

        roi_layout.addWidget(QLabel("ROI:"))
        self.tool_combo = QComboBox()
        self.tool_combo.addItems(["Pan", "Rectangle", "Ellipse", "Polygon", "Brush"])
        self.tool_combo.currentTextChanged.connect(self._on_tool_changed)
        roi_layout.addWidget(self.tool_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Include", "Erase"])
        self.mode_combo.currentTextChanged.connect(
            lambda text: self.viewer.set_mode(MODE_INCLUDE if text == "Include" else MODE_ERASE)
        )
        roi_layout.addWidget(self.mode_combo)

        undo_button = QPushButton("Undo")
        undo_button.clicked.connect(self.viewer.undo)
        redo_button = QPushButton("Redo")
        redo_button.clicked.connect(self.viewer.redo)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.viewer.clear_mask)
        roi_layout.addWidget(undo_button)
        roi_layout.addWidget(redo_button)
        roi_layout.addWidget(clear_button)
        roi_layout.addStretch(1)

        layout.addWidget(roi_bar)
        return container

    def _on_tool_changed(self, text: str) -> None:
        tool = {
            "Pan": TOOL_PAN,
            "Rectangle": TOOL_RECT,
            "Ellipse": TOOL_ELLIPSE,
            "Polygon": TOOL_POLYGON,
            "Brush": TOOL_BRUSH,
        }[text]
        self.viewer.set_tool(tool)

    def _on_context_tab_changed(self, index: int) -> None:
        widget = self._context_tabs.widget(index)
        is_perforation = widget is self.perforation_panel
        if widget is not self.stamp_panel and self.stamp_panel._editing_box:
            # Leaving the Stamp tab mid bounding-box edit: the edit shows
            # the sample sheet in place of the stamp crop other tabs
            # expect, so it can't be left dangling underneath them.
            self.stamp_panel._cancel_box_edit()
        if is_perforation:
            self.perforation_panel.activate()
            self.viewer.set_tool(TOOL_PERF_BOX)
        else:
            self.perforation_panel.deactivate()
            self._on_tool_changed(self.tool_combo.currentText())
        self.tool_combo.setEnabled(not is_perforation)
        self.mode_combo.setEnabled(not is_perforation)

    # -- top toolbar: project/library-wide actions --------------------------

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        self.addToolBar(toolbar)

        open_action = toolbar.addAction("Open library...")
        open_action.triggered.connect(self._choose_library_root)

        new_project_action = toolbar.addAction("New project...")
        new_project_action.triggered.connect(self._new_project)

        toolbar.addSeparator()
        report_action = toolbar.addAction("Generate report")
        report_action.triggered.connect(self._generate_report)

    # -- library -------------------------------------------------------

    def _choose_library_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Library root", str(self.library_root))
        if path:
            self.library_root = Path(path)
            self._reload_tree()

    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        if ok and name:
            project_mod.get_or_create_project(self.library_root, name)
            self._reload_tree()

    def _reload_tree(self) -> None:
        self.tree.reload(self.library_root)

    # -- selection -------------------------------------------------------

    def _on_tree_selection(self, kind: str, project_id: str, item_id: str) -> None:
        if kind == KIND_PROJECT:
            self._current_project_id = project_id
            self.sheet_panel.set_project(project_id)
        elif kind == KIND_SAMPLE:
            self._current_project_id = project_id
            self.sheet_panel.set_project(project_id)
            self._load_sample_image(project_id, item_id)
        elif kind == KIND_STAMP:
            self._current_project_id = project_id
            self._current_stamp_id = item_id
            self.sheet_panel.set_project(project_id)
            self.perforation_panel.set_stamp(project_id, item_id)
            self.colour_panel.set_stamp(project_id, item_id)
            self.stamp_panel.set_stamp(project_id, item_id)
            self._load_stamp_image(project_id, item_id)

    def _load_stamp_image(self, project_id: str, stamp_id: str) -> None:
        crop = project_mod.load_stamp_crop(self.library_root, project_id, stamp_id)
        px_per_mm_x = px_per_mm_y = None
        predict_lab_fn = None
        try:
            sample_id = find_sample_for_stamp(self.library_root, project_id, stamp_id)
            sample = project_mod.load_sample(self.library_root, project_id, sample_id)
            ref = ref_mod.load_reference(self.library_root, sample.ref_id)
            if ref.scale is not None:
                px_per_mm_x, px_per_mm_y = ref.scale.px_per_mm_x, ref.scale.px_per_mm_y
            if ref.colour_model is not None:
                predict_lab_fn = ref_mod.load_colour_model(
                    self.library_root, sample.ref_id
                ).predict_lab
        except Exception:
            pass  # pixel readout just won't have mm/Lab; the image itself still loads
        self.viewer.set_image(crop, px_per_mm_x, px_per_mm_y, predict_lab_fn)

    def _load_sample_image(self, project_id: str, sample_id: str) -> None:
        # Selecting a sample shows the whole sheet, for sheet-level work
        # (SPEC §12) -- not the detection overview diagnostic, the real scan.
        sample = project_mod.load_sample(self.library_root, project_id, sample_id)
        scan_image, _meta = project_mod.load_sample_scan(self.library_root, project_id, sample)
        px_per_mm_x = px_per_mm_y = None
        try:
            ref = ref_mod.load_reference(self.library_root, sample.ref_id)
            if ref.scale is not None:
                px_per_mm_x, px_per_mm_y = ref.scale.px_per_mm_x, ref.scale.px_per_mm_y
        except Exception:
            pass
        self.viewer.set_image(scan_image, px_per_mm_x, px_per_mm_y)

    def _on_pixel_info(self, info) -> None:
        if info is None:
            self.pixel_readout.setText("")
            return
        text = f"px ({info['x']}, {info['y']})"
        if info["mm"]:
            text += f"  mm ({info['mm'][0]:.2f}, {info['mm'][1]:.2f})"
        text += f"  RGB {info['rgb']}"
        if info["lab"]:
            lab = info["lab"]
            text += f"  Lab ({lab[0]:.1f}, {lab[1]:.1f}, {lab[2]:.1f})"
        self.pixel_readout.setText(text)

    # -- report -------------------------------------------------------

    def _generate_report(self) -> None:
        if self._current_project_id is None:
            QMessageBox.information(self, "Report", "Select a project first.")
            return
        project_id = self._current_project_id

        def do_report(progress=None):
            from stampan.core.report import generator as report_mod

            if progress:
                progress("building report...")
            project = project_mod.load_project(self.library_root, project_id)
            context = report_mod.build_report_context(self.library_root, project)
            if not context.stamps:
                raise RuntimeError("no stamps with a usable reference in this project")
            if progress:
                progress("rendering PDF...")
            html = report_mod.render_html(context)
            pdf_bytes = report_mod.render_pdf(html)
            project_dir = project_mod.project_dir(self.library_root, project_id)
            pdf_path = project_dir / "report.pdf"
            pdf_path.write_bytes(pdf_bytes)
            (project_dir / "report.csv").write_text(
                report_mod.export_csv(context), encoding="utf-8"
            )
            (project_dir / "report.json").write_text(
                report_mod.export_json(context), encoding="utf-8"
            )
            if context.colour_overview_image:
                import base64

                (project_dir / "colour_overview.png").write_bytes(
                    base64.b64decode(context.colour_overview_image.split(",", 1)[1])
                )
            return pdf_path

        self.report_progress.setVisible(True)
        self.status_bar.showMessage("Generating report...")
        worker = Worker(do_report)
        worker.signals.progress.connect(self.status_bar.showMessage)
        worker.signals.finished.connect(self._on_report_done)
        worker.signals.failed.connect(self._on_report_failed)
        self._report_worker = worker  # keep a reference so it isn't garbage-collected
        worker.start()

    def _on_report_done(self, path) -> None:
        self.report_progress.setVisible(False)
        self.status_bar.clearMessage()
        QMessageBox.information(self, "Report", f"Wrote {path}")

    def _on_report_failed(self, traceback_text: str) -> None:
        self.report_progress.setVisible(False)
        self.status_bar.clearMessage()
        QMessageBox.critical(self, "Report failed", traceback_text)

    # -- shutdown -------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt override
        """A background ``Worker`` (``QThread``) still running when its last
        Python reference is dropped gets destroyed mid-run, which aborts the
        whole process (same hazard ``_BusyPanel._run`` guards against for
        overlapping runs, SPEC §12) -- closing the window while one is still
        in flight would hit exactly that. Give each panel's worker a moment
        to finish first."""
        panels = (self.sheet_panel, self.perforation_panel, self.colour_panel, self.stamp_panel)
        for panel in panels:
            worker = getattr(panel, "_worker", None)
            if worker is not None and worker.isRunning():
                worker.wait(10000)
        if getattr(self, "_report_worker", None) is not None and self._report_worker.isRunning():
            self._report_worker.wait(10000)
        super().closeEvent(event)
