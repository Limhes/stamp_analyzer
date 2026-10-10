"""Rename and remove projects, sheets and stamps, with confirmation.

Shared by the library tree's context menu and the pages. Each function
returns True when it changed the library, so the caller can refresh.
Removal is permanent (the files are deleted), so every removal asks first
and says exactly what will be lost.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QInputDialog, QMessageBox, QWidget

from stampan.core import project as project_mod


def _confirm(parent: QWidget, title: str, text: str, detail: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    box.setInformativeText(detail + "\n\nThis cannot be undone.")
    remove = box.addButton("Remove", QMessageBox.ButtonRole.DestructiveRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    return box.clickedButton() is remove


def rename_project(parent: QWidget, root: Path, project_id: str) -> bool:
    project = project_mod.load_project(root, project_id)
    name, ok = QInputDialog.getText(parent, "Rename Project", "Project name:", text=project.name)
    if not ok or not name.strip() or name.strip() == project.name:
        return False
    project_mod.rename_project(root, project_id, name.strip())
    return True


def remove_project(parent: QWidget, root: Path, project_id: str) -> bool:
    project = project_mod.load_project(root, project_id)
    n_stamps = len(project_mod.stamp_index(root, project))
    if not _confirm(
        parent,
        "Remove Project",
        f"Remove the project “{project.name}”?",
        f"Its {len(project.sample_ids)} sheet scan(s), {n_stamps} stamp(s) with all their "
        "measurements, and its reports are deleted from the library.",
    ):
        return False
    project_mod.delete_project(root, project_id)
    return True


def rename_sheet(parent: QWidget, root: Path, project_id: str, sample_id: str) -> bool:
    sample = project_mod.load_sample(root, project_id, sample_id)
    name, ok = QInputDialog.getText(
        parent,
        "Rename Sheet",
        "Sheet name (leave empty for the default):",
        text=sample.name or "",
    )
    if not ok or (name.strip() or None) == sample.name:
        return False
    project_mod.rename_sample(root, project_id, sample_id, name.strip() or None)
    return True


def remove_sheet(parent: QWidget, root: Path, project_id: str, sample_id: str) -> bool:
    sample = project_mod.load_sample(root, project_id, sample_id)
    if not _confirm(
        parent,
        "Remove Sheet",
        f"Remove “{project_mod.sheet_title(sample)}” from the project?",
        f"The sheet scan and its {len(sample.stamp_ids)} stamp(s), with all their "
        "measurements, are deleted from the library.",
    ):
        return False
    project_mod.delete_sample(root, project_id, sample_id)
    return True


def remove_stamp(parent: QWidget, root: Path, project_id: str, stamp_id: str) -> bool:
    if not _confirm(
        parent,
        "Remove Stamp",
        f"Remove {project_mod.stamp_label(stamp_id)} from its sheet?",
        "Its crop, perforation and colour measurements are deleted. The sheet scan itself "
        "is kept, so the stamp can be added again with “Add Missed Stamp”.",
    ):
        return False
    project_mod.delete_stamp(root, project_id, stamp_id)
    return True
