"""The reference page: one calibration reference's quality, settings and
diagnostics, with rename/archive/compare (SPEC §4.4)."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QInputDialog,
    QMenu,
    QMessageBox,
    QToolButton,
)

from stampan.core.calibration import reference as ref_mod
from stampan.gui import icons
from stampan.gui.pages.base import AppContext, Page
from stampan.gui.viewer import TOOL_PAN
from stampan.gui.widgets import Banner, ScaledImage, StatCard, button, label

_IDENTITY = ",".join(str(i) for i in range(256))


def _setting_text(value) -> str:
    text = str(value)
    if text == _IDENTITY:
        return "identity (linear)"
    return text if len(text) <= 60 else text[:57] + "…"


class ReferencePage(Page):
    new_reference_requested = Signal()

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(ctx, parent=parent)
        self._ref = None

        self.header.add_action(
            button(
                "New Reference…",
                "plus",
                "primary",
                "Calibrate a new reference from an IT8 scan",
                lambda: self.new_reference_requested.emit(),
            )
        )
        more = QToolButton()
        more.setIcon(icons.icon("edit"))
        more.setToolTip("Rename, archive or compare this reference")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction(icons.icon("edit"), "Rename…", self.rename)
        self.archive_action = menu.addAction(icons.icon("archive"), "Archive", self._toggle_archive)
        menu.addAction(icons.icon("compare"), "Compare With…", self._compare)
        more.setMenu(menu)
        self.header.add_action(more)

        self.banner = Banner()
        self.body_layout.addWidget(self.banner)

        cards = QGridLayout()
        cards.setSpacing(8)
        self.dE_card = StatCard("Colour accuracy")
        self.model_card = StatCard("Colour model")
        self.scale_card = StatCard("Scale")
        self.res_card = StatCard("Resolution")
        for i, card in enumerate((self.dE_card, self.model_card, self.scale_card, self.res_card)):
            cards.addWidget(card, i // 2, i % 2)
        self.body_layout.addLayout(cards)

        self.settings_box = QGroupBox("Scanner settings")
        self.settings_form = QFormLayout(self.settings_box)
        self.body_layout.addWidget(self.settings_box)

        self.body_layout.addWidget(label("IT8 patch location", "section"))
        self.grid_image = ScaledImage(upscale=True, placeholder="No diagnostic image")
        self.body_layout.addWidget(self.grid_image)
        self.body_layout.addWidget(label("Cross-validated error per patch", "section"))
        self.heatmap_image = ScaledImage(placeholder="No diagnostic image")
        self.body_layout.addWidget(self.heatmap_image)
        self.body_layout.addStretch(1)

    def set_reference(self, ref_id: str) -> None:
        root = self.ctx.library_root
        self._ref = ref = ref_mod.load_reference(root, ref_id)
        status = "Archived reference" if ref.archived else "Calibration reference"
        self.header.set_text(ref.name, f"{status} · created {ref.created}")
        self.archive_action.setText("Unarchive" if ref.archived else "Archive")

        if ref.colour_model:
            cv = ref.colour_model.cv_delta_e00
            self.dE_card.set_value(
                f"ΔE00 {cv.get('mean', float('nan')):.2f}",
                f"cross-validated mean · p95 {cv.get('p95', float('nan')):.2f} · "
                f"max {cv.get('max', float('nan')):.2f}",
            )
            self.model_card.set_value(ref.colour_model.kind.replace("_", " "))
        else:
            self.dE_card.set_value("—", "no colour model")
            self.model_card.set_value("—")
        if ref.scale:
            iso = " (isotropic)" if ref.scale.assumed_isotropic else ""
            self.scale_card.set_value(f"{ref.scale.px_per_mm_x:.3f}", f"px/mm{iso}")
        else:
            self.scale_card.set_value("—", "not calibrated")
        dpi = ref.scanner_options.get("resolution")
        self.res_card.set_value(f"{float(dpi):.0f} dpi" if dpi else "—")

        notes = [line for line in ref.notes.splitlines() if line.strip()]
        self.banner.show_message("<br>".join(notes), "info")

        while self.settings_form.rowCount():
            self.settings_form.removeRow(0)
        for key in sorted(ref.scanner_options):
            value = label(_setting_text(ref.scanner_options[key]), "mono")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.settings_form.addRow(key, value)
        self.settings_box.setVisible(bool(ref.scanner_options))

        diag = root / "references" / ref.ref_id / "diagnostics"
        grid = diag / "it8_grid.png"
        heatmap = diag / "colour_model_error_heatmap.png"
        self.grid_image.set_png(grid if grid.exists() else None)
        self.heatmap_image.set_png(heatmap if heatmap.exists() else None)
        self._show_in_viewer(grid)

    def _show_in_viewer(self, path) -> None:
        viewer = self.ctx.viewer
        image = cv2.imread(str(path), cv2.IMREAD_COLOR) if path.exists() else None
        if image is None:
            viewer.clear_image()
            return
        viewer.set_image(np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)))
        viewer.set_tool(TOOL_PAN)

    # -- actions ---------------------------------------------------------------

    def rename(self) -> None:
        if self._ref is None:
            return
        name, ok = QInputDialog.getText(
            self.window(), "Rename Reference", "Name:", text=self._ref.name
        )
        if ok and name.strip():
            self._ref.name = name.strip()
            ref_mod.save_reference_metadata(self.ctx.library_root, self._ref)
            self.set_reference(self._ref.ref_id)
            self.changed.emit()

    def _toggle_archive(self) -> None:
        if self._ref is None:
            return
        self._ref.archived = not self._ref.archived
        ref_mod.save_reference_metadata(self.ctx.library_root, self._ref)
        self.set_reference(self._ref.ref_id)
        self.changed.emit()

    def _compare(self) -> None:
        if self._ref is None:
            return
        others = [
            r
            for r in ref_mod.list_references(self.ctx.library_root)
            if r.ref_id != self._ref.ref_id
        ]
        if not others:
            QMessageBox.information(
                self.window(), "Compare", "There is no other reference to compare with."
            )
            return
        labels = [f"{r.name}  ({r.created})" for r in others]
        choice, ok = QInputDialog.getItem(
            self.window(), "Compare With", "Reference:", labels, editable=False
        )
        if not ok:
            return
        a, b = self._ref, others[labels.index(choice)]
        rows = []
        if a.scale and b.scale:
            rows.append(
                f"Scale difference: x {a.scale.px_per_mm_x - b.scale.px_per_mm_x:+.3f}, "
                f"y {a.scale.px_per_mm_y - b.scale.px_per_mm_y:+.3f} px/mm"
            )
        for r in (a, b):
            if r.colour_model:
                rows.append(
                    f"<b>{r.name}</b>: {r.colour_model.kind}, own cross-validated ΔE00 "
                    f"{r.colour_model.cv_delta_e00.get('mean', float('nan')):.2f}"
                )
        rows.append(
            "<i>Each ΔE00 is that reference's cross-validation against its own IT8 scan — "
            "how well each describes its own scan, not a head-to-head on one patch set.</i>"
        )
        box = QMessageBox(self.window())
        box.setWindowTitle("Compare References")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(f"<b>{a.name}</b> vs <b>{b.name}</b>")
        box.setInformativeText("<br><br>".join(rows))
        box.exec()
