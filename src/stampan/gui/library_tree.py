"""The library tree: Projects -> Sheets -> Stamps, and References (SPEC §12).

Pure presentation over ``core.project`` / ``core.calibration.reference`` --
it lists what is on disk and keeps the user's place (selection and which
nodes are open) across reloads.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QAbstractItemView, QTreeWidget, QTreeWidgetItem

from stampan.core import project as project_mod
from stampan.core.calibration import reference as ref_mod
from stampan.gui import icons

KIND_SECTION = "section"
KIND_REFERENCE = "reference"
KIND_PROJECT = "project"
KIND_SAMPLE = "sample"
KIND_STAMP = "stamp"

_ROLE = Qt.ItemDataRole.UserRole


def stamp_status(stamp) -> tuple[int, int, str]:
    """(measurements done, measurements possible, tooltip text)."""
    perforation = bool(stamp.perforation.auto or stamp.perforation.manual)
    colour = bool(stamp.colour.clusters)
    tooltip = (
        f"Perforation: {'measured' if perforation else 'not measured'}\n"
        f"Colour: {'measured' if colour else 'not measured'}"
    )
    return int(perforation) + int(colour), 2, tooltip


class LibraryTree(QTreeWidget):
    selection_changed = Signal(str, str, str)  # kind, project_id, item_id ("" if n/a)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setIndentation(16)
        self.setAnimated(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.library_root: Path | None = None
        self._loaded_once = False

    # -- (re)loading -------------------------------------------------------

    def reload(self, library_root: Path) -> None:
        same_library = library_root == self.library_root
        expanded = self._expanded_keys() if same_library else set()
        selected = self.selected_key() if same_library else None
        self.library_root = library_root

        self.blockSignals(True)
        self.clear()
        projects_root = self._section("Projects", "projects")
        for project in project_mod.list_projects(library_root):
            self._add_project(projects_root, project)
        refs_root = self._section("References", "references")
        for ref in ref_mod.list_references(library_root):
            if ref.archived:
                continue
            item = self._item(refs_root, ref.name, "target", (KIND_REFERENCE, "", ref.ref_id))
            item.setToolTip(0, f"{ref.name}\nCreated {ref.created} · {ref.ref_id}")
        self.blockSignals(False)

        if not self._loaded_once:
            self._loaded_once = True
            projects_root.setExpanded(True)
            refs_root.setExpanded(True)
            for i in range(projects_root.childCount()):
                projects_root.child(i).setExpanded(True)
        else:
            self._restore_expanded(expanded)
        if selected is not None:
            self.select_key(*selected, emit=False)

    def _section(self, title: str, key: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem([title])
        item.setData(0, _ROLE, (KIND_SECTION, "", key))
        font = QFont(item.font(0))
        font.setBold(True)
        item.setFont(0, font)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self.addTopLevelItem(item)
        return item

    @staticmethod
    def _item(parent, text: str, icon_name: str | None, key: tuple) -> QTreeWidgetItem:
        item = QTreeWidgetItem([text])
        item.setData(0, _ROLE, key)
        if icon_name:
            item.setIcon(0, icons.icon(icon_name))
        parent.addChild(item)
        return item

    def _add_project(self, parent, project) -> None:
        root = self.library_root
        project_item = self._item(
            parent, project.name, "folder", (KIND_PROJECT, project.project_id, "")
        )
        project_item.setToolTip(0, f"{project.name}\nCreated {project.created}")
        for sample_id in project.sample_ids:
            sample = project_mod.load_sample(root, project.project_id, sample_id)
            n = len(sample.stamp_ids)
            sample_item = self._item(
                project_item,
                f"{project_mod.sheet_title(sample)}  ·  {n} stamp{'s' if n != 1 else ''}",
                "sheet",
                (KIND_SAMPLE, project.project_id, sample_id),
            )
            sample_item.setToolTip(0, f"{sample_id}\nScanned {sample.scan_time or 'unknown'}")
            for stamp_id in sample.stamp_ids:
                stamp = project_mod.load_stamp(root, project.project_id, stamp_id)
                text = project_mod.stamp_label(stamp_id)
                if stamp.catalogue_numbers:
                    text += f"  —  {', '.join(stamp.catalogue_numbers)}"
                done, total, tooltip = stamp_status(stamp)
                stamp_item = QTreeWidgetItem([text])
                stamp_item.setData(0, _ROLE, (KIND_STAMP, project.project_id, stamp_id))
                stamp_item.setIcon(0, icons.status_icon(done, total))
                stamp_item.setToolTip(0, f"{stamp_id}\n{tooltip}")
                sample_item.addChild(stamp_item)

    # -- keys, expansion, selection ------------------------------------------

    def _all_items(self):
        stack = [self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _expanded_keys(self) -> set:
        return {item.data(0, _ROLE) for item in self._all_items() if item.isExpanded()}

    def _restore_expanded(self, keys: set) -> None:
        for item in self._all_items():
            if item.data(0, _ROLE) in keys:
                item.setExpanded(True)

    def selected_key(self) -> tuple[str, str, str] | None:
        items = self.selectedItems()
        return items[0].data(0, _ROLE) if items else None

    def select_key(self, kind: str, project_id: str, item_id: str, emit: bool = True) -> bool:
        for item in self._all_items():
            if item.data(0, _ROLE) == (kind, project_id, item_id):
                if not emit:
                    self.blockSignals(True)
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.setCurrentItem(item)
                self.scrollToItem(item)
                self.blockSignals(False)
                return True
        return False

    def sibling_stamp(self, project_id: str, stamp_id: str, step: int) -> str | None:
        """The stamp ``step`` places before/after this one in project order."""
        stamps = [
            item.data(0, _ROLE)[2]
            for item in self._all_items()
            if item.data(0, _ROLE)[:2] == (KIND_STAMP, project_id)
        ]
        stamps.sort()
        if stamp_id not in stamps:
            return None
        i = stamps.index(stamp_id) + step
        return stamps[i] if 0 <= i < len(stamps) else None

    def item_key_at(self, pos) -> tuple[str, str, str] | None:
        item = self.itemAt(pos)
        return item.data(0, _ROLE) if item is not None else None

    def _on_selection_changed(self) -> None:
        key = self.selected_key()
        if key is not None and key[0] != KIND_SECTION:
            self.selection_changed.emit(*key)
