"""The library tree: References and Projects -> Samples -> Stamps (SPEC §12).

Pure presentation over ``core.project``/``core.calibration.reference`` --
no analysis logic here, just listing what's already on disk.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem

from stampan.core import project as project_mod
from stampan.core.calibration import reference as ref_mod

# Item kinds, stored in column-0 UserRole for the selection handler.
KIND_REFERENCE = "reference"
KIND_PROJECT = "project"
KIND_SAMPLE = "sample"
KIND_STAMP = "stamp"


class LibraryTree(QTreeWidget):
    selection_changed = Signal(str, str, str)  # kind, project_id, item_id ("" if n/a)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.library_root: Path | None = None

    def reload(self, library_root: Path) -> None:
        self.library_root = library_root
        self.clear()

        refs_root = QTreeWidgetItem(["References"])
        self.addTopLevelItem(refs_root)
        for ref in ref_mod.list_references(library_root):
            item = QTreeWidgetItem([f"{ref.name}  ({ref.ref_id})"])
            item.setData(0, Qt.ItemDataRole.UserRole, (KIND_REFERENCE, "", ref.ref_id))
            refs_root.addChild(item)

        projects_root = QTreeWidgetItem(["Projects"])
        self.addTopLevelItem(projects_root)
        for project in project_mod.list_projects(library_root):
            project_item = QTreeWidgetItem([project.name])
            project_item.setData(
                0, Qt.ItemDataRole.UserRole, (KIND_PROJECT, project.project_id, "")
            )
            projects_root.addChild(project_item)

            for sample_id in project.sample_ids:
                sample = project_mod.load_sample(library_root, project.project_id, sample_id)
                sample_item = QTreeWidgetItem([sample_id])
                sample_item.setData(
                    0, Qt.ItemDataRole.UserRole, (KIND_SAMPLE, project.project_id, sample_id)
                )
                project_item.addChild(sample_item)

                for stamp_id in sample.stamp_ids:
                    stamp = project_mod.load_stamp(library_root, project.project_id, stamp_id)
                    label = stamp_id
                    if stamp.catalogue_numbers:
                        label += f"  ({', '.join(stamp.catalogue_numbers)})"
                    status = _status_glyph(stamp)
                    stamp_item = QTreeWidgetItem([f"{status} {label}"])
                    stamp_item.setData(
                        0, Qt.ItemDataRole.UserRole, (KIND_STAMP, project.project_id, stamp_id)
                    )
                    sample_item.addChild(stamp_item)

        self.expandAll()

    def _on_selection_changed(self) -> None:
        items = self.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if data is None:
            return
        kind, project_id, item_id = data
        self.selection_changed.emit(kind, project_id, item_id)


def _status_glyph(stamp) -> str:
    """A terse per-stamp status indicator -- a scoped-down stand-in for
    SPEC §12's full workflow step bar (todo/auto/reviewed/manual per step):
    this just flags what has and hasn't been run yet."""
    done = []
    done.append("P" if stamp.perforation.auto else "-")
    done.append("C" if stamp.colour.clusters else "-")
    return "[" + "".join(done) + "]"
