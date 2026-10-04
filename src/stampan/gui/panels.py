"""Context panels (SPEC §12).

Sheet-level (project/sample) controls live in ``SheetPanel``, meant for the
left side next to the library tree. Everything else here is stamp-level,
for the right-side context tabs: Stamp (catalogue/notes/condition, the
detected geometry, and a manual bounding-box edit against the sample
sheet), Perforation, and Colour. Each panel is a thin orchestration layer
-- it loads/saves via ``core.project`` and runs the same
``core.detection``/``core.perforation``/``core.colour`` calls the CLI
commands do, on a background ``Worker`` thread. No algorithm lives here.
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from stampan.core import colour as colour_mod
from stampan.core import detection as detection_mod
from stampan.core import models
from stampan.core import perforation as perforation_mod
from stampan.core import philatelic_colours as philatelic_colours_mod
from stampan.core import project as project_mod
from stampan.core.calibration import reference as ref_mod
from stampan.gui._util import find_sample_for_stamp
from stampan.gui.viewer import MODE_INCLUDE, TOOL_PAN, TOOL_PERF_BOX, TOOL_RECT
from stampan.gui.workers import Worker

_PHILATELIC_TABLE = None

#: How long to wait after the last ROI edit before recomputing its colour
#: (SPEC request: live update, not a "Run" button -- but recomputing on
#: every single brush-drag sample would be wasteful and janky).
ROI_DEBOUNCE_MS = 400


class _RangeSlider(QWidget):
    """A plain two-handle range slider over a float domain -- drawn
    directly under a side's profile plot so the user can select the
    stretch the gauge estimate's FFT/hole-regression is run on by eye
    (SPEC §6), instead of typing mm numbers into a dialog."""

    range_changed = Signal(float, float)  # (lo, hi) of the selected sub-range

    _HANDLE_RADIUS = 6
    _HIT_RADIUS = 10

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setMinimumWidth(120)
        self._domain_lo = 0.0
        self._domain_hi = 1.0
        self._val_lo = 0.0
        self._val_hi = 1.0
        self._dragging: int | None = None  # 0 = low handle, 1 = high handle

    def set_domain(self, lo: float, hi: float) -> None:
        self._domain_lo, self._domain_hi = lo, hi if hi > lo else lo + 1e-6
        self.update()

    def set_values(self, lo: float, hi: float) -> None:
        self._val_lo = max(self._domain_lo, min(lo, hi))
        self._val_hi = min(self._domain_hi, max(lo, hi))
        self.update()

    def values(self) -> tuple[float, float]:
        return self._val_lo, self._val_hi

    def _track_rect(self) -> QRectF:
        margin = self._HANDLE_RADIUS + 2
        return QRectF(margin, self.height() / 2 - 1, self.width() - 2 * margin, 2)

    def _x_for(self, value: float) -> float:
        track = self._track_rect()
        span = self._domain_hi - self._domain_lo
        frac = (value - self._domain_lo) / span if span > 0 else 0.0
        return track.left() + frac * track.width()

    def _value_for(self, x: float) -> float:
        track = self._track_rect()
        frac = 0.0 if track.width() <= 0 else (x - track.left()) / track.width()
        frac = max(0.0, min(1.0, frac))
        return self._domain_lo + frac * (self._domain_hi - self._domain_lo)

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(200, 200, 200))
        painter.drawRoundedRect(track, 1, 1)
        x_lo, x_hi = self._x_for(self._val_lo), self._x_for(self._val_hi)
        selected = QRectF(x_lo, track.top(), max(x_hi - x_lo, 0.0), track.height())
        painter.setBrush(QColor(60, 160, 90))
        painter.drawRoundedRect(selected, 1, 1)
        painter.setPen(QColor(40, 40, 40))
        for x in (x_lo, x_hi):
            painter.setBrush(QColor(250, 250, 250))
            painter.drawEllipse(
                QRectF(
                    x - self._HANDLE_RADIUS,
                    self.height() / 2 - self._HANDLE_RADIUS,
                    2 * self._HANDLE_RADIUS,
                    2 * self._HANDLE_RADIUS,
                )
            )
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        x_lo, x_hi = self._x_for(self._val_lo), self._x_for(self._val_hi)
        self._dragging = 0 if abs(x - x_lo) <= abs(x - x_hi) else 1
        self._drag_to(x)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging is not None:
            self._drag_to(event.position().x())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = None

    def _drag_to(self, x: float) -> None:
        value = self._value_for(x)
        if self._dragging == 0:
            self._val_lo = min(value, self._val_hi)
        else:
            self._val_hi = max(value, self._val_lo)
        self.update()
        self.range_changed.emit(self._val_lo, self._val_hi)


def _philatelic_table():
    global _PHILATELIC_TABLE
    if _PHILATELIC_TABLE is None:
        _PHILATELIC_TABLE = philatelic_colours_mod.load_philatelic_colours()
    return _PHILATELIC_TABLE


def _png_to_pixmap(png_bytes: bytes, max_width: int = 420) -> QPixmap:
    arr = cv2.imdecode(np.frombuffer(png_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    h, w = arr.shape[:2]
    image = QImage(np.ascontiguousarray(arr).data, w, h, 3 * w, QImage.Format.Format_RGB888)
    pixmap = QPixmap.fromImage(image.copy())
    if pixmap.width() > max_width:
        pixmap = pixmap.scaledToWidth(max_width, Qt.TransformationMode.SmoothTransformation)
    return pixmap


class _BusyPanel(QWidget):
    """Shared "run this on a worker thread, show a status line" plumbing.

    At most one worker runs at a time per panel. A request that arrives
    while one is still in flight (e.g. a live-update ROI/perforation-box
    edit firing again before the previous recompute finished) is queued,
    replacing any earlier queued request, rather than started immediately --
    starting a second ``Worker`` (a ``QThread``) while dropping the only
    Python reference to the still-running first one destroys it mid-run,
    which crashes the whole application (`QThread: Destroyed while thread
    is still running`), not just raises a catchable Python exception.
    """

    changed = Signal()  # emitted after a successful run, so the tree can refresh

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None
        self._pending_call: tuple | None = None
        self.status_label = QLabel("")

    def _run(self, fn, *args, on_done=None, on_fail=None, **kwargs) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._pending_call = (fn, args, kwargs, on_done, on_fail)
            self.status_label.setText("Running... (queued another update)")
            return
        self._start_worker(fn, *args, on_done=on_done, on_fail=on_fail, **kwargs)

    def _start_worker(self, fn, *args, on_done=None, on_fail=None, **kwargs) -> None:
        self.status_label.setText("Running...")
        worker = Worker(fn, *args, **kwargs)
        worker.signals.finished.connect(lambda result: self._on_finished(result, on_done))
        worker.signals.failed.connect(lambda tb: self._on_failed(tb, on_fail))
        worker.signals.progress.connect(self.status_label.setText)
        self._worker = worker  # keep a reference so it isn't garbage-collected mid-run
        worker.start()

    def _on_finished(self, result, on_done) -> None:
        self.status_label.setText("Done.")
        if on_done is not None:
            on_done(result)
        self.changed.emit()
        self._run_pending()

    def _on_failed(self, traceback_text: str, on_fail) -> None:
        self.status_label.setText("Failed -- see error dialog.")
        if on_fail is not None:
            on_fail()
        QMessageBox.critical(self, "Operation failed", traceback_text)
        self._run_pending()

    def _run_pending(self) -> None:
        if self._pending_call is None:
            return
        fn, args, kwargs, on_done, on_fail = self._pending_call
        self._pending_call = None
        self._start_worker(fn, *args, on_done=on_done, on_fail=on_fail, **kwargs)


class SheetPanel(_BusyPanel):
    """Project/sample-level controls (SPEC §12: "everything project and
    sheet level on the left"). Lives next to the library tree, not in the
    stamp-level tabs."""

    def __init__(self, library_root_getter, parent=None) -> None:
        super().__init__(parent)
        self._library_root_getter = library_root_getter
        self._current_project_id: str | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Sheet</b>"))

        self.import_button = QPushButton("Import sample sheet...")
        self.import_button.clicked.connect(self._import_sheet)
        layout.addWidget(self.import_button)

        add_stamp_button = QPushButton("Add stamp manually...")
        add_stamp_button.setEnabled(False)
        add_stamp_button.setToolTip("Not implemented yet: manually mark a stamp detect missed.")
        layout.addWidget(add_stamp_button)

        scanner_button = QPushButton("Scan...")
        scanner_button.setEnabled(False)
        scanner_button.setToolTip("Not implemented yet: drive a SANE scanner from the GUI.")
        layout.addWidget(scanner_button)

        layout.addWidget(self.status_label)
        layout.addStretch(1)

    def set_project(self, project_id: str | None) -> None:
        self._current_project_id = project_id

    def _import_sheet(self) -> None:
        library_root = self._library_root_getter()
        if self._current_project_id is None:
            QMessageBox.information(self, "Import", "Select or create a project first.")
            return
        sample_path, _ = QFileDialog.getOpenFileName(
            self, "Sample sheet TIFF", "", "TIFF (*.tif *.tiff)"
        )
        if not sample_path:
            return
        refs = ref_mod.list_references(library_root)
        if not refs:
            QMessageBox.warning(self, "Import", "No references available.")
            return
        if len(refs) == 1:
            ref_id = refs[0].ref_id
        else:
            labels = [f"{r.name} ({r.ref_id})" for r in refs]
            choice, ok = QInputDialog.getItem(
                self, "Reference", "Reference to use for this sheet:", labels, editable=False
            )
            if not ok:
                return
            ref_id = refs[labels.index(choice)].ref_id

        project_id = self._current_project_id

        def do_detect(progress=None):
            from stampan.core.io import read_tiff
            from stampan.core.models import GeometryResult, Stamp

            ref = ref_mod.load_reference(library_root, ref_id)
            if ref.scale is None:
                raise RuntimeError(f"reference {ref_id} has no scale calibration")
            scan_image, scan_metadata = read_tiff(sample_path)
            scan_metadata.ref_id = ref_id
            project = project_mod.load_project(library_root, project_id)
            sample = project_mod.add_sample(
                library_root, project, scan_image, scan_metadata, ref_id
            )

            if progress:
                progress("detecting stamps...")
            result = detection_mod.detect_stamps(
                scan_image, ref.scale.px_per_mm_x, ref.scale.px_per_mm_y
            )
            overview_png = detection_mod.render_overview(scan_image, result)
            background_level = float(np.percentile(scan_image[::8, ::8], 1))

            for i, detected in enumerate(result.stamps):
                crop, transform = detection_mod.extract_crop(
                    scan_image,
                    detected,
                    ref.scale.px_per_mm_x,
                    ref.scale.px_per_mm_y,
                    background_level=background_level,
                )
                stamp = Stamp(
                    stamp_id=project_mod.next_stamp_id(sample, i),
                    geometry=GeometryResult(
                        width_mm=detected.size_px[0] / ref.scale.px_per_mm_x,
                        height_mm=detected.size_px[1] / ref.scale.px_per_mm_y,
                        rotation_deg=transform.angle_deg,
                        sample_centre_px=transform.sample_centre_px,
                        crop_size_px=transform.crop_size_px,
                    ),
                )
                project_mod.save_stamp(library_root, sample, stamp, crop)

            project_dir = project_mod.project_dir(library_root, project.project_id)
            (project_dir / "diagnostics").mkdir(parents=True, exist_ok=True)
            (project_dir / "diagnostics" / f"{sample.sample_id}_overview.png").write_bytes(
                overview_png
            )
            project_mod.save_sample(library_root, sample)
            return len(result.stamps)

        self._run(do_detect, on_done=lambda n: self.status_label.setText(f"Detected {n} stamp(s)."))


class PerforationPanel(_BusyPanel):
    """Owns the viewer's editable perforation-box overlay (SPEC §6) while
    this tab is active: seeded from the auto-detected stamp rectangle (or a
    saved override), draggable by its corner/rotate handles, with edits
    debounced into an automatic re-run -- "once that is done, you can
    proceed with the rest"."""

    def __init__(self, library_root_getter, viewer, parent=None) -> None:
        super().__init__(parent)
        self._library_root_getter = library_root_getter
        self._viewer = viewer
        self._project_id: str | None = None
        self._stamp_id: str | None = None
        self._last_sides: dict = {}
        self._crop: np.ndarray | None = None
        self._pending_box: np.ndarray | None = None
        self._box_active = False  # is our overlay currently the one shown on the viewer?

        self._box_timer = QTimer(self)
        self._box_timer.setSingleShot(True)
        self._box_timer.timeout.connect(self._run_perforation)
        self._viewer.perf_box_changed.connect(self._on_box_edited)

        self._roi_override: dict[str, list[tuple[float, float]]] = {}
        self._roi_timer = QTimer(self)
        self._roi_timer.setSingleShot(True)
        self._roi_timer.timeout.connect(self._run_perforation)

        layout = QVBoxLayout(self)

        self.condition_note = QLabel("")
        self.condition_note.setWordWrap(True)
        self.condition_note.setStyleSheet("color: #9a5700; font-style: italic;")
        self.condition_note.setVisible(False)
        layout.addWidget(self.condition_note)

        layout.addWidget(
            QLabel(
                "<i>Drag the yellow rectangle's corner handles to resize it, "
                "the cyan handle to rotate it, so it hugs the stamp's true "
                "edges. Drag a side's slider handles to pick the stretch of "
                "its profile the gauge estimate is computed from. Both "
                "re-run measurement automatically.</i>"
            )
        )

        button_row = QHBoxLayout()
        self.run_button = QPushButton("Run perforation")
        self.run_button.clicked.connect(self._run_perforation)
        self.reset_box_button = QPushButton("Reset box to auto-detected")
        self.reset_box_button.clicked.connect(self._reset_box)
        button_row.addWidget(self.run_button)
        button_row.addWidget(self.reset_box_button)
        layout.addLayout(button_row)
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Side", "Kind", "Gauge", "Notation", "Holes"])
        layout.addWidget(self.table)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.overview_label = QLabel()
        content_layout.addWidget(self.overview_label)

        # One row per side, stacked vertically -- not side-by-side columns,
        # which needed far more horizontal space than the context dock
        # actually has (most sliders ended up scrolled out of reach).
        self.side_images: dict[str, QLabel] = {}
        self.side_sliders: dict[str, _RangeSlider] = {}
        for side_name in perforation_mod.SIDES:
            content_layout.addWidget(QLabel(f"<b>{side_name}</b>"))
            img_label = QLabel("(run perforation to see this side's profile)")
            img_label.setWordWrap(True)
            img_label.setScaledContents(False)
            content_layout.addWidget(img_label)
            self.side_images[side_name] = img_label
            slider = _RangeSlider()
            slider.setEnabled(False)
            slider.range_changed.connect(
                lambda lo, hi, s=side_name: self._on_slider_changed(s, lo, hi)
            )
            content_layout.addWidget(slider)
            self.side_sliders[side_name] = slider
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, stretch=1)

    def set_stamp(self, project_id: str, stamp_id: str) -> None:
        self._project_id, self._stamp_id = project_id, stamp_id
        self._last_sides = {}
        library_root = self._library_root_getter()
        stamp = project_mod.load_stamp(library_root, project_id, stamp_id)
        self._crop = project_mod.load_stamp_crop(library_root, project_id, stamp_id)

        on_paper = stamp.condition == "on_paper"
        self.condition_note.setVisible(on_paper)
        if on_paper:
            self.condition_note.setText(
                "Recorded as on paper (Stamp tab): the paper backing typically "
                "obscures the perforation teeth, so a gauge measured here is "
                "not expected to be reliable. Switch to soaked off there if "
                "this stamp's teeth are actually fully visible."
            )

        box = stamp.perforation.box_override_px
        self._pending_box = (
            np.asarray(box, dtype=np.float64)
            if box is not None
            else perforation_mod.detect_stamp_box(self._crop)
        )
        self._roi_override = {k: list(v) for k, v in stamp.perforation.roi_override_mm.items()}

        self._populate_table(stamp.perforation.effective())
        diag = stamp.perforation.meta.diagnostic_image
        self.overview_label.clear()
        if diag:
            path = project_mod.project_dir(library_root, project_id) / "stamps" / stamp_id / diag
            if path.exists():
                self.overview_label.setPixmap(_png_to_pixmap(path.read_bytes()))

        for side_name in perforation_mod.SIDES:
            self.side_images[side_name].setText("(run perforation to see this side's profile)")
            self.side_images[side_name].setPixmap(QPixmap())
            self.side_sliders[side_name].setEnabled(False)

        if self._box_active:
            self._viewer.set_perf_box(self._pending_box)

    # -- box overlay lifecycle, driven by the tab becoming current ---------

    def activate(self) -> None:
        """Called when this tab becomes the visible one: show our box."""
        self._box_active = True
        if self._pending_box is not None:
            self._viewer.set_perf_box(self._pending_box)

    def deactivate(self) -> None:
        """Called when leaving this tab: hand the viewer back."""
        self._box_active = False
        self._box_timer.stop()
        self._viewer.clear_perf_box()

    def _on_box_edited(self) -> None:
        if not self._box_active:
            return
        box = self._viewer.get_perf_box()
        if box is None:
            return
        self._pending_box = box
        self._box_timer.start(ROI_DEBOUNCE_MS)

    def _reset_box(self) -> None:
        if self._crop is None:
            return
        self._pending_box = perforation_mod.detect_stamp_box(self._crop)
        if self._box_active:
            self._viewer.set_perf_box(self._pending_box)
        self._run_perforation()

    def _populate_table(self, sides: dict) -> None:
        self.table.setRowCount(len(sides))
        for row, (name, side) in enumerate(sides.items()):
            values = [
                name,
                side.kind,
                f"{side.gauge:.2f}" if side.gauge is not None else "",
                side.notation or "",
                str(side.hole_count) if side.hole_count is not None else "",
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def _run_perforation(self) -> None:
        if self._stamp_id is None:
            return
        library_root = self._library_root_getter()
        project_id, stamp_id = self._project_id, self._stamp_id
        box = self._pending_box
        roi_override = dict(self._roi_override)

        def do_run(progress=None):
            sample_id = find_sample_for_stamp(library_root, project_id, stamp_id)
            sample = project_mod.load_sample(library_root, project_id, sample_id)
            ref = ref_mod.load_reference(library_root, sample.ref_id)
            crop = project_mod.load_stamp_crop(library_root, project_id, stamp_id)
            stamp = project_mod.load_stamp(library_root, project_id, stamp_id)
            analysis = perforation_mod.analyse_perforation(
                crop,
                ref.scale.px_per_mm_x,
                ref.scale.px_per_mm_y,
                manual_roi=roi_override or None,
                manual_box_px=box,
                condition=stamp.condition,
                multiplicity=stamp.multiplicity,
                multiplicity_count=stamp.multiplicity_count,
            )
            diagnostic_png = perforation_mod.render_diagnostic(crop, analysis)
            side_pngs = {
                f"perforation_{name}.png": perforation_mod.render_side_profile(side)
                for name, side in analysis.sides.items()
            }
            stamp.perforation.auto = {
                name: side.to_persisted() for name, side in analysis.sides.items()
            }
            stamp.perforation.box_override_px = (
                [(float(x), float(y)) for x, y in box] if box is not None else None
            )
            stamp.perforation.roi_override_mm = roi_override
            stamp.perforation.meta.confidence = analysis.confidence
            stamp.perforation.meta.warnings = analysis.warnings
            stamp.perforation.meta.diagnostic_image = "diagnostics/perforation.png"
            project_mod.save_stamp_results(
                library_root,
                project_id,
                stamp,
                diagnostics={"perforation.png": diagnostic_png, **side_pngs},
            )
            return stamp, analysis.sides

        self._run(do_run, on_done=self._on_perforation_done)

    def _on_perforation_done(self, result) -> None:
        stamp, sides = result
        self._last_sides = sides
        self._populate_table(stamp.perforation.effective())
        diag = stamp.perforation.meta.diagnostic_image
        if diag and self._project_id and self._stamp_id:
            library_root = self._library_root_getter()
            path = (
                project_mod.project_dir(library_root, self._project_id)
                / "stamps"
                / self._stamp_id
                / diag
            )
            if path.exists():
                self.overview_label.setPixmap(_png_to_pixmap(path.read_bytes()))

        for side_name, side in sides.items():
            img_label = self.side_images[side_name]
            slider = self.side_sliders[side_name]
            png = perforation_mod.render_side_profile(side)
            img_label.setPixmap(_png_to_pixmap(png, max_width=320))
            img_label.setText("")
            if len(side.profile_s_mm):
                s_lo, s_hi = float(side.profile_s_mm[0]), float(side.profile_s_mm[-1])
                roi = self._roi_override.get(side_name)
                if roi:
                    val_lo = min(start for start, _ in roi)
                    val_hi = max(end for _, end in roi)
                else:
                    val_lo, val_hi = s_lo, s_hi
                slider.blockSignals(True)
                slider.set_domain(s_lo, s_hi)
                slider.set_values(val_lo, val_hi)
                slider.blockSignals(False)
                slider.setEnabled(True)
            else:
                slider.setEnabled(False)

    # -- per-side ROI range slider ------------------------------------------

    def _on_slider_changed(self, side_name: str, lo: float, hi: float) -> None:
        self._roi_override[side_name] = [(lo, hi)]
        self._roi_timer.start(ROI_DEBOUNCE_MS)


class ColourPanel(_BusyPanel):
    """Draw-a-ROI-per-colour workflow (SPEC §8 rework): each row is one
    user-named colour with its own ROI mask, measured directly from that
    ROI's pixels -- no GMM clustering, no N/tau. Edits in the viewer
    recompute automatically (debounced), no "Run" button."""

    def __init__(self, library_root_getter, viewer, parent=None) -> None:
        super().__init__(parent)
        self._library_root_getter = library_root_getter
        self._viewer = viewer
        self._project_id: str | None = None
        self._stamp_id: str | None = None
        self._clusters: list = []  # current stamp.colour.clusters, kept in sync

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._recompute_pending)
        self._pending_roi_name: str | None = None

        viewer.roi_changed.connect(self._on_roi_changed)

        layout = QVBoxLayout(self)

        button_row = QHBoxLayout()
        self.add_button = QPushButton("Add colour...")
        self.add_button.clicked.connect(self._add_colour)
        self.remove_button = QPushButton("Remove colour")
        self.remove_button.clicked.connect(self._remove_colour)
        button_row.addWidget(self.add_button)
        button_row.addWidget(self.remove_button)
        layout.addLayout(button_row)
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Colour", "Philatelic name", "L* C* h"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self.table)

        self.scatter_label = QLabel("Select a colour to see its a*b*/C*-L* scatter.")
        self.scatter_label.setWordWrap(True)
        scroll = QScrollArea()
        scroll.setWidget(self.scatter_label)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, stretch=1)

    # -- stamp loading -------------------------------------------------------

    def set_stamp(self, project_id: str, stamp_id: str) -> None:
        self._project_id, self._stamp_id = project_id, stamp_id
        library_root = self._library_root_getter()
        stamp = project_mod.load_stamp(library_root, project_id, stamp_id)
        # Only colours with a mask_path were actually drawn as a ROI in this
        # GUI -- a cluster without one is left over from the old GMM-based
        # auto-clustering (``core.colour.analyse_colour``, still used by the
        # CLI's ``colour`` command) and corresponds to no ROI the user ever
        # picked. Showing those here as if they were real selections gave no
        # way to tell where a sample was taken from, so they're dropped.
        self._clusters = [c for c in stamp.colour.clusters if c.mask_path]
        self._populate_table()
        self.scatter_label.setText("Select a colour to see its a*b*/C*-L* scatter.")
        self.scatter_label.setPixmap(QPixmap())

    def _populate_table(self) -> None:
        self.table.setRowCount(len(self._clusters))
        for row, c in enumerate(self._clusters):
            lch = (
                f"{c.mean_lch[0]:.1f} {c.mean_lch[1]:.1f} {c.mean_lch[2]:.1f}" if c.mean_lch else ""
            )
            values = [c.label, c.philatelic_name or "", lch]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0 and c.srgb_swatch:
                    item.setBackground(QColor(*c.srgb_swatch))
                self.table.setItem(row, col, item)

    # -- selection -> active ROI in the viewer -----------------------------

    def _on_row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        cluster = self._clusters[rows[0].row()]
        library_root = self._library_root_getter()
        initial_mask = None
        if cluster.mask_path:
            initial_mask = project_mod.load_stamp_roi_mask(
                library_root, self._project_id, cluster.mask_path
            )
        self._viewer.set_tool(TOOL_RECT)
        self._viewer.set_mode(MODE_INCLUDE)
        self._viewer.set_active_roi(cluster.label, initial_mask=initial_mask)
        self._show_scatter(cluster.label)

    def _show_scatter(self, name: str) -> None:
        library_root = self._library_root_getter()
        mask = self._viewer.get_mask(name)
        if mask is None:
            self.scatter_label.setText(f"'{name}' has no ROI drawn yet.")
            self.scatter_label.setPixmap(QPixmap())
            return
        crop = project_mod.load_stamp_crop(library_root, self._project_id, self._stamp_id)
        model = self._colour_model()
        if model is None:
            return
        png = colour_mod.render_roi_scatter(crop, model.predict_lab, mask, name)
        self.scatter_label.setPixmap(_png_to_pixmap(png, max_width=560))
        self.scatter_label.setText("")

    def _colour_model(self):
        library_root = self._library_root_getter()
        sample_id = find_sample_for_stamp(library_root, self._project_id, self._stamp_id)
        sample = project_mod.load_sample(library_root, self._project_id, sample_id)
        ref = ref_mod.load_reference(library_root, sample.ref_id)
        if ref.colour_model is None:
            QMessageBox.warning(self, "Colour", "This stamp's reference has no colour model.")
            return None
        return ref_mod.load_colour_model(library_root, sample.ref_id)

    # -- add / remove --------------------------------------------------------

    def _add_colour(self) -> None:
        if self._stamp_id is None:
            return
        name, ok = QInputDialog.getText(self, "Add colour", "Name (e.g. 'frame', 'centre'):")
        if not ok or not name.strip():
            return
        name = name.strip()
        if any(c.label == name for c in self._clusters):
            QMessageBox.warning(self, "Add colour", f"'{name}' already exists.")
            return
        from stampan.core.models import ColourCluster

        self._clusters.append(ColourCluster(label=name))
        self._populate_table()
        self._select_row_by_name(name)
        self._viewer.set_active_roi(name)

    def _remove_colour(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        cluster = self._clusters.pop(rows[0].row())
        self._viewer.remove_roi(cluster.label)
        if cluster.mask_path:
            library_root = self._library_root_getter()
            path = project_mod.project_dir(library_root, self._project_id) / cluster.mask_path
            path.unlink(missing_ok=True)
        self._save_clusters()
        self._populate_table()
        self.scatter_label.setText("Select a colour to see its a*b*/C*-L* scatter.")
        self.scatter_label.setPixmap(QPixmap())

    def _select_row_by_name(self, name: str) -> None:
        for row, c in enumerate(self._clusters):
            if c.label == name:
                self.table.selectRow(row)
                return

    # -- live recompute on ROI edit -----------------------------------------

    def _on_roi_changed(self, name: str) -> None:
        if not any(c.label == name for c in self._clusters):
            return
        self._pending_roi_name = name
        self._debounce.start(ROI_DEBOUNCE_MS)

    def _recompute_pending(self) -> None:
        name = self._pending_roi_name
        if name is None or self._stamp_id is None:
            return
        library_root = self._library_root_getter()
        project_id, stamp_id = self._project_id, self._stamp_id
        mask = self._viewer.get_mask(name)
        if mask is None:
            return

        def do_measure(progress=None):
            crop = project_mod.load_stamp_crop(library_root, project_id, stamp_id)
            model_ref = _colour_model_for(library_root, project_id, stamp_id)
            if model_ref is None:
                return None, ["no usable reference/colour model for this stamp"]
            cluster, warnings = colour_mod.measure_roi_colour(
                crop, model_ref.predict_lab, mask, name, philatelic_table=_philatelic_table()
            )
            mask_path = project_mod.save_stamp_roi_mask(
                library_root, project_id, stamp_id, name, mask
            )
            if cluster is not None:
                cluster.mask_path = mask_path
            return cluster, warnings

        self._run(do_measure, on_done=lambda res: self._apply_measurement(name, *res))

    def _apply_measurement(self, name: str, cluster, warnings: list[str]) -> None:
        for i, c in enumerate(self._clusters):
            if c.label == name:
                if cluster is not None:
                    self._clusters[i] = cluster
                break
        self._save_clusters()
        self._populate_table()
        self._select_row_by_name(name)
        # _select_row_by_name re-selects a row that may already be selected,
        # which Qt does not always turn into an itemSelectionChanged signal
        # -- show explicitly rather than relying on that side effect, so the
        # plot always reflects the ROI just drawn/edited (not a stale "no
        # ROI drawn yet" from before the user finished drawing).
        self._show_scatter(name)
        if warnings:
            self.status_label.setText("; ".join(warnings))

    def _save_clusters(self) -> None:
        library_root = self._library_root_getter()
        stamp = project_mod.load_stamp(library_root, self._project_id, self._stamp_id)
        stamp.colour.n_inks = len(self._clusters)
        stamp.colour.clusters = self._clusters
        stamp.colour.meta.confidence = 1.0 if self._clusters else 0.0
        project_mod.save_stamp_results(library_root, self._project_id, stamp)


def _colour_model_for(library_root, project_id: str, stamp_id: str):
    sample_id = find_sample_for_stamp(library_root, project_id, stamp_id)
    sample = project_mod.load_sample(library_root, project_id, sample_id)
    ref = ref_mod.load_reference(library_root, sample.ref_id)
    if ref.colour_model is None:
        return None
    return ref_mod.load_colour_model(library_root, sample.ref_id)


_CONDITION_LABELS = {
    None: "(not recorded)",
    models.CONDITION_ON_PAPER: "On paper",
    models.CONDITION_SOAKED_OFF: "Soaked off",
}
_MULTIPLICITY_LABELS = {
    None: "(not recorded)",
    models.MULTIPLICITY_SINGLE: "Single",
    models.MULTIPLICITY_PAIR: "Pair",
    models.MULTIPLICITY_STRIP: "Strip",
    models.MULTIPLICITY_BLOCK: "Block",
}


class StampPanel(_BusyPanel):
    """Catalogue number(s), country/issue, condition, multiplicity, free
    notes, and the (read-only, for now) detected geometry -- SPEC §12: the
    merged home for all stamp-level metadata, first among the context tabs
    since it's the natural starting point once a stamp is selected.

    Condition (on paper / soaked off) and multiplicity (single/pair/strip/
    block of N) feed directly into the Perforation tab's measurement
    (``core.perforation.analyse_perforation``): on paper caps confidence and
    adds a warning (the backing paper's own cut/torn edge can contaminate
    the segmentation), and a non-single multiplicity adds a warning that an
    imperf/straight_edge side may actually be a line shared with a
    neighbour rather than the stamp's own edge.

    The detected geometry's bounding box can be corrected by hand: "Edit
    bounding box on sheet..." swaps the shared viewer to the whole sample
    sheet with the stamp's box shown as the same draggable rectangle
    perforation uses (corner handles resize, the cyan handle rotates), and
    Apply re-extracts the crop from the edited box, replacing crop.tif.
    """

    def __init__(self, library_root_getter, viewer, reload_viewer, parent=None) -> None:
        super().__init__(parent)
        self._library_root_getter = library_root_getter
        self._viewer = viewer
        self._reload_viewer = reload_viewer  # callback: re-show this stamp's own crop
        self._project_id: str | None = None
        self._stamp_id: str | None = None
        self._editing_box = False

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.width_label = QLabel("")
        self.height_label = QLabel("")
        self.rotation_label = QLabel("")
        form.addRow("Width", self.width_label)
        form.addRow("Height", self.height_label)
        form.addRow("Rotation applied", self.rotation_label)

        box_row = QHBoxLayout()
        self.edit_box_button = QPushButton("Edit bounding box on sheet...")
        self.edit_box_button.clicked.connect(self._start_box_edit)
        self.apply_box_button = QPushButton("Apply")
        self.apply_box_button.setEnabled(False)
        self.apply_box_button.clicked.connect(self._apply_box_edit)
        self.cancel_box_button = QPushButton("Cancel")
        self.cancel_box_button.setEnabled(False)
        self.cancel_box_button.clicked.connect(self._cancel_box_edit)
        box_row.addWidget(self.edit_box_button)
        box_row.addWidget(self.apply_box_button)
        box_row.addWidget(self.cancel_box_button)
        form.addRow("Bounding box", box_row)

        self.catalogue_edit = QLineEdit()
        form.addRow("Catalogue numbers (comma-separated)", self.catalogue_edit)
        self.country_edit = QLineEdit()
        form.addRow("Country / issue", self.country_edit)

        self.condition_combo = QComboBox()
        self.condition_combo.addItems(list(_CONDITION_LABELS.values()))
        form.addRow("Condition", self.condition_combo)

        self.multiplicity_combo = QComboBox()
        self.multiplicity_combo.addItems(list(_MULTIPLICITY_LABELS.values()))
        self.multiplicity_combo.currentTextChanged.connect(self._on_multiplicity_changed)
        form.addRow("Multiplicity", self.multiplicity_combo)

        self.multiplicity_count_spin = QSpinBox()
        self.multiplicity_count_spin.setRange(2, 50)
        self.multiplicity_count_spin.setEnabled(False)
        form.addRow("Stamp count", self.multiplicity_count_spin)

        layout.addLayout(form)
        self.notes_edit = QTextEdit()
        layout.addWidget(self.notes_edit, stretch=1)
        save_row = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._save)
        save_row.addStretch(1)
        save_row.addWidget(self.save_button)
        layout.addLayout(save_row)
        layout.addWidget(self.status_label)

    def _on_multiplicity_changed(self, label: str) -> None:
        self.multiplicity_count_spin.setEnabled(
            label
            not in (_MULTIPLICITY_LABELS[None], _MULTIPLICITY_LABELS[models.MULTIPLICITY_SINGLE])
        )

    def set_stamp(self, project_id: str, stamp_id: str) -> None:
        if self._editing_box:
            self._cancel_box_edit()
        self._project_id, self._stamp_id = project_id, stamp_id
        library_root = self._library_root_getter()
        stamp = project_mod.load_stamp(library_root, project_id, stamp_id)
        g = stamp.geometry
        self.width_label.setText(f"{g.width_mm:.2f} mm" if g.width_mm is not None else "—")
        self.height_label.setText(f"{g.height_mm:.2f} mm" if g.height_mm is not None else "—")
        self.rotation_label.setText(f"{g.rotation_deg:.2f}°" if g.rotation_deg is not None else "—")
        self.catalogue_edit.setText(", ".join(stamp.catalogue_numbers))
        self.country_edit.setText(stamp.country_issue or "")
        self.notes_edit.setPlainText(stamp.notes)
        self.condition_combo.setCurrentText(_CONDITION_LABELS.get(stamp.condition, stamp.condition))
        self.multiplicity_combo.setCurrentText(
            _MULTIPLICITY_LABELS.get(stamp.multiplicity, stamp.multiplicity)
        )
        self.multiplicity_count_spin.setValue(stamp.multiplicity_count or 2)

    # -- manual bounding-box edit, on the sample sheet ----------------------

    def _sample_and_scale(self):
        library_root = self._library_root_getter()
        sample_id = find_sample_for_stamp(library_root, self._project_id, self._stamp_id)
        sample = project_mod.load_sample(library_root, self._project_id, sample_id)
        ref = ref_mod.load_reference(library_root, sample.ref_id)
        if ref.scale is None:
            QMessageBox.warning(self, "Bounding box", "This stamp's reference has no scale.")
            return None
        return sample, ref.scale.px_per_mm_x, ref.scale.px_per_mm_y

    def _start_box_edit(self) -> None:
        if self._stamp_id is None:
            return
        found = self._sample_and_scale()
        if found is None:
            return
        sample, px_per_mm_x, px_per_mm_y = found
        library_root = self._library_root_getter()
        scan_image, _meta = project_mod.load_sample_scan(library_root, self._project_id, sample)
        stamp = project_mod.load_stamp(library_root, self._project_id, self._stamp_id)
        g = stamp.geometry
        if g.sample_centre_px is None or g.width_mm is None or g.height_mm is None:
            QMessageBox.warning(self, "Bounding box", "No detected geometry to edit.")
            return

        self._viewer.set_image(scan_image, px_per_mm_x, px_per_mm_y)
        box = cv2.boxPoints(
            (
                g.sample_centre_px,
                (g.width_mm * px_per_mm_x, g.height_mm * px_per_mm_y),
                g.rotation_deg or 0.0,
            )
        )
        self._viewer.set_perf_box(box)
        self._viewer.set_tool(TOOL_PERF_BOX)
        self._editing_box = True
        self.edit_box_button.setEnabled(False)
        self.apply_box_button.setEnabled(True)
        self.cancel_box_button.setEnabled(True)
        self.status_label.setText("Drag the rectangle to match the stamp's true edges, then Apply.")

    def _end_box_edit(self) -> None:
        self._editing_box = False
        self._viewer.clear_perf_box()
        self._viewer.set_tool(TOOL_PAN)
        self.edit_box_button.setEnabled(True)
        self.apply_box_button.setEnabled(False)
        self.cancel_box_button.setEnabled(False)
        self._reload_viewer()

    def _cancel_box_edit(self) -> None:
        self._end_box_edit()
        self.status_label.setText("Bounding box edit cancelled.")

    def _apply_box_edit(self) -> None:
        if self._stamp_id is None:
            return
        rect = self._viewer.get_perf_box_rect()
        if rect is None:
            return
        found = self._sample_and_scale()
        if found is None:
            return
        sample, px_per_mm_x, px_per_mm_y = found
        centre_px, size_px, angle_deg = rect
        library_root = self._library_root_getter()
        project_id, stamp_id = self._project_id, self._stamp_id

        def do_apply(progress=None):
            scan_image, _meta = project_mod.load_sample_scan(library_root, project_id, sample)
            detected = detection_mod.DetectedStamp(
                index=0,
                centre_px=centre_px,
                size_px=size_px,
                angle_deg=angle_deg,
                area_px=size_px[0] * size_px[1],
                fill_ratio=1.0,
                confidence=1.0,
            )
            background_level = float(np.percentile(scan_image[::8, ::8], 1))
            crop, transform = detection_mod.extract_crop(
                scan_image, detected, px_per_mm_x, px_per_mm_y, background_level=background_level
            )
            stamp = project_mod.load_stamp(library_root, project_id, stamp_id)
            stamp.geometry.width_mm = size_px[0] / px_per_mm_x
            stamp.geometry.height_mm = size_px[1] / px_per_mm_y
            stamp.geometry.rotation_deg = transform.angle_deg
            stamp.geometry.sample_centre_px = transform.sample_centre_px
            stamp.geometry.crop_size_px = transform.crop_size_px
            project_mod.save_stamp(library_root, sample, stamp, crop)
            return stamp

        self._run(do_apply, on_done=self._on_box_applied)

    def _on_box_applied(self, stamp) -> None:
        self._end_box_edit()
        g = stamp.geometry
        self.width_label.setText(f"{g.width_mm:.2f} mm" if g.width_mm is not None else "—")
        self.height_label.setText(f"{g.height_mm:.2f} mm" if g.height_mm is not None else "—")
        self.rotation_label.setText(f"{g.rotation_deg:.2f}°" if g.rotation_deg is not None else "—")
        self.status_label.setText(
            "Bounding box applied -- crop re-extracted. Existing perforation/colour "
            "results now refer to the old crop; re-run them for this stamp."
        )
        self.changed.emit()

    def _save(self) -> None:
        if self._stamp_id is None:
            return
        library_root = self._library_root_getter()
        stamp = project_mod.load_stamp(library_root, self._project_id, self._stamp_id)
        stamp.catalogue_numbers = [
            s.strip() for s in self.catalogue_edit.text().split(",") if s.strip()
        ]
        stamp.country_issue = self.country_edit.text() or None
        stamp.notes = self.notes_edit.toPlainText()

        condition_by_label = {v: k for k, v in _CONDITION_LABELS.items()}
        stamp.condition = condition_by_label[self.condition_combo.currentText()]
        multiplicity_by_label = {v: k for k, v in _MULTIPLICITY_LABELS.items()}
        stamp.multiplicity = multiplicity_by_label[self.multiplicity_combo.currentText()]
        stamp.multiplicity_count = (
            self.multiplicity_count_spin.value()
            if stamp.multiplicity not in (None, models.MULTIPLICITY_SINGLE)
            else None
        )

        project_mod.save_stamp_results(library_root, self._project_id, stamp)
        self.status_label.setText("Saved.")
        self.changed.emit()
