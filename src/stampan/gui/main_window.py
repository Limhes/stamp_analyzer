"""The main window (SPEC §12).

Library on the left, the image in the centre with its own tool bar, and an
inspector on the right that shows the page for whatever is selected in the
library: a project, a sheet, a stamp or a calibration reference. All
measurement work goes through the pages; this window only does navigation,
menus, the viewer's tools and the status bar.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from stampan import __version__
from stampan.core import project as project_mod
from stampan.core import workflow
from stampan.gui import icons, library_actions, preferences, theme
from stampan.gui.dialogs.reference_wizard import ReferenceWizard
from stampan.gui.library_tree import (
    KIND_PROJECT,
    KIND_REFERENCE,
    KIND_SAMPLE,
    KIND_STAMP,
    LibraryTree,
)
from stampan.gui.pages.base import AppContext, Page
from stampan.gui.pages.project_page import ProjectPage
from stampan.gui.pages.reference_page import ReferencePage
from stampan.gui.pages.sheet_page import SheetPage
from stampan.gui.pages.stamp_page import MODE_COLOUR_AREA, StampPage
from stampan.gui.tasks import hub
from stampan.gui.viewer import (
    MODE_ERASE,
    MODE_INCLUDE,
    TOOL_BRUSH,
    TOOL_ELLIPSE,
    TOOL_PAN,
    TOOL_POLYGON,
    TOOL_RECT,
    ImageViewer,
)
from stampan.gui.widgets import EmptyState, button

#: When running from a source checkout with test data present, start in the
#: test library unless the user has chosen a library before.
_DEV_LIBRARY_ROOT = Path(__file__).resolve().parents[3] / "testdata" / "_library"

_AREA_TOOLS = (TOOL_RECT, TOOL_ELLIPSE, TOOL_POLYGON, TOOL_BRUSH)


class WelcomePage(Page):
    def __init__(self, ctx: AppContext, window: MainWindow) -> None:
        super().__init__(ctx)
        self.header.set_text("Stamp Analyzer", "Calibrated measurement of postage stamps")
        empty = EmptyState(
            "stamp",
            "Nothing selected",
            "Select a project, sheet, stamp or reference in the library. "
            "To start, calibrate a reference from an IT8 scan, then create a project "
            "and add sample sheets to it.",
        )
        empty.add_action(button("New Reference…", "target", slot=window.new_reference))
        empty.add_action(button("New Project…", "folder-plus", "primary", slot=window.new_project))
        self.body_layout.addWidget(empty)
        self.body_layout.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings()
        saved_root = preferences.library_root()
        if saved_root is not None:
            self.library_root = saved_root
        elif _DEV_LIBRARY_ROOT.is_dir():
            self.library_root = _DEV_LIBRARY_ROOT
        else:
            self.library_root = workflow.DEFAULT_LIBRARY_ROOT
        self.library_root.mkdir(parents=True, exist_ok=True)

        self.viewer = ImageViewer()
        self.ctx = AppContext(lambda: self.library_root, self.viewer, self.navigate)
        self._current: Page | None = None

        self._build_centre()
        self._build_library_dock()
        self._build_inspector_dock()
        self._build_actions()
        self._build_menus()
        self._build_toolbars()
        self._build_status_bar()

        self.resize(1480, 920)
        geometry = self.settings.value("window/geometry")
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        state = self.settings.value("window/state")
        if isinstance(state, QByteArray):
            self.restoreState(state)

        self._reload_tree()
        self._show_page(self.welcome_page)
        self._update_title()

    # -- layout --------------------------------------------------------------

    def _build_centre(self) -> None:
        self.viewer_toolbar = QToolBar("Image Tools")
        self.viewer_toolbar.setObjectName("ImageTools")
        self.viewer_toolbar.setMovable(False)
        self.viewer_empty = EmptyState(
            "sheet", "No image", "Select a sheet, stamp or reference to view it here.", dark=True
        )
        self.centre_stack = QStackedWidget()
        self.centre_stack.addWidget(self.viewer_empty)
        self.centre_stack.addWidget(self.viewer)
        centre = QWidget()
        layout = QVBoxLayout(centre)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.viewer_toolbar)
        layout.addWidget(self.centre_stack, stretch=1)
        self.setCentralWidget(centre)
        self.viewer.pixel_info.connect(self._on_pixel_info)
        self.viewer.zoom_changed.connect(self._on_zoom_changed)

    def _build_library_dock(self) -> None:
        self.tree = LibraryTree()
        self.tree.selection_changed.connect(self._on_tree_selection)
        self.tree.customContextMenuRequested.connect(self._tree_context_menu)
        self.library_dock = QDockWidget("Library", self)
        self.library_dock.setObjectName("LibraryDock")
        self.library_dock.setWidget(self.tree)
        self.library_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.library_dock)

    def _build_inspector_dock(self) -> None:
        self.welcome_page = WelcomePage(self.ctx, self)
        self.project_page = ProjectPage(self.ctx)
        self.sheet_page = SheetPage(self.ctx)
        self.stamp_page = StampPage(self.ctx, self.tree.sibling_stamp)
        self.reference_page = ReferencePage(self.ctx)
        self.pages = QStackedWidget()
        for page in (
            self.welcome_page,
            self.project_page,
            self.sheet_page,
            self.stamp_page,
            self.reference_page,
        ):
            self.pages.addWidget(page)
            page.changed.connect(self._reload_tree)
        self.stamp_page.mode_changed.connect(self._on_stamp_mode)
        self.stamp_page.edit_rectangle_requested.connect(self.sheet_page.select_and_edit)
        self.reference_page.new_reference_requested.connect(self.new_reference)

        self.inspector_dock = QDockWidget("Inspector", self)
        self.inspector_dock.setObjectName("InspectorDock")
        self.inspector_dock.setWidget(self.pages)
        self.inspector_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.inspector_dock.setMinimumWidth(400)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.inspector_dock)
        self.resizeDocks([self.library_dock], [270], Qt.Orientation.Horizontal)
        self.resizeDocks([self.inspector_dock], [460], Qt.Orientation.Horizontal)

    def _action(self, text, icon_name=None, slot=None, shortcut=None, tip=None, checkable=False):
        action = QAction(text, self)
        if icon_name:
            action.setIcon(icons.icon(icon_name))
        if slot is not None:
            action.triggered.connect(slot)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setStatusTip(tip)
            action.setToolTip(f"{tip} ({action.shortcut().toString()})" if shortcut else tip)
        action.setCheckable(checkable)
        return action

    def _build_actions(self) -> None:
        a = self._action
        self.act_new_project = a(
            "New Project…",
            "folder-plus",
            self.new_project,
            "Ctrl+N",
            "Create a project for a group of stamps",
        )
        self.act_new_reference = a(
            "New Reference…",
            "target",
            self.new_reference,
            "Ctrl+Shift+N",
            "Calibrate a reference from an IT8 target scan",
        )
        self.act_add_sheet = a(
            "Add Sheet…",
            "import",
            self._add_sheet,
            "Ctrl+I",
            "Scan or import a sample sheet into the current project",
        )
        self.act_open_library = a(
            "Open Library…", "library", self.open_library, "Ctrl+O", "Choose the library folder"
        )
        self.act_show_folder = a("Show Library Folder", "folder", self._show_library_folder)
        self.act_quit = a("Quit", "quit", self.close, QKeySequence.StandardKey.Quit)

        self.act_report = a(
            "Generate Report",
            "report",
            self._generate_report,
            "Ctrl+R",
            "Write the project's PDF report with CSV/JSON export",
        )
        self.act_open_report = a("Open Last Report", "open-external", self._open_report)
        self.act_remeasure = a(
            "Re-measure Perforation…",
            "run",
            self._remeasure,
            tip="Measure the perforation of every stamp in the project again",
        )

        self.act_prev = a(
            "Previous Stamp", "chevron-left", lambda: self.stamp_page.step(-1), "Alt+Left"
        )
        self.act_next = a(
            "Next Stamp", "chevron-right", lambda: self.stamp_page.step(+1), "Alt+Right"
        )
        self.act_reload = a("Reload Library", "refresh", self._reload_tree, "F5")

        self.act_zoom_in = a(
            "Zoom In",
            "zoom-in",
            lambda: self.viewer.zoom_by(1.25),
            QKeySequence.StandardKey.ZoomIn,
            "Zoom in",
        )
        self.act_zoom_out = a(
            "Zoom Out",
            "zoom-out",
            lambda: self.viewer.zoom_by(0.8),
            QKeySequence.StandardKey.ZoomOut,
            "Zoom out",
        )
        self.act_fit = a(
            "Fit to Window", "fit", self.viewer.fit_whole_image, "Ctrl+0", "Fit the whole image"
        )
        self.act_actual = a(
            "Actual Pixels",
            "actual-size",
            self.viewer.zoom_actual_size,
            "Ctrl+1",
            "One image pixel per screen pixel",
        )

        self.tool_group = QActionGroup(self)
        self.tool_actions: dict[str, QAction] = {}
        for tool, text, icon_name, key in (
            (TOOL_PAN, "Pan", "pan", "H"),
            (TOOL_RECT, "Rectangle", "rect", "R"),
            (TOOL_ELLIPSE, "Ellipse", "ellipse", "E"),
            (TOOL_POLYGON, "Polygon", "polygon", "P"),
            (TOOL_BRUSH, "Brush", "brush", "B"),
        ):
            action = a(text, icon_name, None, key, f"{text} tool", checkable=True)
            action.triggered.connect(lambda _c=False, t=tool: self.viewer.set_tool(t))
            self.tool_group.addAction(action)
            self.tool_actions[tool] = action

        self.mode_group = QActionGroup(self)
        self.act_include = a(
            "Add to Area",
            "include",
            lambda: self.viewer.set_mode(MODE_INCLUDE),
            "+",
            "Shapes add to the measurement area",
            checkable=True,
        )
        self.act_erase = a(
            "Erase from Area",
            "erase",
            lambda: self.viewer.set_mode(MODE_ERASE),
            "-",
            "Shapes remove from the measurement area",
            checkable=True,
        )
        self.mode_group.addAction(self.act_include)
        self.mode_group.addAction(self.act_erase)
        self.act_include.setChecked(True)

        self.act_undo = a(
            "Undo",
            "undo",
            self.viewer.undo,
            QKeySequence.StandardKey.Undo,
            "Undo the last area edit",
        )
        self.act_redo = a("Redo", "redo", self.viewer.redo, QKeySequence.StandardKey.Redo, "Redo")
        self.act_clear = a(
            "Clear Area", "clear", self.viewer.clear_mask, None, "Clear the whole measurement area"
        )
        self.area_actions = [
            *(self.tool_actions[t] for t in _AREA_TOOLS),
            self.act_include,
            self.act_erase,
            self.act_undo,
            self.act_redo,
            self.act_clear,
        ]

        self.act_rename_project = a("Rename Project…", "edit", self._rename_project)
        self.act_remove_project = a("Remove Project…", "clear", self._remove_project)
        # F2 / Delete act on whatever is selected in the library.
        self.act_rename_item = a("Rename", None, self._rename_selected, "F2")
        self.act_remove_item = a(
            "Remove", None, self._remove_selected, QKeySequence.StandardKey.Delete
        )
        for act in (self.act_rename_item, self.act_remove_item):
            act.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            self.tree.addAction(act)
        self.act_preferences = a(
            "Preferences…",
            "edit",
            self.open_preferences,
            QKeySequence.StandardKey.Preferences,
            "Library folder and scanner settings",
        )
        self.act_about = a("About Stamp Analyzer", "about", self._about)

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        for act in (
            self.act_new_project,
            self.act_new_reference,
            None,
            self.act_add_sheet,
            None,
            self.act_open_library,
            self.act_show_folder,
            None,
            self.act_quit,
        ):
            file_menu.addSeparator() if act is None else file_menu.addAction(act)

        edit_menu = bar.addMenu("&Edit")
        for act in (self.act_undo, self.act_redo, self.act_clear):
            edit_menu.addAction(act)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_preferences)

        view_menu = bar.addMenu("&View")
        for act in (
            self.act_zoom_in,
            self.act_zoom_out,
            self.act_fit,
            self.act_actual,
            None,
            self.act_prev,
            self.act_next,
            None,
            self.act_reload,
        ):
            view_menu.addSeparator() if act is None else view_menu.addAction(act)
        view_menu.addSeparator()
        view_menu.addAction(self.library_dock.toggleViewAction())
        view_menu.addAction(self.inspector_dock.toggleViewAction())

        tools_menu = bar.addMenu("&Tools")
        for act in self.tool_actions.values():
            tools_menu.addAction(act)
        tools_menu.addSeparator()
        tools_menu.addAction(self.act_include)
        tools_menu.addAction(self.act_erase)

        project_menu = bar.addMenu("&Project")
        for act in (
            self.act_add_sheet,
            self.act_remeasure,
            None,
            self.act_report,
            self.act_open_report,
        ):
            project_menu.addSeparator() if act is None else project_menu.addAction(act)
        project_menu.addSeparator()
        project_menu.addAction(self.act_rename_project)
        project_menu.addAction(self.act_remove_project)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self.act_about)

    def _build_toolbars(self) -> None:
        main = QToolBar("Main")
        main.setObjectName("MainToolbar")
        main.setMovable(False)
        main.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for act in (
            self.act_new_project,
            self.act_new_reference,
            None,
            self.act_add_sheet,
            self.act_report,
        ):
            main.addSeparator() if act is None else main.addAction(act)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, main)

        tb = self.viewer_toolbar
        for act in self.tool_actions.values():
            tb.addAction(act)
        tb.addSeparator()
        tb.addAction(self.act_include)
        tb.addAction(self.act_erase)
        self.brush_spin = QSpinBox()
        self.brush_spin.setRange(1, 200)
        self.brush_spin.setValue(12)
        self.brush_spin.setSuffix(" px")
        self.brush_spin.setToolTip("Brush radius (mouse wheel to change)")
        self.brush_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.brush_spin.setFixedWidth(64)
        self.brush_spin.valueChanged.connect(self.viewer.set_brush_radius)
        self.viewer.set_brush_radius(self.brush_spin.value())
        self.brush_spin_action = tb.addWidget(self.brush_spin)
        tb.addSeparator()
        for act in (self.act_undo, self.act_redo, self.act_clear):
            tb.addAction(act)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.act_zoom_out)
        self.zoom_label = QLabel("—")
        self.zoom_label.setMinimumWidth(52)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tb.addWidget(self.zoom_label)
        tb.addAction(self.act_zoom_in)
        tb.addAction(self.act_fit)
        tb.addAction(self.act_actual)

    def _build_status_bar(self) -> None:
        bar = self.statusBar()
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(120)
        self.busy.setMaximumHeight(14)
        self.busy.setVisible(False)
        self.busy_label = QLabel("")
        self.pixel_readout = QLabel("")
        theme.set_role(self.pixel_readout, "mono")
        bar.addWidget(self.busy)
        bar.addWidget(self.busy_label)
        bar.addPermanentWidget(self.pixel_readout)
        hub().activity.connect(self._on_activity)

    # -- navigation -------------------------------------------------------------

    def navigate(self, kind: str, project_id: str, item_id: str) -> None:
        if self.tree.selected_key() == (kind, project_id, item_id):
            self._on_tree_selection(kind, project_id, item_id)
        elif not self.tree.select_key(kind, project_id, item_id):
            self._show_page(self.welcome_page)

    def _on_tree_selection(self, kind: str, project_id: str, item_id: str) -> None:
        try:
            if kind == KIND_PROJECT:
                self._show_page(self.project_page)
                self.project_page.set_project(project_id)
            elif kind == KIND_SAMPLE:
                self._show_page(self.sheet_page)
                self.sheet_page.set_sample(project_id, item_id)
            elif kind == KIND_STAMP:
                self._show_page(self.stamp_page)
                self.stamp_page.set_stamp(project_id, item_id)
            elif kind == KIND_REFERENCE:
                self._show_page(self.reference_page)
                self.reference_page.set_reference(item_id)
            else:
                self._show_page(self.welcome_page)
        except (OSError, ValueError, KeyError) as exc:
            self._show_page(self.welcome_page)
            QMessageBox.warning(self, "Could not open item", f"{type(exc).__name__}: {exc}")
        self._sync_viewer_ui()

    def _show_page(self, page: Page) -> None:
        if self._current is not None and self._current is not page:
            self._current.deactivate()
        if page in (self.welcome_page, self.project_page):
            self.viewer.clear_image()
        self._current = page
        self.pages.setCurrentWidget(page)
        self._sync_viewer_ui()

    def _current_project_id(self) -> str | None:
        key = self.tree.selected_key()
        return key[1] or None if key else None

    def _sync_viewer_ui(self) -> None:
        has_image = self.viewer.has_image()
        self.centre_stack.setCurrentWidget(self.viewer if has_image else self.viewer_empty)
        area = self._current is self.stamp_page and self._stamp_mode == MODE_COLOUR_AREA
        for act in self.area_actions:
            act.setEnabled(area)
        self.brush_spin_action.setEnabled(area)
        self.tool_actions[TOOL_PAN].setEnabled(has_image)
        for act in (self.act_zoom_in, self.act_zoom_out, self.act_fit, self.act_actual):
            act.setEnabled(has_image)
        tool = self.viewer.tool()
        if tool in self.tool_actions:
            self.tool_actions[tool].setChecked(True)
        else:  # the perforation/sheet rectangle: no tool button represents it
            checked = self.tool_group.checkedAction()
            if checked is not None:
                self.tool_group.setExclusive(False)
                checked.setChecked(False)
                self.tool_group.setExclusive(True)
        on_stamp = self._current is self.stamp_page
        self.act_prev.setEnabled(on_stamp)
        self.act_next.setEnabled(on_stamp)
        has_project = self._current_project_id() is not None
        for act in (
            self.act_add_sheet,
            self.act_report,
            self.act_open_report,
            self.act_remeasure,
            self.act_rename_project,
            self.act_remove_project,
        ):
            act.setEnabled(has_project)

    _stamp_mode = ""

    def _on_stamp_mode(self, mode: str) -> None:
        self._stamp_mode = mode
        if mode == MODE_COLOUR_AREA:
            if self.viewer.tool() == TOOL_PAN:
                self.viewer.set_tool(TOOL_RECT)
            self.act_include.setChecked(True)
            self.viewer.set_mode(MODE_INCLUDE)
        elif self.viewer.tool() in _AREA_TOOLS:
            self.viewer.set_tool(TOOL_PAN)
        self._sync_viewer_ui()

    def _reload_tree(self) -> None:
        self.tree.reload(self.library_root)
        self._sync_viewer_ui()

    # -- library-level actions -----------------------------------------------

    def open_library(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open Library", str(self.library_root))
        if path:
            self.set_library(Path(path))

    def set_library(self, path: Path) -> None:
        if self._current is not None:
            self._current.deactivate()
        path.mkdir(parents=True, exist_ok=True)
        self.library_root = path
        preferences.set_library_root(path)
        self.tree.clearSelection()
        self._reload_tree()
        self._show_page(self.welcome_page)
        self._update_title()

    def open_preferences(self) -> None:
        dialog = preferences.PreferencesDialog(self.library_root, self)
        if dialog.exec() and dialog.library_path() != self.library_root:
            self.set_library(dialog.library_path())

    def new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New Project", "Project name:")
        if not ok or not name.strip():
            return
        project = project_mod.get_or_create_project(self.library_root, name.strip())
        self._reload_tree()
        self.navigate(KIND_PROJECT, project.project_id, "")

    def new_reference(self) -> None:
        wizard = ReferenceWizard(self.library_root, self)
        wizard.saved.connect(self._on_reference_saved)
        wizard.exec()

    def _on_reference_saved(self, ref_id: str) -> None:
        self._reload_tree()
        self.navigate(KIND_REFERENCE, "", ref_id)

    def _show_library_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library_root)))

    # -- project actions: forwarded to the project page ------------------------

    def _with_project_page(self):
        project_id = self._current_project_id()
        if project_id is None:
            return None
        if self._current is not self.project_page:
            self.navigate(KIND_PROJECT, project_id, "")
        return self.project_page

    def _add_sheet(self) -> None:
        if (page := self._with_project_page()) is not None:
            page.add_sheet()

    def _generate_report(self) -> None:
        if (page := self._with_project_page()) is not None:
            page.generate_report()

    def _open_report(self) -> None:
        if (page := self._with_project_page()) is not None:
            page.open_report()

    def _remeasure(self) -> None:
        if (page := self._with_project_page()) is not None:
            page.remeasure_perforation()

    # -- context menu ----------------------------------------------------------

    def _tree_context_menu(self, pos) -> None:
        key = self.tree.item_key_at(pos)
        menu = QMenu(self)
        if key is None:
            menu.addAction(self.act_new_project)
            menu.addAction(self.act_new_reference)
        else:
            kind, project_id, item_id = key
            if kind in (KIND_PROJECT, KIND_SAMPLE, KIND_STAMP):
                self.navigate(kind, project_id, item_id)
            if kind == KIND_PROJECT:
                menu.addAction(self.act_add_sheet)
                menu.addAction(self.act_remeasure)
                menu.addSeparator()
                menu.addAction(self.act_report)
                menu.addAction(self.act_open_report)
                menu.addSeparator()
                menu.addAction(
                    icons.icon("edit"), "Rename Project…", self.project_page.rename_project
                )
                menu.addAction(
                    icons.icon("clear"), "Remove Project…", self.project_page.remove_project
                )
            elif kind == KIND_SAMPLE:
                menu.addAction(self.act_add_sheet)
                menu.addSeparator()
                menu.addAction(icons.icon("edit"), "Rename Sheet…", self.sheet_page.rename_sheet)
                menu.addAction(icons.icon("clear"), "Remove Sheet…", self.sheet_page.remove_sheet)
            elif kind == KIND_STAMP:
                menu.addAction(self.act_prev)
                menu.addAction(self.act_next)
                menu.addSeparator()
                menu.addAction(
                    icons.icon("clear"),
                    "Remove Stamp…",
                    lambda: self._remove_stamp(project_id, item_id),
                )
            elif kind == KIND_REFERENCE:
                self.navigate(kind, project_id, item_id)
                menu.addAction(self.act_new_reference)
            else:
                menu.addAction(self.act_new_project)
                menu.addAction(self.act_new_reference)
        menu.addSeparator()
        menu.addAction(self.act_reload)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _rename_project(self) -> None:
        if (page := self._with_project_page()) is not None:
            page.rename_project()

    def _remove_project(self) -> None:
        if (page := self._with_project_page()) is not None:
            page.remove_project()

    def _rename_selected(self) -> None:
        key = self.tree.selected_key()
        if key is None:
            return
        kind = key[0]
        if kind == KIND_PROJECT:
            self.project_page.rename_project()
        elif kind == KIND_SAMPLE:
            self.sheet_page.rename_sheet()
        elif kind == KIND_REFERENCE:
            self.reference_page.rename()

    def _remove_selected(self) -> None:
        key = self.tree.selected_key()
        if key is None:
            return
        kind, project_id, item_id = key
        if kind == KIND_PROJECT:
            self.project_page.remove_project()
        elif kind == KIND_SAMPLE:
            self.sheet_page.remove_sheet()
        elif kind == KIND_STAMP:
            self._remove_stamp(project_id, item_id)

    def _remove_stamp(self, project_id: str, stamp_id: str) -> None:
        sample_id = project_mod.find_sample_for_stamp(self.library_root, project_id, stamp_id)
        if self._current is not None:
            self._current.deactivate()  # save pending edits while the stamp still exists
        if library_actions.remove_stamp(self, self.library_root, project_id, stamp_id):
            self._current = None  # the removed stamp's page must not save again
            self._reload_tree()
            self.navigate(KIND_SAMPLE, project_id, sample_id)
        else:
            self.navigate(KIND_STAMP, project_id, stamp_id)

    # -- status ------------------------------------------------------------------

    def _on_activity(self, busy: bool, message: str) -> None:
        self.busy.setVisible(busy)
        self.busy_label.setText(message if busy else "")

    def _on_zoom_changed(self, scale: float) -> None:
        self.zoom_label.setText(f"{scale * 100:.0f} %")

    def _on_pixel_info(self, info) -> None:
        if info is None:
            self.pixel_readout.setText("")
            return
        parts = [f"x {info['x']}  y {info['y']}"]
        if info["mm"]:
            parts.append(f"{info['mm'][0]:.2f} × {info['mm'][1]:.2f} mm")
        if info["lab"]:
            L, a, b = info["lab"]  # noqa: N806 -- CIELAB notation
            parts.append(f"L* {L:.1f}  a* {a:.1f}  b* {b:.1f}")
        else:
            parts.append(f"RGB {info['rgb']}")
        self.pixel_readout.setText("   │   ".join(parts))

    def _update_title(self) -> None:
        self.setWindowTitle(f"Stamp Analyzer — {self.library_root}")

    def _about(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("About Stamp Analyzer")
        box.setIconPixmap(icons.app_icon().pixmap(64, 64))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<b>Stamp Analyzer</b> {__version__}")
        box.setInformativeText(
            "Perforation gauge, cancellation and calibrated colour (CIELAB D50, ΔE00) "
            "of postage stamps from characterised flatbed scans.<br><br>"
            f"Library: {self.library_root}"
        )
        box.exec()

    # -- shutdown --------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt override
        if self._current is not None:
            self._current.deactivate()  # saves pending stamp details
        # A QThread destroyed while running aborts the process: let any
        # running measurement finish first.
        hub().wait_all()
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        super().closeEvent(event)
