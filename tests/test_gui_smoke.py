"""Offscreen smoke test of the main window: it builds, and every library
item kind opens its page without error."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtCore import QCoreApplication, QSettings

    QCoreApplication.setOrganizationName("StampAnalyzerTests")
    QCoreApplication.setApplicationName("smoke")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
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
