"""The sheet page: the stamps on one sample sheet. Stamps are found by
detection inside a region the user drags out (undoable), or placed by hand;
any stamp's rectangle can be corrected (SPEC §5)."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
)

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
from stampan.gui.viewer import TOOL_PAN, TOOL_PERF_BOX, TOOL_REGION
from stampan.gui.widgets import Banner, button, label

_IDLE_HINT = (
    "Find stamps by dragging a region around them, or place one by hand. Click a stamp "
    "in the list to adjust its rectangle; double-click opens it; right-click for more."
)
_REGION_HINT = (
    "Drag a rectangle around one or more stamps (right-drag pans, wheel zooms), then "
    "Detect Stamps. Stamps cut by the region's border are left out."
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
        self._selecting = False  # dragging out a detection region
        #: Stamp ids added by each region detection on this sheet, for undo.
        self._detections: list[list[str]] = []

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

        find = QGroupBox("Find Stamps")
        find_layout = QVBoxLayout(find)
        find_layout.addWidget(
            label(
                "Detection works on the region you select, so the background "
                "threshold adapts to that part of the sheet.",
                "caption",
                wrap=True,
            )
        )
        find_row = QHBoxLayout()
        self.region_button = button(
            "Select Region", "box", tooltip="Drag a rectangle on the sheet", slot=self._start_region
        )
        self.region_button.setCheckable(True)
        self.detect_button = button(
            "Detect Stamps", "run", "primary", tooltip="Detect stamps inside the region",
            slot=self._detect_in_region,
        )  # fmt: skip
        self.undo_button = button("Undo", "undo", slot=self._undo_detection)
        find_row.addWidget(self.region_button)
        find_row.addWidget(self.detect_button)
        find_row.addStretch(1)
        find_row.addWidget(self.undo_button)
        find_layout.addLayout(find_row)
        self.body_layout.addWidget(find)
        self.ctx.viewer.region_changed.connect(self._update_find_ui)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.itemSelectionChanged.connect(self._on_row_selected)
        self.list.itemDoubleClicked.connect(lambda _item: self._open_selected())
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.body_layout.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        self.add_button = button(
            "Place Stamp by Hand", "plus", tooltip="Fit a rectangle to a stamp yourself",
            slot=self._start_add,
        )  # fmt: skip
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
        if (project_id, sample_id) != (self.project_id, self.sample_id):
            self._detections = []
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
        self._update_find_ui()
        if not self._sample.stamp_ids:
            self._start_region()  # a new, empty sheet: finding stamps is the next step

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

    def _show_all_boxes(self, fit: bool = True) -> None:
        boxes = []
        for stamp_id in self._sample.stamp_ids:
            box = self._box_for(stamp_id)
            if box is not None:
                boxes.append((stamp_label(stamp_id).replace("Stamp ", ""), box))
        self.ctx.viewer.set_static_boxes(boxes)
        if fit:
            self.ctx.viewer.fit_whole_image()

    # -- the stamp list ---------------------------------------------------------

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
        menu.addAction(
            icons.icon("rotate-cw"), "Rotate Clockwise", lambda: self._rotate(stamp_id, True)
        )
        menu.addAction(
            icons.icon("rotate-ccw"), "Rotate Anticlockwise", lambda: self._rotate(stamp_id, False)
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
        for added in self._detections:
            if stamp_id in added:
                added.remove(stamp_id)
        self._reload_sheet()
        self._update_find_ui()
        self.changed.emit()
        self.banner.show_message(f"Removed {stamp_label(stamp_id)}.", "success")

    def _rotate(self, stamp_id: str, clockwise: bool) -> None:
        self._end_edit(show_boxes=False)

        def done(_stamp) -> None:
            self._reload_sheet()
            self.changed.emit()
            self.banner.show_message(f"Rotated {stamp_label(stamp_id)}.", "success")

        self.runner.run(
            workflow.rotate_stamp,
            self.ctx.library_root,
            self.project_id,
            stamp_id,
            clockwise,
            description="Rotating stamp…",
            on_done=done,
            error_title="Could not rotate the stamp",
        )

    def _open_selected(self) -> None:
        stamp_id = self._selected_stamp()
        if stamp_id is not None:
            self.ctx.navigate(KIND_STAMP, self.project_id, stamp_id)

    # -- detecting stamps in a region -------------------------------------------

    def _start_region(self) -> None:
        if self._sample is None:
            return
        if self._selecting:  # the button toggles
            self._end_region()
            self.banner.show_message(_IDLE_HINT, "info")
            return
        self._end_edit()
        self._selecting = True
        self.ctx.viewer.set_tool(TOOL_REGION)
        self.banner.show_message(_REGION_HINT, "info")
        self._update_find_ui()

    def _end_region(self) -> None:
        if self._selecting:
            self._selecting = False
            self.ctx.viewer.clear_region()
            self.ctx.viewer.set_tool(TOOL_PAN)
        self._update_find_ui()

    def _update_find_ui(self) -> None:
        self.region_button.setChecked(self._selecting)
        self.detect_button.setEnabled(self._selecting and self.ctx.viewer.region() is not None)
        n = len(self._detections[-1]) if self._detections else 0
        self.undo_button.setEnabled(n > 0)
        self.undo_button.setToolTip(
            f"Remove the {n} stamp(s) the last detection added" if n else "Nothing to undo"
        )

    def _detect_in_region(self) -> None:
        region = self.ctx.viewer.region()
        if region is None or self._sample is None:
            return
        root, project_id, sample_id = self.ctx.library_root, self.project_id, self.sample_id

        def done(result) -> None:
            if result.added:
                self._detections.append(result.added)
            self._reload_sheet()
            self.ctx.viewer.clear_region()
            self._update_find_ui()
            text = f"Found {len(result.added)} stamp(s)."
            skipped = []
            if result.n_cut:
                skipped.append(f"{result.n_cut} cut by the region's border")
            if result.n_existing:
                skipped.append(f"{result.n_existing} already on the sheet")
            if skipped:
                text += " Left out: " + ", ".join(skipped) + "."
            if not result.added:
                text += " Try a region with more background around the stamps."
            self.banner.show_message(text, "success" if result.added else "warning")
            self.changed.emit()

        self.runner.run(
            workflow.detect_stamps_in_region,
            root,
            project_id,
            sample_id,
            region,
            description="Detecting stamps…",
            on_done=done,
            error_title="Could not detect stamps",
        )

    def _undo_detection(self) -> None:
        if not self._detections:
            return
        stamp_ids = self._detections[-1]
        root = self.ctx.library_root
        worked_on = [
            stamp_label(s)
            for s in stamp_ids
            if (st := project_mod.load_stamp(root, self.project_id, s)).perforation.auto
            or st.perforation.manual
            or st.colour.clusters
            or st.catalogue_numbers
        ]
        if worked_on:
            answer = QMessageBox.question(
                self.window(),
                "Undo Detection",
                f"Remove the {len(stamp_ids)} stamps of the last detection?\n\n"
                f"{', '.join(worked_on)} already have measurements or details, "
                "which are removed with them.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._end_edit(show_boxes=False)
        workflow.remove_stamps(root, self.project_id, stamp_ids)
        self._detections.pop()
        self._reload_sheet()
        self._update_find_ui()
        self.changed.emit()
        self.banner.show_message(
            f"Removed the {len(stamp_ids)} stamp(s) of that detection.", "info"
        )

    def _reload_sheet(self) -> None:
        self._sample = project_mod.load_sample(
            self.ctx.library_root, self.project_id, self.sample_id
        )
        self._populate_list()
        if self._editing is None and not self._adding:
            self._show_all_boxes(fit=False)

    # -- correcting a stamp's rectangle -------------------------------------------

    def _begin_edit(self, stamp_id: str) -> None:
        self._end_region()
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
        self._end_region()
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
            self._reload_sheet()
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
        self._end_region()
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
        self.region_button.setEnabled(not editing)
