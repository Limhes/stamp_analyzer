"""The sheet page: the stamps detected on one sample sheet, with correction
of a stamp's rectangle and adding a stamp the detection missed (SPEC §5)."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QMenu, QToolButton

from stampan.core import project as project_mod
from stampan.core import workflow
from stampan.core.calibration import reference as ref_mod
from stampan.core.project import stamp_label
from stampan.gui import icons, library_actions
from stampan.gui.library_tree import (
    KIND_PROJECT,
    KIND_STAMP,
    stamp_status,
)
from stampan.gui.pages.base import AppContext, Page
from stampan.gui.viewer import TOOL_PAN, TOOL_PERF_BOX
from stampan.gui.widgets import Banner, button

_IDLE_HINT = (
    "Click a stamp to adjust its rectangle on the sheet, or use “Add Missed Stamp” "
    "for one the detection did not find. Double-click opens the stamp; right-click "
    "for more."
)


class SheetPage(Page):
    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(ctx, scrollable=False, parent=parent)
        self.project_id: str | None = None
        self.sample_id: str | None = None
        self._sample = None
        self._scale: tuple[float, float] | None = None
        self._editing: str | None = None  # stamp id being corrected
        self._adding = False

        self.open_button = self.header.add_action(
            button(
                "Open Stamp",
                "chevron-right",
                tooltip="Show the selected stamp",
                slot=self._open_selected,
            )
        )
        more = QToolButton()
        more.setIcon(icons.icon("edit"))
        more.setToolTip("Rename or remove this sheet")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction(icons.icon("edit"), "Rename Sheet…", self.rename_sheet)
        menu.addAction(icons.icon("clear"), "Remove Sheet…", self.remove_sheet)
        more.setMenu(menu)
        self.header.add_action(more)

        self.banner = Banner(_IDLE_HINT, "info")
        self.body_layout.addWidget(self.banner)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.itemSelectionChanged.connect(self._on_row_selected)
        self.list.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.body_layout.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        self.add_button = button("Add Missed Stamp", "plus", slot=self._start_add)
        self.apply_button = button("Apply", "check", "primary", slot=self._apply)
        self.cancel_button = button("Cancel", "close", slot=self._cancel)
        row.addWidget(self.add_button)
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        row.addWidget(self.apply_button)
        self.body_layout.addLayout(row)
        self._set_editing_ui(False)

    # -- loading ---------------------------------------------------------------

    def set_sample(self, project_id: str, sample_id: str) -> None:
        self._end_edit(show_boxes=False)
        self.project_id, self.sample_id = project_id, sample_id
        root = self.ctx.library_root
        self._sample = project_mod.load_sample(root, project_id, sample_id)
        project = project_mod.load_project(root, project_id)
        reference = ref_mod.load_reference(root, self._sample.ref_id)
        self._scale = (
            (reference.scale.px_per_mm_x, reference.scale.px_per_mm_y) if reference.scale else None
        )
        scanned = (self._sample.scan_time or "")[:10] or "date unknown"
        self.header.set_text(
            project_mod.sheet_title(self._sample),
            f"Scanned {scanned} · reference “{reference.name}”",
            breadcrumb=project.name,
        )
        scan, _ = project_mod.load_sample_scan(root, project_id, self._sample)
        self.ctx.viewer.set_image(scan, *(self._scale or (None, None)))
        self.ctx.viewer.set_tool(TOOL_PAN)
        self._populate_list()
        self._show_all_boxes()

    def deactivate(self) -> None:
        self._end_edit(show_boxes=False)

    def _populate_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        root = self.ctx.library_root
        for stamp_id in self._sample.stamp_ids:
            stamp = project_mod.load_stamp(root, self.project_id, stamp_id)
            text = stamp_label(stamp_id)
            if stamp.geometry.width_mm and stamp.geometry.height_mm:
                text += f"   {stamp.geometry.width_mm:.1f} × {stamp.geometry.height_mm:.1f} mm"
            if stamp.catalogue_numbers:
                text += f"   —  {', '.join(stamp.catalogue_numbers)}"
            done, total, tooltip = stamp_status(stamp)
            item = QListWidgetItem(icons.status_icon(done, total), text)
            item.setToolTip(tooltip)
            item.setData(Qt.ItemDataRole.UserRole, stamp_id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.open_button.setEnabled(False)

    def _box_for(self, stamp_id: str) -> np.ndarray | None:
        g = project_mod.load_stamp(self.ctx.library_root, self.project_id, stamp_id).geometry
        if self._scale is None or g.sample_centre_px is None or not g.width_mm or not g.height_mm:
            return None
        size = (g.width_mm * self._scale[0], g.height_mm * self._scale[1])
        return cv2.boxPoints((g.sample_centre_px, size, g.rotation_deg or 0.0))

    def _show_all_boxes(self) -> None:
        boxes = []
        for stamp_id in self._sample.stamp_ids:
            box = self._box_for(stamp_id)
            if box is not None:
                boxes.append((stamp_label(stamp_id).replace("Stamp ", ""), box))
        self.ctx.viewer.set_static_boxes(boxes)
        self.ctx.viewer.fit_whole_image()

    # -- correcting a detected stamp ------------------------------------------

    def _selected_stamp(self) -> str | None:
        items = self.list.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _on_row_selected(self) -> None:
        stamp_id = self._selected_stamp()
        self.open_button.setEnabled(stamp_id is not None)
        if stamp_id is not None:
            self._begin_edit(stamp_id)

    def select_and_edit(self, stamp_id: str) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == stamp_id:
                self.list.setCurrentItem(item)
                return

    def _list_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None or self._adding:
            return
        stamp_id = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction(
            icons.icon("chevron-right"),
            "Open Stamp",
            lambda: self.ctx.navigate(KIND_STAMP, self.project_id, stamp_id),
        )
        menu.addAction(
            icons.icon("box"), "Adjust Rectangle", lambda: self.list.setCurrentItem(item)
        )
        menu.addSeparator()
        menu.addAction(icons.icon("clear"), "Remove Stamp…", lambda: self.remove_stamp(stamp_id))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # -- sheet / stamp management ----------------------------------------------

    def rename_sheet(self) -> None:
        if self.sample_id and library_actions.rename_sheet(
            self.window(), self.ctx.library_root, self.project_id, self.sample_id
        ):
            self.changed.emit()
            self.set_sample(self.project_id, self.sample_id)

    def remove_sheet(self) -> None:
        if self.sample_id and library_actions.remove_sheet(
            self.window(), self.ctx.library_root, self.project_id, self.sample_id
        ):
            project_id = self.project_id
            self._end_edit(show_boxes=False)
            self.sample_id = self._sample = None
            self.changed.emit()
            self.ctx.navigate(KIND_PROJECT, project_id, "")

    def remove_stamp(self, stamp_id: str) -> None:
        if not library_actions.remove_stamp(
            self.window(), self.ctx.library_root, self.project_id, stamp_id
        ):
            return
        self._end_edit(show_boxes=False)
        self._sample = project_mod.load_sample(
            self.ctx.library_root, self.project_id, self.sample_id
        )
        self._populate_list()
        self._show_all_boxes()
        self.changed.emit()
        self.banner.show_message(f"Removed {stamp_label(stamp_id)}.", "success")

    def _open_selected(self) -> None:
        stamp_id = self._selected_stamp()
        if stamp_id is not None:
            self.ctx.navigate(KIND_STAMP, self.project_id, stamp_id)

    def _begin_edit(self, stamp_id: str) -> None:
        if self._adding:
            self._end_edit(show_boxes=False)
        box = self._box_for(stamp_id)
        if box is None:
            self.banner.show_message(
                f"{stamp_label(stamp_id)} has no detected rectangle to adjust.",
                "warning",
            )
            return
        viewer = self.ctx.viewer
        viewer.clear_static_boxes()
        viewer.set_perf_box(box)
        viewer.zoom_to_perf_box()
        viewer.set_tool(TOOL_PERF_BOX)
        self._editing = stamp_id
        self._set_editing_ui(True)
        self.banner.show_message(
            f"Adjusting {stamp_label(stamp_id)}: drag the rectangle or its corner handles "
            "to fit the stamp, "
            "the round handle above it to rotate, then Apply. The stamp is re-cropped; its "
            "perforation and colour then need measuring again.",
            "info",
        )

    # -- adding a stamp the detection missed ----------------------------------

    def _start_add(self) -> None:
        if self._sample is None or self._scale is None:
            self.banner.show_message("This sheet's reference has no scale calibration.", "warning")
            return
        self.list.blockSignals(True)
        self.list.clearSelection()
        self.list.blockSignals(False)
        self._editing = None
        root = self.ctx.library_root
        widths, heights = [], []
        for stamp_id in self._sample.stamp_ids:
            g = project_mod.load_stamp(root, self.project_id, stamp_id).geometry
            if g.width_mm and g.height_mm:
                widths.append(g.width_mm)
                heights.append(g.height_mm)
        # A sheet is usually one issue, so the other stamps' median size is
        # a good starting rectangle; a generic definitive size otherwise.
        width_mm = float(np.median(widths)) if widths else 25.0
        height_mm = float(np.median(heights)) if heights else 30.0
        image_size = self.ctx.viewer.image_size() or (0, 0)
        centre = (image_size[0] / 2.0, image_size[1] / 2.0)
        box = cv2.boxPoints((centre, (width_mm * self._scale[0], height_mm * self._scale[1]), 0.0))
        # Stay zoomed out with the other stamps visible: the user still has
        # to find where the missed stamp is.
        self.ctx.viewer.set_perf_box(box)
        self.ctx.viewer.fit_whole_image()
        self.ctx.viewer.set_tool(TOOL_PERF_BOX)
        self._adding = True
        self._set_editing_ui(True)
        self.banner.show_message(
            "Drag the new rectangle onto the missed stamp (right-drag, or drag outside the "
            "rectangle, to pan; wheel to zoom), fit it with the handles, then Apply.",
            "info",
        )

    # -- apply / cancel -------------------------------------------------------

    def _apply(self) -> None:
        rect = self.ctx.viewer.get_perf_box_rect()
        if rect is None or (self._editing is None and not self._adding):
            return
        centre, size, angle = rect
        root, project_id, sample, stamp_id = (
            self.ctx.library_root,
            self.project_id,
            self._sample,
            self._editing,
        )

        def done(stamp) -> None:
            verb = "Added" if stamp_id is None else "Updated"
            self._end_edit()
            self._sample = project_mod.load_sample(root, project_id, self.sample_id)
            self._populate_list()
            self._show_all_boxes()
            self.changed.emit()
            self.banner.show_message(f"{verb} {stamp_label(stamp.stamp_id)}.", "success")

        self.runner.run(
            workflow.place_stamp,
            root,
            project_id,
            sample,
            centre,
            size,
            angle,
            stamp_id=stamp_id,
            description="Re-cropping stamp…",
            on_done=done,
            error_title="Could not update the stamp",
        )

    def _cancel(self) -> None:
        self._end_edit()
        self.banner.show_message(_IDLE_HINT, "info")

    def _end_edit(self, show_boxes: bool = True) -> None:
        was_active = self._editing is not None or self._adding
        self._editing = None
        self._adding = False
        self._set_editing_ui(False)
        if was_active:
            self.ctx.viewer.clear_perf_box()
            self.ctx.viewer.set_tool(TOOL_PAN)
            self.list.blockSignals(True)
            self.list.clearSelection()
            self.list.blockSignals(False)
            self.open_button.setEnabled(False)
            if show_boxes and self._sample is not None:
                self._show_all_boxes()

    def _set_editing_ui(self, editing: bool) -> None:
        self.apply_button.setVisible(editing)
        self.cancel_button.setVisible(editing)
        self.add_button.setEnabled(not editing)
