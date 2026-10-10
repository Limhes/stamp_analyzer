"""Perforation tab (SPEC §6): the measured gauge per side, the editable stamp
rectangle on the viewer, the gauge search band, and the diagnostics."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stampan.core import perforation as perforation_mod
from stampan.core import project as project_mod
from stampan.core import workflow
from stampan.core.models import LOW_CONFIDENCE
from stampan.gui.pages.base import AppContext
from stampan.gui.tasks import TaskRunner
from stampan.gui.viewer import TOOL_PERF_BOX
from stampan.gui.widgets import Banner, RangeSlider, ScaledImage, button, label

REMEASURE_DEBOUNCE_MS = 400
_KIND_TEXT = {"imperf": "imperforate", "straight_edge": "straight edge"}


def _fmt_notation(notation: str) -> str:
    return (
        notation.replace(" 1/4", "¼")
        .replace(" 1/2", "½")
        .replace(" 3/4", "¾")
        .replace(" x ", " × ")
    )


class PerforationTab(QWidget):
    changed = Signal()

    def __init__(self, ctx: AppContext, runner: TaskRunner, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.runner = runner
        self.project_id: str | None = None
        self.stamp_id: str | None = None
        self._crop: np.ndarray | None = None
        self._box: np.ndarray | None = None
        self._active = False
        self._gauge_range = perforation_mod.DEFAULT_GAUGE_RANGE

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.measure)
        ctx.viewer.perf_box_changed.connect(self._on_box_edited)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        self.condition_banner = Banner()
        layout.addWidget(self.condition_banner)

        result = QGroupBox("Result")
        result_layout = QVBoxLayout(result)
        self.notation = label("—", "value")
        self.notation.setStyleSheet("font-size: 20pt;")
        self.confidence = label("", "caption")
        result_layout.addWidget(self.notation)
        result_layout.addWidget(self.confidence)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Side", "Result", "Gauge", "Holes"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setFixedHeight(4 * 28 + 30)
        result_layout.addWidget(self.table)
        self.warnings = Banner()
        result_layout.addWidget(self.warnings)
        layout.addWidget(result)

        settings = QGroupBox("Measurement")
        settings_layout = QVBoxLayout(settings)
        settings_layout.addWidget(
            label(
                "Fit the yellow rectangle to the stamp on the image: drag its corners to resize, "
                "the round handle above it to rotate. Narrow the gauge band to where the real "
                "tooth-spacing peak is if a spurious one wins. Changes re-measure automatically.",
                "caption",
                wrap=True,
            )
        )
        band_row = QHBoxLayout()
        band_row.addWidget(label("Gauge search band"))
        band_row.addStretch(1)
        self.band_label = label("", "mono")
        band_row.addWidget(self.band_label)
        settings_layout.addLayout(band_row)
        self.slider = RangeSlider()
        self.slider.set_domain(perforation_mod.GAUGE_MIN, perforation_mod.GAUGE_MAX)
        self.slider.range_changed.connect(self._on_band_changed)
        settings_layout.addWidget(self.slider)
        buttons = QHBoxLayout()
        buttons.addWidget(button("Reset Rectangle", "box", slot=self._reset_box))
        buttons.addStretch(1)
        buttons.addWidget(button("Measure", "run", "primary", slot=self.measure))
        settings_layout.addLayout(buttons)
        layout.addWidget(settings)

        layout.addWidget(label("Diagnostics", "section"))
        self.overview = ScaledImage(placeholder="Not measured yet")
        layout.addWidget(self.overview)
        self.side_images: dict[str, ScaledImage] = {}
        for side in perforation_mod.SIDES:
            layout.addWidget(label(f"{side.capitalize()} edge — profile and spectrum", "caption"))
            image = ScaledImage(upscale=True)
            self.side_images[side] = image
            layout.addWidget(image)
        layout.addStretch(1)

    # -- loading ---------------------------------------------------------------

    def set_stamp(self, project_id: str, stamp_id: str, crop: np.ndarray) -> None:
        self._timer.stop()
        self.project_id, self.stamp_id, self._crop = project_id, stamp_id, crop
        stamp = project_mod.load_stamp(self.ctx.library_root, project_id, stamp_id)
        if stamp.condition == "on_paper":
            self.condition_banner.show_message(
                "Recorded as <b>on paper</b>: the backing paper usually hides part of the "
                "perforation, so this gauge is not expected to be reliable.",
                "warning",
            )
        else:
            self.condition_banner.show_message("")
        box = stamp.perforation.box_override_px
        self._box = (
            np.asarray(box, dtype=np.float64)
            if box is not None
            else perforation_mod.detect_stamp_box(crop)
        )
        self._gauge_range = stamp.perforation.gauge_range or perforation_mod.DEFAULT_GAUGE_RANGE
        self.slider.blockSignals(True)
        self.slider.set_values(*self._gauge_range)
        self.slider.blockSignals(False)
        self._update_band_label()
        self._show_result(stamp)
        if self._active:
            self.ctx.viewer.set_perf_box(self._box)

    def _stamp_dir(self):
        return (
            project_mod.project_dir(self.ctx.library_root, self.project_id)
            / "stamps"
            / self.stamp_id
        )

    def _show_result(self, stamp) -> None:
        sides = stamp.perforation.effective()
        if not sides:
            self.notation.setText("Not measured")
            self.confidence.setText("Fit the rectangle if needed, then Measure.")
            self.table.setRowCount(0)
            self.warnings.show_message("")
        else:
            self.notation.setText(_fmt_notation(perforation_mod.perforation_result_notation(sides)))
            conf = stamp.perforation.meta.confidence
            if conf is None:
                self.confidence.setText("")
            elif conf < LOW_CONFIDENCE:
                self.confidence.setText(f"Confidence {conf:.2f} — low; check the diagnostics below")
            else:
                self.confidence.setText(f"Confidence {conf:.2f}")
            self.table.setRowCount(len(sides))
            for row, (name, side) in enumerate(sides.items()):
                result = _KIND_TEXT.get(side.kind) or _fmt_notation(side.notation or "?")
                gauge = (
                    f"{side.gauge:.2f}" + (f" ± {side.uncertainty:.2f}" if side.uncertainty else "")
                    if side.gauge is not None
                    else "—"
                )
                holes = str(side.hole_count) if side.hole_count is not None else "—"
                for col, text in enumerate((name.capitalize(), result, gauge, holes)):
                    self.table.setItem(row, col, QTableWidgetItem(text))
            warnings = stamp.perforation.meta.warnings or []
            self.warnings.show_message("<br>".join(warnings), "warning")

        diag_dir = self._stamp_dir() / "diagnostics"
        overview = diag_dir / "perforation.png"
        self.overview.set_png(overview if overview.exists() and sides else None)
        for side, image in self.side_images.items():
            path = diag_dir / f"perforation_{side}.png"
            image.set_png(path if path.exists() and sides else None)

    # -- viewer rectangle lifecycle -------------------------------------------

    def activate(self) -> None:
        self._active = True
        self.ctx.viewer.set_tool(TOOL_PERF_BOX)
        if self._box is not None:
            self.ctx.viewer.set_perf_box(self._box)

    def deactivate(self) -> None:
        self._active = False
        self._timer.stop()
        self.ctx.viewer.clear_perf_box()

    def _on_box_edited(self) -> None:
        if not self._active:
            return
        box = self.ctx.viewer.get_perf_box()
        if box is not None:
            self._box = box
            self._timer.start(REMEASURE_DEBOUNCE_MS)

    def _reset_box(self) -> None:
        if self._crop is None:
            return
        self._box = perforation_mod.detect_stamp_box(self._crop)
        if self._active:
            self.ctx.viewer.set_perf_box(self._box)
        self.measure()

    def _on_band_changed(self, lo: float, hi: float) -> None:
        self._gauge_range = (lo, hi)
        self._update_band_label()
        self._timer.start(REMEASURE_DEBOUNCE_MS)

    def _update_band_label(self) -> None:
        lo, hi = self._gauge_range
        self.band_label.setText(f"{lo:.2f} – {hi:.2f}")

    # -- measuring -------------------------------------------------------------

    def measure(self) -> None:
        if self.stamp_id is None:
            return
        project_id, stamp_id = self.project_id, self.stamp_id

        def done(measurement) -> None:
            if (project_id, stamp_id) != (self.project_id, self.stamp_id):
                return  # the user moved on to another stamp meanwhile
            self._show_result(measurement.stamp)
            self.changed.emit()

        self.runner.run(
            workflow.measure_perforation,
            self.ctx.library_root,
            project_id,
            stamp_id,
            box_px=self._box,
            gauge_range=self._gauge_range,
            description="Measuring perforation…",
            on_done=done,
            error_title="Perforation measurement failed",
        )
