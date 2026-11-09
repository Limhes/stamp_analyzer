"""Offscreen smoke test of the main window: it builds, and every library
item kind opens its page without error."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    from PySide6.QtCore import QCoreApplication, QSettings

    QCoreApplication.setOrganizationName("StampAnalyzerTests")
    QCoreApplication.setApplicationName("smoke")
    # Keep the test's settings out of the user's ~/.config.
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat,
        QSettings.Scope.UserScope,
        str(tmp_path_factory.mktemp("settings")),
    )
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from stampan.gui import theme

    theme.apply(application)
    return application


def test_main_window_opens_pages(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from stampan.core import project as project_mod
    from stampan.gui.library_tree import KIND_PROJECT
    from stampan.gui.main_window import MainWindow
    from stampan.gui.tasks import hub

    QSettings().setValue("library/root", str(tmp_path))
    project = project_mod.get_or_create_project(tmp_path, "Smoke")
    window = MainWindow()
    try:
        assert window.library_root == tmp_path
        assert window.pages.currentWidget() is window.welcome_page
        window.navigate(KIND_PROJECT, project.project_id, "")
        app.processEvents()
        assert window.pages.currentWidget() is window.project_page
        assert window.act_add_sheet.isEnabled()
        assert not window.viewer.has_image()

        from stampan.gui import library_actions

        monkeypatch.setattr(
            library_actions.QInputDialog, "getText", lambda *a, **k: ("Renamed", True)
        )
        monkeypatch.setattr(library_actions, "_confirm", lambda *a: True)
        window.act_rename_project.trigger()
        assert project_mod.load_project(tmp_path, project.project_id).name == "Renamed"
        window.act_remove_project.trigger()
        app.processEvents()
        assert project_mod.list_projects(tmp_path) == []
        assert window.pages.currentWidget() is window.welcome_page
    finally:
        hub().wait_all()
        window.close()
        QSettings().clear()


def test_perforation_entry_modes(app):
    from PySide6.QtCore import QSettings

    from stampan.gui.perforation_entry import MODE_HV, MODE_ONE, MODE_SIDES, PerforationEntry

    QSettings().remove("perforation/entry_mode")
    entry = PerforationEntry()
    assert entry.mode() == MODE_ONE
    got = []
    entry.apply_requested.connect(got.append)
    entry.one.setText("11½")
    entry.one.returnPressed.emit()
    assert got[-1]["left"] == ("perforated", 11.5)

    entry.set_mode(MODE_HV)
    assert PerforationEntry().mode() == MODE_HV  # remembered
    entry.horizontal.setText("11")
    entry.vertical.setText("12 1/2")
    entry.vertical.returnPressed.emit()
    assert got[-1]["top"] == ("perforated", 11.0) and got[-1]["right"] == ("perforated", 12.5)
    entry.vertical.setText("nonsense")
    assert not entry.apply_button.isEnabled()

    entry.set_mode(MODE_SIDES)
    entry.side_edits["left"].setText("imperf")
    entry._apply()
    assert got[-1] == {"left": ("imperf", None)}  # other sides: measured


def test_overview_cells_save_through_workflow(app, tmp_path):
    from PySide6.QtCore import Qt

    from stampan.core import overview as overview_mod
    from stampan.core import project as project_mod
    from stampan.gui import overview as gui_overview
    from tests.test_overview import _project_with_stamp

    project, stamp_id = _project_with_stamp(tmp_path)
    model = gui_overview.OverviewModel(lambda: tmp_path)
    model.set_entries(project.project_id, overview_mod.project_entries(tmp_path, project))
    errors = []
    model.edit_failed.connect(errors.append)

    def cell(col):
        return model.index(0, col)

    assert model.setData(cell(gui_overview.C_CAT), " SG 4 ,Mi  5")
    assert model.data(cell(gui_overview.C_CAT)) == "SG 4, Mi 5"
    assert model.entries[1].catalogue == "SG 4, Mi 5"  # the stamp's other row too

    # Unchanged perforation text must not turn the measured value into "by hand".
    text = model.data(cell(gui_overview.C_PERF), Qt.ItemDataRole.EditRole)
    assert not model.setData(cell(gui_overview.C_PERF), text)
    assert model.setData(cell(gui_overview.C_PERF), "11 × 12½")
    stamp = project_mod.load_stamp(tmp_path, project.project_id, stamp_id)
    assert stamp.perforation.manual["left"].notation == "12 1/2"

    assert not model.setData(cell(gui_overview.C_CDATE), "1889-13")
    assert errors and "date" in errors[-1].lower()
    assert model.setData(cell(gui_overview.C_CDATE), "c. 1890-02")
    stamp = project_mod.load_stamp(tmp_path, project.project_id, stamp_id)
    assert stamp.cancellation.date == "1890-02" and stamp.cancellation.date_estimated
