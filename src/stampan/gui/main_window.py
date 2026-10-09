"""The main window (SPEC §12): library tree + sheet panel on the left,
image viewer (with its ROI toolbar underneath) in the centre, and the
stamp-level context panel (Detection/Perforation/Colour/Stamp tabs) on the
right."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
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
    QStackedWidget,
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
from stampan.gui.library_tree import (
    KIND_PROJECT,
    KIND_REFERENCE,
    KIND_SAMPLE,
    KIND_STAMP,
    LibraryTree,
)
from stampan.gui.panels import (
    ColourOverviewPanel,
    ColourPanel,
    PerforationPanel,
    ReferenceManagerPanel,
    ReferenceWizardPanel,
    SheetPanel,
    SheetStampsPanel,
    StampPanel,
)
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

#: For debugging convenience only: when running from a source checkout (not
#: an installed package), default straight into the same test library the
#: test suite uses instead of the normal empty ~/StampAnalyzer, so there's
#: something to look at without a manual "Open library..." + project click
#: every time. Falls back to DEFAULT_LIBRARY_ROOT when this doesn't exist
#: (an installed app, or a checkout without testdata/ populated).
_DEV_LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "testdata" / "_library"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Stamp Analyzer")
        self.resize(1400, 900)

        self.library_root: Path = (
            _DEV_LIBRARY_ROOT if _DEV_LIBRARY_ROOT.is_dir() else DEFAULT_LIBRARY_ROOT
        )
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
        self._tree_dock = QDockWidget("Library", self)
        self._tree_dock.setWidget(left_container)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._tree_dock)

        # -- right: stamp-level context tabs, or the sheet's stamp list --------
        self.stamp_panel = StampPanel(lambda: self.library_root, self._begin_edit_box_on_sheet)
        self.perforation_panel = PerforationPanel(lambda: self.library_root, self.viewer)
        self.colour_panel = ColourPanel(lambda: self.library_root, self.viewer)
        self.sheet_stamps_panel = SheetStampsPanel(lambda: self.library_root, self.viewer)
        self.colour_overview_panel = ColourOverviewPanel(lambda: self.library_root)
        self.reference_manager_panel = ReferenceManagerPanel(lambda: self.library_root)
        self.reference_wizard_panel = ReferenceWizardPanel(lambda: self.library_root, self.viewer)
        for panel in (self.perforation_panel, self.colour_panel, self.stamp_panel):
            panel.changed.connect(self._reload_tree)
        self.sheet_stamps_panel.changed.connect(self._reload_tree)
        self.reference_manager_panel.changed.connect(self._reload_tree)
        self.reference_wizard_panel.changed.connect(self._reload_tree)

        self._context_tabs = QTabWidget()
        self._context_tabs.addTab(self.stamp_panel, "Stamp")
        self._context_tabs.addTab(self.perforation_panel, "Perforation")
        self._context_tabs.addTab(self.colour_panel, "Colour")
        self._context_tabs.currentChanged.connect(self._on_context_tab_changed)

        # Which stamp these three tabs are about is otherwise only visible
        # in the library tree's own (possibly scrolled-out-of-view)
        # selection -- state it here too.
        self._stamp_context_container = QWidget()
        stamp_context_container = self._stamp_context_container
        stamp_context_layout = QVBoxLayout(stamp_context_container)
        stamp_context_layout.setContentsMargins(0, 0, 0, 0)
        stamp_context_layout.setSpacing(2)
        self.current_stamp_label = QLabel("")
        self.current_stamp_label.setStyleSheet("font-weight: bold; padding: 2px 4px;")
        stamp_context_layout.addWidget(self.current_stamp_label)
        stamp_context_layout.addWidget(self._context_tabs)

        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(stamp_context_container)  # index 0: a selected stamp
        self._right_stack.addWidget(self.sheet_stamps_panel)  # index 1: a selected sample
        self._right_stack.addWidget(self.colour_overview_panel)  # index 2: a selected project
        self._right_stack.addWidget(self.reference_manager_panel)  # index 3: a selected reference
        self._right_stack.addWidget(self.reference_wizard_panel)  # index 4: "New reference..."
        self._panel_dock = QDockWidget("Context", self)
        self._panel_dock.setWidget(self._right_stack)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._panel_dock)

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

        # Both side docks defaulted to a roughly equal 1/3 share each,
        # leaving the viewer (the actual working area -- the image) smaller
        # than either -- give it the lion's share instead. A hard cap, not
        # just resizeDocks's initial-split hint: resizeDocks only suggests a
        # starting size, and Qt's own layout pass can still grow a dock
        # back out to fit its content's minimum size hint (observed: the
        # panel dock ending up *wider* than the whole window). A fixed
        # maximum guarantees the viewer keeps at least an equal share
        # regardless of what the panels inside want to be.
        _SIDE_DOCK_MAX_WIDTH = 380
        self._tree_dock.setMaximumWidth(_SIDE_DOCK_MAX_WIDTH)
        self._panel_dock.setMaximumWidth(_SIDE_DOCK_MAX_WIDTH)

        self._reload_tree()
        QTimer.singleShot(0, self._size_docks)

    def _size_docks(self) -> None:
        side_width = min(self._tree_dock.maximumWidth(), max(self.width() // 4, 260))
        self.resizeDocks(
            [self._tree_dock, self._panel_dock],
            [side_width, side_width],
            Qt.Orientation.Horizontal,
        )
        # The cap was only to stop the initial layout pass from growing a
        # dock back out to its content's minimum size hint -- once the
        # split above has actually taken, let the user drag it wider again
        # if they want to.
        QTimer.singleShot(0, self._release_dock_width_cap)

    def _release_dock_width_cap(self) -> None:
        self._tree_dock.setMaximumWidth(16777215)
        self._panel_dock.setMaximumWidth(16777215)

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

        new_reference_action = toolbar.addAction("New reference...")
        new_reference_action.triggered.connect(self._new_reference)

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

    def _new_reference(self) -> None:
        self._right_stack.setCurrentWidget(self.reference_wizard_panel)

    def _reload_tree(self) -> None:
        self.tree.reload(self.library_root)
        self._select_first_project_if_none_selected()

    def _select_first_project_if_none_selected(self) -> None:
        if self._current_project_id is not None or self.tree.topLevelItemCount() < 2:
            return
        projects_root = self.tree.topLevelItem(1)
        if projects_root.childCount() == 0:
            return
        self.tree.setCurrentItem(projects_root.child(0))

    # -- selection -------------------------------------------------------

    def _on_tree_selection(self, kind: str, project_id: str, item_id: str) -> None:
        if kind != KIND_SAMPLE:
            self.sheet_stamps_panel.deactivate()
        if kind == KIND_PROJECT:
            self._current_project_id = project_id
            self.sheet_panel.set_project(project_id)
            self._right_stack.setCurrentWidget(self.colour_overview_panel)
            self.colour_overview_panel.set_project(project_id)
        elif kind == KIND_SAMPLE:
            self._current_project_id = project_id
            self.sheet_panel.set_project(project_id)
            self._load_sample_image(project_id, item_id)
            self._right_stack.setCurrentWidget(self.sheet_stamps_panel)
            self.sheet_stamps_panel.set_sample(project_id, item_id)
        elif kind == KIND_REFERENCE:
            self._right_stack.setCurrentWidget(self.reference_manager_panel)
            self.reference_manager_panel.refresh(select_ref_id=item_id)
        elif kind == KIND_STAMP:
            self._current_project_id = project_id
            self._current_stamp_id = item_id
            self.sheet_panel.set_project(project_id)
            self._right_stack.setCurrentWidget(self._stamp_context_container)
            self.current_stamp_label.setText(f"Stamp: {item_id}")
            self.perforation_panel.set_stamp(project_id, item_id)
            self.colour_panel.set_stamp(project_id, item_id)
            self.stamp_panel.set_stamp(project_id, item_id)
            self._load_stamp_image(project_id, item_id)

    def _begin_edit_box_on_sheet(self, project_id: str | None, stamp_id: str | None) -> None:
        """The Stamp tab's "Edit bounding box on sheet...": switch to that
        stamp's sample in the tree (which shows the sheet and its stamp
        list) and start editing this one's box there."""
        if project_id is None or stamp_id is None:
            return
        sample_id = find_sample_for_stamp(self.library_root, project_id, stamp_id)
        if sample_id is None:
            return
        self.tree.select_sample(project_id, sample_id)
        self.sheet_stamps_panel.select_and_edit(stamp_id)

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
        panels = (
            self.sheet_panel,
            self.perforation_panel,
            self.colour_panel,
            self.stamp_panel,
            self.sheet_stamps_panel,
            self.colour_overview_panel,
            self.reference_manager_panel,
            self.reference_wizard_panel,
        )
        for panel in panels:
            worker = getattr(panel, "_worker", None)
            if worker is not None and worker.isRunning():
                worker.wait(10000)
        if getattr(self, "_report_worker", None) is not None and self._report_worker.isRunning():
            self._report_worker.wait(10000)
        super().closeEvent(event)
