"""Colour tab (SPEC §8): one user-named measurement area per colour, drawn on
the viewer; each is measured from its own pixels as soon as it changes."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stampan.core import colour as colour_mod
from stampan.core import philatelic_colours as philatelic_mod
from stampan.core import project as project_mod
from stampan.core import workflow
from stampan.core.models import ColourCluster
from stampan.gui.pages.base import AppContext
from stampan.gui.tasks import TaskRunner
from stampan.gui.viewer import MODE_INCLUDE
from stampan.gui.widgets import Banner, ScaledImage, button, label

REMEASURE_DEBOUNCE_MS = 400


class ColourTab(QWidget):
    changed = Signal()
    #: True while a colour area is selected for drawing on the viewer.
    area_editing = Signal(bool)

    def __init__(self, ctx: AppContext, runner: TaskRunner, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.runner = runner
        self.project_id: str | None = None
        self.stamp_id: str | None = None
        self._clusters: list[ColourCluster] = []
        self._philatelic = None
        self._pending: str | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._measure_pending)
        ctx.viewer.roi_changed.connect(self._on_roi_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(
            label(
                "Add a colour, then mark an area that contains only that colour — clean "
                "paper, or clean design ink away from any cancellation — with the area tools "
                "above the image. It is measured as you draw. Double-click a name to rename it.",
                "caption",
                wrap=True,
            )
        )
        buttons = QHBoxLayout()
        buttons.addWidget(button("Add Colour…", "plus", "primary", slot=self._add))
        self.remove_button = button("Remove", "clear", "danger", slot=self._remove)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Colour", "Philatelic name", "L*  C*  h°"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # The colour's name is edited in place (double-click or F2).
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setMinimumHeight(130)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self.table)

        self.banner = Banner()
        layout.addWidget(self.banner)
        layout.addWidget(label("Area distribution", "section"))
        self.scatter = ScaledImage(placeholder="Select a colour to see its a*b* and C*–L* scatter")
        layout.addWidget(self.scatter)
        layout.addStretch(1)
        self._update_buttons()

    # -- loading ---------------------------------------------------------------

    def set_stamp(self, project_id: str, stamp_id: str) -> None:
        self._timer.stop()
        self.project_id, self.stamp_id = project_id, stamp_id
        stamp = project_mod.load_stamp(self.ctx.library_root, project_id, stamp_id)
        # Only areas drawn here have a mask; clusters without one come from
        # the CLI's unattended GMM clustering and correspond to no area the
        # user can see or edit, so they are not listed.
        self._clusters = [c for c in stamp.colour.clusters if c.mask_path]
        self._populate()
        self.scatter.clear()
        self.banner.show_message("")

    def deactivate(self) -> None:
        self.table.clearSelection()
        self.ctx.viewer.set_active_roi(None)
        self.area_editing.emit(False)

    def _populate(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._clusters))
        for row, c in enumerate(self._clusters):
            name = QTableWidgetItem(c.label)
            if c.srgb_swatch:
                name.setData(Qt.ItemDataRole.DecorationRole, QColor(*c.srgb_swatch))
            lch = "  ".join(f"{v:.1f}" for v in c.mean_lch) if c.mean_lch else "not drawn yet"
            name.setToolTip("Double-click to rename")
            for col, item in enumerate(
                (name, QTableWidgetItem(c.philatelic_name or ""), QTableWidgetItem(lch))
            ):
                if col:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.remove_button.setEnabled(bool(self.table.selectionModel().selectedRows()))

    def _selected(self) -> ColourCluster | None:
        rows = self.table.selectionModel().selectedRows()
        return self._clusters[rows[0].row()] if rows else None

    def _select(self, name: str) -> None:
        for row, c in enumerate(self._clusters):
            if c.label == name:
                self.table.selectRow(row)
                return

    # -- selection -> active area on the viewer --------------------------------

    def _on_row_selected(self) -> None:
        self._update_buttons()
        cluster = self._selected()
        if cluster is None:
            self.ctx.viewer.set_active_roi(None)
            self.area_editing.emit(False)
            return
        mask = (
            project_mod.load_stamp_roi_mask(
                self.ctx.library_root, self.project_id, cluster.mask_path
            )
            if cluster.mask_path
            else None
        )
        size = self.ctx.viewer.image_size()
        if mask is not None and size is not None and mask.shape[:2] != (size[1], size[0]):
            # The stamp was re-cropped (rectangle adjusted on the sheet) after
            # this area was drawn: the mask no longer lines up with the crop.
            mask = None
            self.banner.show_message(
                f"The area of “{cluster.label}” was drawn on an earlier crop of this stamp "
                "and no longer fits it. Draw it again; its values below are from the old area.",
                "warning",
            )
        self.ctx.viewer.set_mode(MODE_INCLUDE)
        self.ctx.viewer.set_active_roi(cluster.label, initial_mask=mask)
        self.area_editing.emit(True)
        self._show_scatter(cluster.label)

    def _show_scatter(self, name: str) -> None:
        mask = self.ctx.viewer.get_mask(name)
        if mask is None:
            self.scatter.clear()
            return
        model = workflow.colour_model_for_stamp(
            self.ctx.library_root, self.project_id, self.stamp_id
        )
        if model is None:
            self.banner.show_message("This stamp's reference has no colour model.", "warning")
            return
        crop = project_mod.load_stamp_crop(self.ctx.library_root, self.project_id, self.stamp_id)
        self.scatter.set_png(colour_mod.render_roi_scatter(crop, model.predict_lab, mask, name))

    # -- rename ----------------------------------------------------------------

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or item.row() >= len(self._clusters):
            return
        cluster = self._clusters[item.row()]
        old, new = cluster.label, item.text().strip()
        if new == old:
            return
        problem = ""
        if not new:
            problem = "A colour needs a name."
        elif any(c.label == new for c in self._clusters):
            problem = f"“{new}” already exists on this stamp."
        if problem:
            QMessageBox.warning(self.window(), "Rename Colour", problem)
            self._populate()
            self._select(old)
            return
        if cluster.mask_path:  # already saved: rename on disk, mask included
            stamp = project_mod.rename_stamp_colour(
                self.ctx.library_root, self.project_id, self.stamp_id, old, new
            )
            self._clusters[item.row()] = next(c for c in stamp.colour.clusters if c.label == new)
        else:
            cluster.label = new
        self.ctx.viewer.rename_roi(old, new)
        if self._pending == old:
            self._pending = new
        self._populate()
        self._select(new)
        self.changed.emit()

    # -- add / remove ----------------------------------------------------------

    def _add(self) -> None:
        if self.stamp_id is None:
            return
        name, ok = QInputDialog.getText(
            self.window(), "Add Colour", "Name for this colour (e.g. “paper”, “frame”, “centre”):"
        )
        name = name.strip()
        if not ok or not name:
            return
        if any(c.label == name for c in self._clusters):
            QMessageBox.warning(self.window(), "Add Colour", f"“{name}” already exists.")
            return
        self._clusters.append(ColourCluster(label=name))
        self._populate()
        self._select(name)

    def _remove(self) -> None:
        cluster = self._selected()
        if cluster is None:
            return
        answer = QMessageBox.question(
            self.window(), "Remove Colour", f"Remove “{cluster.label}” and its measurement area?"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._clusters.remove(cluster)
        self.ctx.viewer.remove_roi(cluster.label)
        if cluster.mask_path:
            path = (
                project_mod.project_dir(self.ctx.library_root, self.project_id) / cluster.mask_path
            )
            path.unlink(missing_ok=True)
        workflow.save_stamp_colours(
            self.ctx.library_root, self.project_id, self.stamp_id, self._clusters
        )
        self._populate()
        self.scatter.clear()
        self.area_editing.emit(False)
        self.changed.emit()

    # -- measuring as the area changes -----------------------------------------

    def _on_roi_changed(self, name: str) -> None:
        if any(c.label == name for c in self._clusters):
            self._pending = name
            self._timer.start(REMEASURE_DEBOUNCE_MS)

    def _measure_pending(self) -> None:
        name, self._pending = self._pending, None
        mask = self.ctx.viewer.get_mask(name) if name else None
        if name is None or mask is None or self.stamp_id is None:
            return
        if self._philatelic is None:
            self._philatelic = philatelic_mod.load_philatelic_colours()
        project_id, stamp_id = self.project_id, self.stamp_id

        def done(result) -> None:
            if (project_id, stamp_id) != (self.project_id, self.stamp_id):
                return
            cluster, warnings = result
            for i, c in enumerate(self._clusters):
                if c.label == name and cluster is not None:
                    self._clusters[i] = cluster
            workflow.save_stamp_colours(self.ctx.library_root, project_id, stamp_id, self._clusters)
            self._populate()
            self._select(name)
            # Re-selecting an already-selected row doesn't always signal, so
            # refresh the plot explicitly for the area just drawn.
            self._show_scatter(name)
            self.banner.show_message("<br>".join(warnings), "warning")
            self.changed.emit()

        self.runner.run(
            workflow.measure_roi_colour,
            self.ctx.library_root,
            project_id,
            stamp_id,
            name,
            mask,
            philatelic_table=self._philatelic,
            description="Measuring colour…",
            on_done=done,
            error_title="Colour measurement failed",
        )
