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

from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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

from stampan.cli import DEFAULT_CGATS_PATH
from stampan.core import colour as colour_mod
from stampan.core import detection as detection_mod
from stampan.core import models
from stampan.core import perforation as perforation_mod
from stampan.core import philatelic_colours as philatelic_colours_mod
from stampan.core import project as project_mod
from stampan.core import scanner as scanner_mod
from stampan.core.calibration import reference as ref_mod
from stampan.core.io import ScanMetadata, read_tiff, write_tiff
from stampan.gui._util import find_sample_for_stamp
from stampan.gui.viewer import MODE_INCLUDE, TOOL_PAN, TOOL_PERF_BOX, TOOL_RECT
from stampan.gui.workers import Worker

_PHILATELIC_TABLE = None

#: How long to wait after the last ROI edit before recomputing its colour
#: (SPEC request: live update, not a "Run" button -- but recomputing on
#: every single brush-drag sample would be wasteful and janky).
ROI_DEBOUNCE_MS = 400

#: Re-exported for readability at call sites below -- the canonical
#: definition lives in core.perforation so the CLI's bulk re-measure can
#: share it.
DEFAULT_GAUGE_RANGE = perforation_mod.DEFAULT_GAUGE_RANGE


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


def _hint_label(text: str) -> QLabel:
    """A small instructional label that wraps instead of forcing its
    container wider -- an un-wrapped multi-sentence label sizes itself to
    fit the text on one line, which was dragging the whole side dock wide
    enough to starve the viewer of space."""
    label = QLabel(text)
    label.setWordWrap(True)
    return label


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
        self.status_label.setWordWrap(True)

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


class ColourOverviewPanel(_BusyPanel):
    """Cross-stamp colour comparison (SPEC §9/§10): every ink cluster
    measured anywhere in the project, plotted together in CIELAB a*b* and
    CIELCh -- the same data (and the same include/exclude + custom label
    choices, stored on each ``ColourCluster``) the PDF report's colour
    overview uses, so the interactive view and the report stay in sync.
    Shown in the right panel when a project is selected in the tree."""

    def __init__(self, library_root_getter, parent=None) -> None:
        super().__init__(parent)
        self._library_root_getter = library_root_getter
        self._project_id: str | None = None
        self._stamps: dict[str, object] = {}  # stamp_id -> loaded Stamp, kept in sync
        self._entries: list[tuple[str, object]] = []  # (stamp_id, cluster), == table rows

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._save_and_replot)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Colour overview</b>"))
        layout.addWidget(
            _hint_label(
                "<i>Every ink colour measured across this project's stamps. "
                "Uncheck a row to exclude it from the plot and the PDF "
                "report, or edit its label.</i>"
            )
        )

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda: self.set_project(self._project_id))
        layout.addWidget(self.refresh_button)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Include", "Label", "ISCC-NBS-like name", "L* C* h"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.table, stretch=1)

        self.plot_label = QLabel()
        scroll = QScrollArea()
        scroll.setWidget(self.plot_label)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, stretch=1)
        layout.addWidget(self.status_label)

    def set_project(self, project_id: str | None) -> None:
        self._project_id = project_id
        self._stamps = {}
        self._entries = []
        if project_id is None:
            self.table.setRowCount(0)
            self.plot_label.clear()
            return
        library_root = self._library_root_getter()
        project = project_mod.load_project(library_root, project_id)
        for sample_id in project.sample_ids:
            sample = project_mod.load_sample(library_root, project_id, sample_id)
            for stamp_id in sample.stamp_ids:
                stamp = project_mod.load_stamp(library_root, project_id, stamp_id)
                self._stamps[stamp_id] = stamp
                for cluster in stamp.colour.clusters:
                    if cluster.label != "paper" and cluster.mean_lab is not None:
                        self._entries.append((stamp_id, cluster))
        self._populate_table()
        self._replot()
        self.status_label.setText(f"{len(self._entries)} ink colour(s) across this project.")

    def _default_label(self, stamp_id: str, cluster) -> str:
        return f"{stamp_id}/{cluster.label}"

    def _populate_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._entries))
        for row, (stamp_id, cluster) in enumerate(self._entries):
            include_item = QTableWidgetItem()
            include_item.setFlags(
                (include_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsEditable
            )
            include_item.setCheckState(
                Qt.CheckState.Checked if cluster.overview_included else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, include_item)

            label_item = QTableWidgetItem(
                cluster.overview_label or self._default_label(stamp_id, cluster)
            )
            if cluster.srgb_swatch:
                label_item.setBackground(QColor(*cluster.srgb_swatch))
            self.table.setItem(row, 1, label_item)

            # "philatelic_name" *is* the ISCC-NBS-like name (a modifier +
            # hue name composed the same way ISCC-NBS itself is, SPEC §9 --
            # not the old, removed Centore lookup table) -- just not
            # labelled that anywhere the user actually sees it until now.
            iscc_item = QTableWidgetItem(cluster.philatelic_name or "")
            iscc_item.setFlags(iscc_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 2, iscc_item)

            lch = (
                f"{cluster.mean_lch[0]:.1f} {cluster.mean_lch[1]:.1f} {cluster.mean_lch[2]:.1f}"
                if cluster.mean_lch
                else ""
            )
            lch_item = QTableWidgetItem(lch)
            lch_item.setFlags(lch_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, lch_item)
        self.table.blockSignals(False)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if row >= len(self._entries):
            return
        stamp_id, cluster = self._entries[row]
        include_item = self.table.item(row, 0)
        label_item = self.table.item(row, 1)
        cluster.overview_included = include_item.checkState() == Qt.CheckState.Checked
        text = label_item.text().strip()
        default = self._default_label(stamp_id, cluster)
        cluster.overview_label = None if text in ("", default) else text
        self._debounce.start(ROI_DEBOUNCE_MS)

    def _save_and_replot(self) -> None:
        library_root = self._library_root_getter()
        for stamp in self._stamps.values():
            project_mod.save_stamp_results(library_root, self._project_id, stamp)
        self._replot()
        self.status_label.setText("Saved.")

    def _replot(self) -> None:
        entries = [
            (cluster.overview_label or self._default_label(stamp_id, cluster), cluster)
            for stamp_id, cluster in self._entries
            if cluster.overview_included
        ]
        png = colour_mod.render_colour_overview(entries)
        self.plot_label.setPixmap(_png_to_pixmap(png, max_width=900))


class ReferenceManagerPanel(_BusyPanel):
    """Every reference in the library (SPEC §4.4/§8 Phase 8): rename,
    archive/unarchive (soft-hide, not delete), inspect a reference's own
    calibration diagnostics, and compare two side by side. Shown whenever
    a reference is selected in the tree."""

    def __init__(self, library_root_getter, parent=None) -> None:
        super().__init__(parent)
        self._library_root_getter = library_root_getter
        self._refs: list = []  # Reference objects, index-aligned with table rows

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>References</b>"))

        self.show_archived_check = QCheckBox("Show archived")
        self.show_archived_check.stateChanged.connect(lambda _state: self.refresh())
        layout.addWidget(self.show_archived_check)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Name", "Created", "Scale (px/mm x/y)", "Colour model", "CV ΔE00 mean"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table, stretch=1)

        button_row = QHBoxLayout()
        self.rename_button = QPushButton("Rename...")
        self.rename_button.clicked.connect(self._rename_selected)
        self.archive_button = QPushButton("Archive/Unarchive")
        self.archive_button.clicked.connect(self._toggle_archive_selected)
        self.compare_button = QPushButton("Compare selected (2)")
        self.compare_button.clicked.connect(self._compare_selected)
        button_row.addWidget(self.rename_button)
        button_row.addWidget(self.archive_button)
        button_row.addWidget(self.compare_button)
        layout.addLayout(button_row)
        layout.addWidget(self.status_label)

        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextFormat(Qt.TextFormat.RichText)
        scroll = QScrollArea()
        scroll.setWidget(self.detail_label)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, stretch=1)

    def refresh(self, select_ref_id: str | None = None) -> None:
        library_root = self._library_root_getter()
        all_refs = ref_mod.list_references(library_root)
        show_archived = self.show_archived_check.isChecked()
        self._refs = [r for r in all_refs if show_archived or not r.archived]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._refs))
        for row, r in enumerate(self._refs):
            scale_txt = f"{r.scale.px_per_mm_x:.2f} / {r.scale.px_per_mm_y:.2f}" if r.scale else "—"
            model_txt = r.colour_model.kind if r.colour_model else "—"
            cv_mean = r.colour_model.cv_delta_e00.get("mean") if r.colour_model else None
            cv_txt = f"{cv_mean:.2f}" if cv_mean is not None else "—"
            name = r.name + (" (archived)" if r.archived else "")
            for col, value in enumerate([name, r.created, scale_txt, model_txt, cv_txt]):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.blockSignals(False)
        if select_ref_id:
            for row, r in enumerate(self._refs):
                if r.ref_id == select_ref_id:
                    self.table.selectRow(row)
                    break
        else:
            self._on_selection_changed()

    def _selected_refs(self) -> list:
        rows = sorted({idx.row() for idx in self.table.selectionModel().selectedRows()})
        return [self._refs[r] for r in rows]

    def _on_selection_changed(self) -> None:
        selected = self._selected_refs()
        if len(selected) == 1:
            self._show_detail(selected[0])
        elif len(selected) == 2:
            self.detail_label.setText("<i>Two references selected -- click Compare.</i>")
        else:
            self.detail_label.setText("")

    def _show_detail(self, ref) -> None:
        lines = [f"<b>{ref.name}</b> ({ref.ref_id})", f"Created: {ref.created}"]
        if ref.scale:
            iso = " (assumed isotropic)" if ref.scale.assumed_isotropic else ""
            lines.append(
                f"Scale: {ref.scale.px_per_mm_x:.3f} / {ref.scale.px_per_mm_y:.3f} px/mm{iso}"
            )
        else:
            lines.append("Scale: not calibrated (colour-only reference).")
        if ref.colour_model:
            cv = ref.colour_model.cv_delta_e00
            lines.append(
                f"Colour model: {ref.colour_model.kind} -- cross-validated ΔE00 "
                f"mean {cv.get('mean', float('nan')):.2f}, "
                f"p95 {cv.get('p95', float('nan')):.2f}, "
                f"max {cv.get('max', float('nan')):.2f}"
            )
        if ref.notes:
            lines.append(f"Notes: {ref.notes}")
        self.detail_label.setText("<br>".join(lines))

    def _rename_selected(self) -> None:
        selected = self._selected_refs()
        if len(selected) != 1:
            QMessageBox.information(self, "Rename", "Select exactly one reference.")
            return
        ref = selected[0]
        name, ok = QInputDialog.getText(self, "Rename reference", "Name:", text=ref.name)
        if not ok or not name.strip():
            return
        library_root = self._library_root_getter()
        ref.name = name.strip()
        ref_mod.save_reference_metadata(library_root, ref)
        self.status_label.setText("Renamed.")
        self.refresh(select_ref_id=ref.ref_id)
        self.changed.emit()

    def _toggle_archive_selected(self) -> None:
        selected = self._selected_refs()
        if not selected:
            QMessageBox.information(self, "Archive", "Select at least one reference.")
            return
        library_root = self._library_root_getter()
        for ref in selected:
            ref.archived = not ref.archived
            ref_mod.save_reference_metadata(library_root, ref)
        self.status_label.setText("Updated.")
        self.refresh()
        self.changed.emit()

    def _compare_selected(self) -> None:
        selected = self._selected_refs()
        if len(selected) != 2:
            QMessageBox.information(self, "Compare", "Select exactly two references.")
            return
        a, b = selected
        lines = [f"<b>{a.name}</b> vs <b>{b.name}</b>"]
        if a.scale and b.scale:
            dx = a.scale.px_per_mm_x - b.scale.px_per_mm_x
            dy = a.scale.px_per_mm_y - b.scale.px_per_mm_y
            lines.append(f"Scale difference: x {dx:+.3f} px/mm, y {dy:+.3f} px/mm")
        else:
            lines.append("Scale: not available for both -- can't compare.")
        if a.colour_model and b.colour_model:
            cv_a = a.colour_model.cv_delta_e00.get("mean", float("nan"))
            cv_b = b.colour_model.cv_delta_e00.get("mean", float("nan"))
            lines.append(
                f"{a.name}: {a.colour_model.kind}, own cross-validated ΔE00 mean {cv_a:.2f}"
            )
            lines.append(
                f"{b.name}: {b.colour_model.kind}, own cross-validated ΔE00 mean {cv_b:.2f}"
            )
            lines.append(
                "<i>Each reference's ΔE00 is its own cross-validation against its own IT8 "
                "scan, not a same-patch-set comparison between the two -- a reference's raw IT8 "
                "patch samples (and the corner coordinates needed to re-sample them from the "
                "other's scan) aren't persisted separately from its fitted model, so a literal "
                "apples-to-apples prediction comparison isn't available here. Treat this as "
                "'how well does each describe its own scan', not a direct head-to-head.</i>"
            )
        else:
            lines.append("Colour model: not available for both -- can't compare.")
        self.detail_label.setText("<br>".join(lines))


class ReferenceWizardPanel(_BusyPanel):
    """Build a new Reference from a scan (SPEC §4.4/§8 Phase 8).

    Either scan the IT8 target live (device selection, option dump,
    apply-and-verify per SPEC §4.1, then acquire) or load a
    previously-captured TIFF, then Build & Review directly. IT8 patch
    location is fully automatic via ArgyllCMS's `scanin` (SPEC §4.3,
    René's explicit direction) -- no corner clicking. The target can be
    anywhere on the bed at any rotation; `scanin` locates the chart's own
    printed fiducial marks and patch grid from the scan itself, the same
    way it does for any ICC profiling workflow. (The earlier click-4-6-
    points approach needed pixel-precise manual clicks whose errors
    compounded across the grid, and its labels were ambiguous enough to
    cause real misclicks -- both gone now.) Build & Review shows the
    fitted colour model's validation (ΔE00 per candidate model kind, the
    chosen one's own stats, and `scanin`'s own patch-box diagnostic image)
    before Save commits it to the library.

    Geometric (px/mm) calibration is derived from the scan's resolution
    (dpi), not a ruler. For a live-captured scan, that resolution is the
    scanner setting actually read back and verified via
    ``scanner.apply_and_verify()`` -- not merely requested -- so it is
    trustworthy as an exact value. For a loaded TIFF, the dpi field is
    pre-filled from embedded scan metadata when the TIFF was written by
    this app's own capture step, and otherwise must be typed in by hand
    (in which case it is only as trustworthy as its source and is
    labelled accordingly in the saved reference's warnings).
    """

    def __init__(self, library_root_getter, viewer, parent=None) -> None:
        super().__init__(parent)
        self._library_root_getter = library_root_getter
        self._viewer = viewer
        self._scan: np.ndarray | None = None
        self._scan_metadata = None
        self._dpi_source = "unspecified"
        self._build_result: ref_mod.ReferenceBuildResult | None = None
        self._review_diagnostic_png: bytes | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>New reference from scan</b>"))
        layout.addWidget(
            _hint_label(
                "<i>Scan the IT8 target live, or load an existing scan TIFF, "
                "then Build & review -- patch location is fully automatic "
                "(ArgyllCMS scanin), no corner clicking. The target can be "
                "placed anywhere on the bed, at any rotation.</i>"
            )
        )

        scan_row = QHBoxLayout()
        self.device_combo = QComboBox()
        # A long item (e.g. a "(scanimage not available: ...)" error entry)
        # would otherwise size the combo -- and so the whole side panel --
        # to fit it; cap how much it can grow and let the rest elide.
        self.device_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.device_combo.setMinimumContentsLength(20)
        self.refresh_devices_button = QPushButton("Refresh devices")
        self.refresh_devices_button.clicked.connect(self._refresh_devices)
        scan_row.addWidget(self.device_combo, stretch=1)
        scan_row.addWidget(self.refresh_devices_button)
        layout.addLayout(scan_row)

        scan_form = QFormLayout()
        self.scan_dpi_spin = QDoubleSpinBox()
        self.scan_dpi_spin.setRange(50.0, 4800.0)
        self.scan_dpi_spin.setValue(1200.0)
        self.scan_dpi_spin.setSuffix(" dpi")
        scan_form.addRow("Scan resolution", self.scan_dpi_spin)
        layout.addLayout(scan_form)

        capture_row = QHBoxLayout()
        self.scan_button = QPushButton("Scan IT8 target...")
        self.scan_button.clicked.connect(self._scan_now)
        self.load_button = QPushButton("Load scan...")
        self.load_button.clicked.connect(self._load_scan)
        capture_row.addWidget(self.scan_button)
        capture_row.addWidget(self.load_button)
        layout.addLayout(capture_row)

        self._refresh_devices()

        options_form = QFormLayout()
        self.dpi_x_spin = QDoubleSpinBox()
        self.dpi_x_spin.setRange(50.0, 4800.0)
        self.dpi_x_spin.setValue(1200.0)
        self.dpi_x_spin.setSuffix(" dpi")
        options_form.addRow("Reference x resolution", self.dpi_x_spin)
        self.dpi_y_check = QCheckBox("Y resolution differs from X")
        self.dpi_y_check.toggled.connect(self._on_dpi_y_toggled)
        options_form.addRow(self.dpi_y_check)
        self.dpi_y_spin = QDoubleSpinBox()
        self.dpi_y_spin.setRange(50.0, 4800.0)
        self.dpi_y_spin.setValue(1200.0)
        self.dpi_y_spin.setSuffix(" dpi")
        self.dpi_y_spin.setEnabled(False)
        options_form.addRow("Reference y resolution", self.dpi_y_spin)
        self.dpi_source_label = QLabel("Resolution source: unspecified")
        self.dpi_source_label.setWordWrap(True)
        options_form.addRow(self.dpi_source_label)
        layout.addLayout(options_form)

        name_form = QFormLayout()
        self.name_edit = QLineEdit()
        name_form.addRow("Name", self.name_edit)
        cgats_row = QHBoxLayout()
        self.cgats_edit = QLineEdit(str(DEFAULT_CGATS_PATH))
        cgats_row.addWidget(self.cgats_edit)
        cgats_browse = QPushButton("Browse...")
        cgats_browse.clicked.connect(self._browse_cgats)
        cgats_row.addWidget(cgats_browse)
        name_form.addRow("CGATS reference data", cgats_row)
        layout.addLayout(name_form)
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Notes (optional)")
        self.notes_edit.setMaximumHeight(60)
        layout.addWidget(self.notes_edit)

        self.build_button = QPushButton("Build && review")
        self.build_button.setEnabled(False)
        self.build_button.clicked.connect(self._build_and_review)
        layout.addWidget(self.build_button)
        layout.addWidget(self.status_label)

        self.review_label = QLabel()
        self.review_label.setWordWrap(True)
        self.review_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.review_label)
        self.review_image_label = QLabel()
        self.review_image_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        self.review_scroll = QScrollArea()
        self.review_scroll.setWidget(self.review_image_label)
        self.review_scroll.setWidgetResizable(True)
        self.review_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.review_scroll, stretch=1)

        save_row = QHBoxLayout()
        self.save_button = QPushButton("Save reference")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save)
        self.discard_button = QPushButton("Discard review")
        self.discard_button.setEnabled(False)
        self.discard_button.clicked.connect(self._discard_review)
        save_row.addWidget(self.save_button)
        save_row.addWidget(self.discard_button)
        layout.addLayout(save_row)

    # -- scan acquisition -------------------------------------------------

    def _refresh_devices(self) -> None:
        self.device_combo.clear()
        try:
            all_devices = scanner_mod.list_devices()
        except OSError as exc:
            self.device_combo.addItem(f"(scanimage not available: {exc})", None)
            self.scan_button.setEnabled(False)
            return
        # SANE's "v4l" backend wraps webcams as scanner-shaped devices (so
        # e.g. a laptop's integrated webcam shows up in `scanimage -L`
        # alongside the real flatbed) -- they take none of the
        # resolution/mode/colour options this app relies on, so excluding
        # them outright (not just deprioritising) avoids silently picking
        # one as the default device and failing deep inside apply_and_verify.
        devices = [d for d in all_devices if not d.device_name.startswith("v4l:")]
        if not devices:
            self.device_combo.addItem("(no scanner found)", None)
            self.scan_button.setEnabled(False)
            return
        for device in devices:
            self.device_combo.addItem(f"{device.description} [{device.device_name}]", device)
        self.scan_button.setEnabled(True)

    def _on_dpi_y_toggled(self, checked: bool) -> None:
        self.dpi_y_spin.setEnabled(checked)

    def _scan_now(self) -> None:
        device = self.device_combo.currentData()
        resolution = self.scan_dpi_spin.value()
        library_root = self._library_root_getter()
        scan_dir = Path(library_root) / "_scans"
        scan_dir.mkdir(parents=True, exist_ok=True)
        output_path = scan_dir / f"it8_scan_{int(resolution)}dpi.tif"
        device_name = device.device_name if device is not None else None

        def do_scan(progress=None):
            if progress:
                progress("checking scanner options...")
            # Per SPEC §2/§4.1: all automatic processing stays off (colour
            # correction, gamma massaging, sharpening, descreening) so the
            # captured RGB is as close to the sensor's raw response as this
            # backend allows -- the characterisation model, not the scanner
            # firmware, must account for any nonlinearity.
            #
            # "Default" gamma-correction is NOT linear/off -- it's the
            # backend's own contrast curve, a real colorimetric distortion
            # (confirmed: cross-validated ΔE00 mean ~2.4 with an identity
            # user-defined gamma table loaded, vs ~6-8 with "Default" on
            # otherwise-identical scans). An identity table (output[i] = i)
            # is the actual linear/off equivalent on this backend.
            # auto-area-segmentation is a lineart/halftone-mode-only option on
            # this backend (inactive in Color mode), so it is deliberately
            # left out here rather than causing apply_and_verify to fail.
            identity_gamma = ",".join(str(i) for i in range(256))
            no_auto_options = {
                "color-correction": "None",
                "gamma-correction": "User defined",
                "red-gamma-table": identity_gamma,
                "green-gamma-table": identity_gamma,
                "blue-gamma-table": identity_gamma,
                "sharpness": "0",
            }
            requested = {"resolution": str(int(resolution)), "mode": "Color", **no_auto_options}
            mismatches = scanner_mod.apply_and_verify(requested, device=device_name)
            if mismatches:
                raise scanner_mod.ScannerError(
                    "scanner would not apply requested geometry/colour options "
                    f"(SPEC §4.1 verification failed): {mismatches}"
                )
            if progress:
                progress("scanning...")
            tmp_tiff = scan_dir / f"_raw_{int(resolution)}dpi.tif"
            acquire = scanner_mod.acquire_scan(
                tmp_tiff,
                device=device_name,
                resolution_dpi=int(resolution),
                mode="Color",
                depth=16,
                extra_options=no_auto_options,
            )
            scan, _ = read_tiff(tmp_tiff)
            extra: dict = {}
            warnings = list(acquire.warnings)
            if scan.dtype == np.uint8:
                # This backend (e.g. the Epson 1200U via epson2) may only
                # offer 8-bit depth. write_tiff enforces the pipeline's
                # uint16 convention; a caller that just `.astype(uint16)`s
                # an 8-bit array gets values still numerically in 0-255,
                # silently outside the domain any reference's
                # characterisation model was trained on. Rescale losslessly
                # (x257, so 0->0, 255->65535) instead.
                warnings.append("scanner wrote 8-bit; upscaled to 16-bit (x257) for consistency")
                scan = (scan.astype(np.uint16) * 257).astype(np.uint16)
                extra["upscaled_from_8bit"] = True
            # resolution recorded here is the value just read back and
            # confirmed by apply_and_verify() above, not merely requested.
            metadata = ScanMetadata(
                scanner_options={"resolution": resolution, "mode": "Color", **requested},
                extra=extra,
            )
            write_tiff(output_path, scan, metadata=metadata)
            tmp_tiff.unlink(missing_ok=True)
            return scan, metadata, warnings

        self._run(do_scan, on_done=self._on_scan_done)

    def _on_scan_done(self, result) -> None:
        scan, metadata, warnings = result
        self._scan, self._scan_metadata = scan, metadata
        self._dpi_source = "verified scanner setting (just captured)"
        self.dpi_source_label.setText(f"Resolution source: {self._dpi_source}")
        resolution = metadata.scanner_options.get("resolution")
        if resolution is not None:
            self.dpi_x_spin.setValue(float(resolution))
            self.dpi_y_check.setChecked(False)
        self._build_result = None
        self._viewer.set_image(self._scan)
        self._viewer.set_tool(TOOL_PAN)
        self.save_button.setEnabled(False)
        self.discard_button.setEnabled(False)
        self.review_label.setText("")
        self._review_diagnostic_png = None
        self._rescale_review_image()
        self.build_button.setEnabled(True)
        self.status_label.setText(
            "Scanned, with warnings: " + "; ".join(warnings) if warnings else "Scanned."
        )

    def _load_scan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Reference scan TIFF", "", "TIFF (*.tif *.tiff)"
        )
        if not path:
            return
        self._scan, self._scan_metadata = read_tiff(path)
        self._build_result = None
        self._viewer.set_image(self._scan)
        self._viewer.set_tool(TOOL_PAN)
        self.save_button.setEnabled(False)
        self.discard_button.setEnabled(False)
        self.review_label.setText("")
        self._review_diagnostic_png = None
        self._rescale_review_image()
        resolution = (
            self._scan_metadata.scanner_options.get("resolution") if self._scan_metadata else None
        )
        if resolution is not None:
            self.dpi_x_spin.setValue(float(resolution))
            self.dpi_y_check.setChecked(False)
            self._dpi_source = "verified scanner setting (embedded scan metadata)"
        else:
            self._dpi_source = "typed by hand (not embedded in this TIFF)"
        self.dpi_source_label.setText(f"Resolution source: {self._dpi_source}")
        self.build_button.setEnabled(True)
        self.status_label.setText("Loaded -- Build & review when ready.")

    # -- build + review ----------------------------------------------------

    def _browse_cgats(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "CGATS reference data", "", "Text (*.txt)")
        if path:
            self.cgats_edit.setText(path)

    def _build_and_review(self) -> None:
        if self._scan is None:
            return
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "Build reference", "Enter a name first.")
            return
        cgats_path = self.cgats_edit.text().strip()
        if not cgats_path or not Path(cgats_path).exists():
            QMessageBox.warning(self, "Build reference", f"CGATS file not found: {cgats_path}")
            return

        dpi_x = self.dpi_x_spin.value()
        dpi_y = self.dpi_y_spin.value() if self.dpi_y_check.isChecked() else None
        dpi_source = self._dpi_source
        name_final = name
        notes = self.notes_edit.toPlainText()
        scan, scan_metadata = self._scan, self._scan_metadata
        cgats = Path(cgats_path)

        def do_build(progress=None):
            if progress:
                progress("fitting calibration...")
            return ref_mod.build_reference(
                name=name_final,
                scan=scan,
                scan_metadata=scan_metadata,
                cgats_reference_path=cgats,
                dpi_x=dpi_x,
                dpi_y=dpi_y,
                dpi_source=dpi_source,
                notes=notes,
            )

        self._run(do_build, on_done=self._on_build_done)

    def resizeEvent(self, event) -> None:  # noqa: N802 -- Qt override
        super().resizeEvent(event)
        self._rescale_review_image()

    def _rescale_review_image(self) -> None:
        """Scale the review diagnostic to the scroll area's current
        viewport width (panel-wide, not a fixed small thumbnail) and let
        the scroll area handle vertical scrolling for the rest -- it's a
        264+24-patch grid image, too tall to fit any reasonable panel
        height at a size where individual patch boxes are legible."""
        if self._review_diagnostic_png is None:
            self.review_image_label.clear()
            return
        width = self.review_scroll.viewport().width()
        if width <= 0:
            width = 600
        arr = cv2.imdecode(
            np.frombuffer(self._review_diagnostic_png, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
        h, w = arr.shape[:2]
        image = QImage(np.ascontiguousarray(arr).data, w, h, 3 * w, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(image.copy())
        # Scale to fill the panel's width exactly (not just shrink-to-fit),
        # so the diagnostic is actually legible instead of a small, fixed
        # thumbnail regardless of how much space is available.
        pixmap = pixmap.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        self.review_image_label.setPixmap(pixmap)

    def _on_build_done(self, result: ref_mod.ReferenceBuildResult) -> None:
        self._build_result = result
        ref = result.reference
        lines = [f"<b>{ref.name}</b>"]
        if ref.scale:
            lines.append(
                f"Scale: {ref.scale.px_per_mm_x:.3f} / {ref.scale.px_per_mm_y:.3f} px/mm "
                f"(±{ref.scale.uncertainty_x:.4f} / ±{ref.scale.uncertainty_y:.4f})"
            )
        if ref.colour_model:
            cv = ref.colour_model.cv_delta_e00
            lines.append(
                f"Chosen colour model: <b>{ref.colour_model.kind}</b> -- cross-validated "
                f"ΔE00 mean {cv.get('mean', float('nan')):.2f}, "
                f"p95 {cv.get('p95', float('nan')):.2f}, max {cv.get('max', float('nan')):.2f}"
            )
        if result.model_comparison:
            lines.append("<br>All candidate models' cross-validated ΔE00 (mean / p95 / max):")
            for kind, stats in result.model_comparison.items():
                marker = " ← chosen" if ref.colour_model and kind == ref.colour_model.kind else ""
                lines.append(
                    f"&nbsp;&nbsp;{kind}: {stats.get('mean', float('nan')):.2f} / "
                    f"{stats.get('p95', float('nan')):.2f} / "
                    f"{stats.get('max', float('nan')):.2f}{marker}"
                )
        if ref.notes:
            lines.append(f"<br><i>{ref.notes}</i>")
        self.review_label.setText("<br>".join(lines))

        preview = result.diagnostics.get("it8_grid.png") or result.diagnostics.get(
            "colour_model_error_heatmap.png"
        )
        self._review_diagnostic_png = preview
        self._rescale_review_image()
        self.save_button.setEnabled(True)
        self.discard_button.setEnabled(True)
        self.status_label.setText("Reviewed -- Save to keep it, or Discard to adjust and retry.")

    def _discard_review(self) -> None:
        self._build_result = None
        self.review_label.setText("")
        self._review_diagnostic_png = None
        self._rescale_review_image()
        self.save_button.setEnabled(False)
        self.discard_button.setEnabled(False)
        self.status_label.setText("Discarded. Adjust points/options and Build & review again.")

    def _save(self) -> None:
        if self._build_result is None:
            return
        library_root = self._library_root_getter()
        ref_dir = ref_mod.save_reference(self._build_result, library_root)
        self.status_label.setText(f"Saved {self._build_result.reference.ref_id} to {ref_dir}.")
        self._scan = None
        self._scan_metadata = None
        saved_result = self._build_result
        self._build_result = None
        self.save_button.setEnabled(False)
        self.discard_button.setEnabled(False)
        self.build_button.setEnabled(False)
        self._viewer.clear_points()
        self._viewer.set_tool(TOOL_PAN)
        self.status_label.setText("Load a scan to begin.")
        self.changed.emit()
        QMessageBox.information(self, "Reference saved", f"Saved '{saved_result.reference.name}'.")


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

        layout.addWidget(
            _hint_label(
                "<i>Select the sheet in the tree above to list its stamps, "
                "add one detection missed, or correct a bounding box.</i>"
            )
        )

        self.rerun_perforation_button = QPushButton("Re-measure perforation (all stamps)...")
        self.rerun_perforation_button.clicked.connect(self._rerun_perforation_all)
        layout.addWidget(self.rerun_perforation_button)

        self.scanner_button = QPushButton("Scan...")
        self.scanner_button.setToolTip(
            "Scan a sample sheet with the selected reference's exact scanner options "
            "(SPEC §4.1), then detect stamps on it automatically."
        )
        self.scanner_button.clicked.connect(self._scan_sheet)
        layout.addWidget(self.scanner_button)

        layout.addWidget(self.status_label)
        layout.addStretch(1)

    def set_project(self, project_id: str | None) -> None:
        self._current_project_id = project_id

    def _rerun_perforation_all(self) -> None:
        library_root = self._library_root_getter()
        if self._current_project_id is None:
            QMessageBox.information(self, "Re-measure", "Select a project first.")
            return
        project_id = self._current_project_id
        answer = QMessageBox.question(
            self,
            "Re-measure perforation",
            "Re-run perforation on every stamp in this project?\n\n"
            "Each stamp keeps its own saved box and gauge range if it has "
            "one (a stamp never measured before uses the current defaults: "
            f"{perforation_mod.DEFAULT_GAUGE_RANGE[0]:.0f}-"
            f"{perforation_mod.DEFAULT_GAUGE_RANGE[1]:.0f}). Any per-stamp "
            'manual correction (the "manual" override, not the auto '
            "result) is left untouched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def do_rerun(progress=None):
            project = project_mod.load_project(library_root, project_id)
            scale_cache: dict[str, tuple[float, float]] = {}
            stamp_to_sample: dict[str, str] = {}
            for sample_id in project.sample_ids:
                sample = project_mod.load_sample(library_root, project_id, sample_id)
                for stamp_id in sample.stamp_ids:
                    stamp_to_sample[stamp_id] = sample_id
                if sample.ref_id not in scale_cache:
                    ref = ref_mod.load_reference(library_root, sample.ref_id)
                    if ref.scale is not None:
                        scale_cache[sample.ref_id] = (ref.scale.px_per_mm_x, ref.scale.px_per_mm_y)

            n_ok, n_total = 0, len(stamp_to_sample)
            for i, (stamp_id, sample_id) in enumerate(stamp_to_sample.items(), start=1):
                if progress:
                    progress(f"perforation {i}/{n_total}: {stamp_id}")
                sample = project_mod.load_sample(library_root, project_id, sample_id)
                scale = scale_cache.get(sample.ref_id)
                if scale is None:
                    continue
                px_per_mm_x, px_per_mm_y = scale
                crop = project_mod.load_stamp_crop(library_root, project_id, stamp_id)
                stamp = project_mod.load_stamp(library_root, project_id, stamp_id)
                gauge_range = stamp.perforation.gauge_range or perforation_mod.DEFAULT_GAUGE_RANGE
                analysis = perforation_mod.analyse_perforation(
                    crop,
                    px_per_mm_x,
                    px_per_mm_y,
                    manual_roi=stamp.perforation.roi_override_mm or None,
                    manual_box_px=stamp.perforation.box_override_px,
                    condition=stamp.condition,
                    multiplicity=stamp.multiplicity,
                    multiplicity_count=stamp.multiplicity_count,
                    gauge_range=gauge_range,
                )
                diagnostic_png = perforation_mod.render_diagnostic(crop, analysis)
                side_pngs = {
                    f"perforation_{name}.png": perforation_mod.render_side_profile(side)
                    for name, side in analysis.sides.items()
                }
                stamp.perforation.auto = {
                    name: side.to_persisted() for name, side in analysis.sides.items()
                }
                stamp.perforation.gauge_range = gauge_range
                stamp.perforation.meta.confidence = analysis.confidence
                stamp.perforation.meta.warnings = analysis.warnings
                stamp.perforation.meta.diagnostic_image = "diagnostics/perforation.png"
                project_mod.save_stamp_results(
                    library_root,
                    project_id,
                    stamp,
                    diagnostics={"perforation.png": diagnostic_png, **side_pngs},
                )
                n_ok += 1
            return n_ok, n_total

        self._run(
            do_rerun,
            on_done=lambda result: self.status_label.setText(
                f"Re-measured perforation on {result[0]}/{result[1]} stamp(s)."
            ),
        )

    def _pick_reference(self, title: str) -> str | None:
        library_root = self._library_root_getter()
        refs = ref_mod.list_references(library_root)
        if not refs:
            QMessageBox.warning(self, title, "No references available.")
            return None
        if len(refs) == 1:
            return refs[0].ref_id
        labels = [f"{r.name} ({r.ref_id})" for r in refs]
        choice, ok = QInputDialog.getItem(
            self, "Reference", "Reference to use for this sheet:", labels, editable=False
        )
        if not ok:
            return None
        return refs[labels.index(choice)].ref_id

    def _detect_and_save_sample(
        self, library_root, project_id: str, ref_id: str, scan_image, scan_metadata, progress=None
    ) -> int:
        """Shared by both the file-import and live-scan paths: register the
        sample, detect stamps, extract crops, save everything."""
        from stampan.core.models import GeometryResult, Stamp

        ref = ref_mod.load_reference(library_root, ref_id)
        if ref.scale is None:
            raise RuntimeError(f"reference {ref_id} has no scale calibration")
        scan_metadata.ref_id = ref_id
        project = project_mod.load_project(library_root, project_id)
        sample = project_mod.add_sample(library_root, project, scan_image, scan_metadata, ref_id)

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
        (project_dir / "diagnostics" / f"{sample.sample_id}_overview.png").write_bytes(overview_png)
        project_mod.save_sample(library_root, sample)
        return len(result.stamps)

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
        ref_id = self._pick_reference("Import")
        if ref_id is None:
            return
        project_id = self._current_project_id

        def do_detect(progress=None):
            from stampan.core.io import read_tiff

            scan_image, scan_metadata = read_tiff(sample_path)
            return self._detect_and_save_sample(
                library_root, project_id, ref_id, scan_image, scan_metadata, progress
            )

        self._run(do_detect, on_done=lambda n: self.status_label.setText(f"Detected {n} stamp(s)."))

    def _scan_sheet(self) -> None:
        library_root = self._library_root_getter()
        if self._current_project_id is None:
            QMessageBox.information(self, "Scan", "Select or create a project first.")
            return
        ref_id = self._pick_reference("Scan")
        if ref_id is None:
            return

        try:
            all_devices = scanner_mod.list_devices()
        except OSError as exc:
            QMessageBox.warning(self, "Scan", f"scanimage not available: {exc}")
            return
        devices = [d for d in all_devices if not d.device_name.startswith("v4l:")]
        if not devices:
            QMessageBox.warning(self, "Scan", "No scanner found.")
            return
        device_name: str | None
        if len(devices) == 1:
            device_name = devices[0].device_name
        else:
            labels = [f"{d.description} [{d.device_name}]" for d in devices]
            choice, ok = QInputDialog.getItem(
                self, "Scanner", "Device to scan with:", labels, editable=False
            )
            if not ok:
                return
            device_name = devices[labels.index(choice)].device_name

        project_id = self._current_project_id

        def do_scan(progress=None):
            from stampan.core.io import ScanMetadata, read_tiff, write_tiff

            # SPEC §4.1: a sample sheet must be scanned with *exactly* the
            # reference's own geometry/colour-relevant options -- apply them
            # and read them back, refusing (not just warning) on any
            # mismatch, the same apply-and-verify discipline the reference
            # wizard's own live-scan path uses.
            ref = ref_mod.load_reference(library_root, ref_id)
            requested = {
                k: v for k, v in ref.scanner_options.items() if k not in ("resolution", "mode")
            }
            resolution = ref.scanner_options.get("resolution")
            if resolution is None:
                raise scanner_mod.ScannerError(
                    f"reference {ref_id} has no recorded resolution -- it predates live "
                    "scanning or was built from an externally-captured scan; rebuild it "
                    "via a live scan first."
                )
            requested["resolution"] = str(int(float(resolution)))
            requested.setdefault("mode", "Color")

            if progress:
                progress("checking scanner options match the reference...")
            mismatches = scanner_mod.apply_and_verify(requested, device=device_name)
            if mismatches:
                raise scanner_mod.ScannerError(
                    "scanner options don't match reference "
                    f"{ref_id} (SPEC §4.1 verification failed): {mismatches}"
                )

            if progress:
                progress("scanning...")
            library_root_path = Path(library_root)
            scan_dir = library_root_path / "_scans"
            scan_dir.mkdir(parents=True, exist_ok=True)
            tmp_tiff = scan_dir / "_raw_sample_scan.tif"
            acquire = scanner_mod.acquire_scan(
                tmp_tiff,
                device=device_name,
                resolution_dpi=int(float(resolution)),
                mode=requested["mode"],
                depth=16,
                extra_options={
                    k: v for k, v in requested.items() if k not in ("resolution", "mode")
                },
            )
            scan_image, _ = read_tiff(tmp_tiff)
            extra: dict = {}
            warnings = list(acquire.warnings)
            if scan_image.dtype == np.uint8:
                # Same reasoning as the reference wizard's own scan path:
                # write_tiff enforces uint16, and a naive .astype would
                # leave values numerically in 0-255, outside the domain the
                # reference's colour model was trained on.
                warnings.append("scanner wrote 8-bit; upscaled to 16-bit (x257) for consistency")
                scan_image = (scan_image.astype(np.uint16) * 257).astype(np.uint16)
                extra["upscaled_from_8bit"] = True
            scan_metadata = ScanMetadata(scanner_options=dict(requested), extra=extra)
            write_tiff(tmp_tiff, scan_image, metadata=scan_metadata)

            n = self._detect_and_save_sample(
                library_root, project_id, ref_id, scan_image, scan_metadata, progress
            )
            tmp_tiff.unlink(missing_ok=True)
            return n, warnings

        def on_done(result):
            n, warnings = result
            msg = f"Scanned and detected {n} stamp(s)."
            if warnings:
                msg += " Warnings: " + "; ".join(warnings)
            self.status_label.setText(msg)

        self._run(do_scan, on_done=on_done)


class SheetStampsPanel(_BusyPanel):
    """The right-panel view for a selected sample sheet (SPEC §5/§12):
    lists every stamp detected on it, lets the user pick one to correct its
    bounding box by dragging the same rectangle Perforation uses (corner
    handles resize, the cyan handle rotates), and adds a stamp a detection
    pass missed by drawing a new one from scratch. Shown in place of the
    Stamp/Perforation/Colour tabs, which only make sense once a single
    stamp is selected -- main_window swaps the two in and out of a
    QStackedWidget based on what's selected in the library tree."""

    def __init__(self, library_root_getter, viewer, parent=None) -> None:
        super().__init__(parent)
        self._library_root_getter = library_root_getter
        self._viewer = viewer
        self._project_id: str | None = None
        self._sample_id: str | None = None
        self._sample = None
        self._px_per_mm_x: float | None = None
        self._px_per_mm_y: float | None = None
        self._editing_stamp_id: str | None = None
        self._adding = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Stamps on this sheet</b>"))

        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._on_row_selected)
        layout.addWidget(self.list, stretch=1)

        layout.addWidget(
            _hint_label(
                "<i>Select a stamp to edit its bounding box, or Add stamp "
                "to mark one detection missed.</i>"
            )
        )

        button_row = QHBoxLayout()
        self.add_button = QPushButton("Add stamp")
        self.add_button.clicked.connect(self._start_add)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply_edit)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(lambda: self._cancel_edit())
        button_row.addWidget(self.add_button)
        button_row.addWidget(self.apply_button)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)
        layout.addWidget(self.status_label)

    # -- sheet loading -------------------------------------------------------

    def set_sample(self, project_id: str, sample_id: str) -> None:
        self._cancel_edit()
        self._project_id, self._sample_id = project_id, sample_id
        library_root = self._library_root_getter()
        self._sample = project_mod.load_sample(library_root, project_id, sample_id)
        self._px_per_mm_x = self._px_per_mm_y = None
        try:
            ref = ref_mod.load_reference(library_root, self._sample.ref_id)
            if ref.scale is not None:
                self._px_per_mm_x = ref.scale.px_per_mm_x
                self._px_per_mm_y = ref.scale.px_per_mm_y
        except Exception:
            pass
        self._populate_list()
        self._show_all_boxes()

    def deactivate(self) -> None:
        """A different tree item was selected: drop any in-progress
        edit/add rather than leave it dangling under what's shown next."""
        self._cancel_edit()

    def _show_all_boxes(self) -> None:
        """Every detected stamp's box, all at once, non-interactive (SPEC
        §5) -- the overview shown while nothing is selected, so a stamp
        the detection pass missed is immediately visible as a gap."""
        if self._sample is None or self._px_per_mm_x is None:
            self._viewer.clear_static_boxes()
            return
        library_root = self._library_root_getter()
        boxes = []
        for stamp_id in self._sample.stamp_ids:
            g = project_mod.load_stamp(library_root, self._project_id, stamp_id).geometry
            if g.sample_centre_px is None or g.width_mm is None or g.height_mm is None:
                continue
            box = cv2.boxPoints(
                (
                    g.sample_centre_px,
                    (g.width_mm * self._px_per_mm_x, g.height_mm * self._px_per_mm_y),
                    g.rotation_deg or 0.0,
                )
            )
            boxes.append((stamp_id, box))
        self._viewer.set_static_boxes(boxes)
        self._viewer.fit_whole_image()

    def _populate_list(self) -> None:
        self.list.clear()
        if self._sample is None:
            return
        library_root = self._library_root_getter()
        for stamp_id in self._sample.stamp_ids:
            stamp = project_mod.load_stamp(library_root, self._project_id, stamp_id)
            label = stamp_id
            if stamp.catalogue_numbers:
                label += f"  ({', '.join(stamp.catalogue_numbers)})"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, stamp_id)
            self.list.addItem(item)

    # -- selecting a stamp to edit its box -----------------------------

    def _on_row_selected(self) -> None:
        items = self.list.selectedItems()
        if not items:
            return
        self._begin_edit(items[0].data(Qt.ItemDataRole.UserRole))

    def select_and_edit(self, stamp_id: str) -> None:
        """External entry point for the Stamp tab's "Edit bounding box on
        sheet...": select this stamp here and start editing its box."""
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == stamp_id:
                self.list.setCurrentItem(item)  # triggers _on_row_selected
                return

    def _begin_edit(self, stamp_id: str) -> None:
        if self._adding:
            self._cancel_add()
        if self._px_per_mm_x is None:
            self.status_label.setText("No scale calibration for this sheet.")
            return
        library_root = self._library_root_getter()
        stamp = project_mod.load_stamp(library_root, self._project_id, stamp_id)
        g = stamp.geometry
        if g.sample_centre_px is None or g.width_mm is None or g.height_mm is None:
            self.status_label.setText(f"{stamp_id} has no detected geometry to edit.")
            return
        box = cv2.boxPoints(
            (
                g.sample_centre_px,
                (g.width_mm * self._px_per_mm_x, g.height_mm * self._px_per_mm_y),
                g.rotation_deg or 0.0,
            )
        )
        self._viewer.clear_static_boxes()
        self._viewer.set_perf_box(box)
        self._viewer.zoom_to_perf_box()
        self._viewer.set_tool(TOOL_PERF_BOX)
        self._editing_stamp_id = stamp_id
        self.apply_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.status_label.setText(f"Editing {stamp_id}'s bounding box -- drag to fit, then Apply.")

    def _apply_edit(self) -> None:
        if self._adding:
            self._confirm_add()
            return
        if self._editing_stamp_id is None:
            return
        rect = self._viewer.get_perf_box_rect()
        if rect is None:
            return
        centre_px, size_px, angle_deg = rect
        library_root = self._library_root_getter()
        project_id, sample, stamp_id = self._project_id, self._sample, self._editing_stamp_id
        px_per_mm_x, px_per_mm_y = self._px_per_mm_x, self._px_per_mm_y

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
            return stamp_id

        self._run(do_apply, on_done=self._on_edit_applied)

    def _on_edit_applied(self, stamp_id) -> None:
        self._end_edit()
        self.status_label.setText(
            f"{stamp_id}'s bounding box applied -- crop re-extracted. Existing "
            "perforation/colour results now refer to the old crop; re-run them."
        )
        self.changed.emit()

    def _cancel_edit(self) -> None:
        if self._adding:
            self._cancel_add()
            return
        if self._editing_stamp_id is None:
            return
        self._end_edit()
        self.status_label.setText("Edit cancelled.")

    def _end_edit(self) -> None:
        self._editing_stamp_id = None
        self._viewer.clear_perf_box()
        self._viewer.set_tool(TOOL_PAN)
        self.apply_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.list.clearSelection()
        self._show_all_boxes()

    # -- adding a stamp the detection pass missed ---------------------------

    def _start_add(self) -> None:
        if self._sample is None or self._px_per_mm_x is None:
            self.status_label.setText("No scale calibration for this sheet.")
            return
        self.list.clearSelection()
        self._editing_stamp_id = None
        library_root = self._library_root_getter()
        widths, heights = [], []
        for stamp_id in self._sample.stamp_ids:
            g = project_mod.load_stamp(library_root, self._project_id, stamp_id).geometry
            if g.width_mm and g.height_mm:
                widths.append(g.width_mm)
                heights.append(g.height_mm)
        # A new stamp's default box: the median size of this sheet's other
        # stamps (a sheet is usually one issue, so they're a good guess),
        # falling back to a generic definitive-size stamp if this is the
        # first one on the sheet. Centred on the viewer so it starts on
        # screen regardless of sheet size; the user drags it onto the
        # actual missed stamp from there.
        width_mm = float(np.median(widths)) if widths else 25.0
        height_mm = float(np.median(heights)) if heights else 30.0
        image_size = self._viewer.image_size()
        centre = (image_size[0] / 2.0, image_size[1] / 2.0) if image_size else (0.0, 0.0)
        box = cv2.boxPoints(
            (centre, (width_mm * self._px_per_mm_x, height_mm * self._px_per_mm_y), 0.0)
        )
        # Keep the other stamps' static boxes showing and stay zoomed to
        # the whole sheet here, unlike editing an existing one -- the user
        # doesn't yet know *where* the missed stamp is, so zooming in on
        # this box's arbitrary starting position would hide the very gap
        # they're trying to find.
        self._viewer.set_perf_box(box)
        self._viewer.fit_whole_image()
        self._viewer.set_tool(TOOL_PERF_BOX)
        self._adding = True
        self.apply_button.setText("Confirm add")
        self.apply_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self.status_label.setText("Drag the new rectangle onto the missed stamp, then Confirm add.")

    def _confirm_add(self) -> None:
        rect = self._viewer.get_perf_box_rect()
        if rect is None:
            return
        centre_px, size_px, angle_deg = rect
        library_root = self._library_root_getter()
        project_id, sample = self._project_id, self._sample
        px_per_mm_x, px_per_mm_y = self._px_per_mm_x, self._px_per_mm_y

        def do_add(progress=None):
            from stampan.core.models import GeometryResult, Stamp

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
            stamp = Stamp(
                stamp_id=project_mod.next_stamp_id(sample, len(sample.stamp_ids)),
                geometry=GeometryResult(
                    width_mm=size_px[0] / px_per_mm_x,
                    height_mm=size_px[1] / px_per_mm_y,
                    rotation_deg=transform.angle_deg,
                    sample_centre_px=transform.sample_centre_px,
                    crop_size_px=transform.crop_size_px,
                ),
            )
            project_mod.save_stamp(library_root, sample, stamp, crop)
            project_mod.save_sample(library_root, sample)
            return stamp.stamp_id

        self._run(do_add, on_done=self._on_add_confirmed)

    def _on_add_confirmed(self, stamp_id) -> None:
        self._adding = False
        self.apply_button.setText("Apply")
        library_root = self._library_root_getter()
        self._sample = project_mod.load_sample(library_root, self._project_id, self._sample_id)
        self._populate_list()
        self._end_edit()  # refreshed self._sample above, so the new box shows too
        self.status_label.setText(f"Added {stamp_id}.")
        self.changed.emit()

    def _cancel_add(self) -> None:
        self._adding = False
        self.apply_button.setText("Apply")
        self._end_edit()
        self.status_label.setText("Add cancelled.")


class PerforationPanel(_BusyPanel):
    """Owns the viewer's editable perforation-box overlay (SPEC §6) while
    this tab is active: seeded from the auto-detected stamp rectangle (or a
    saved override), draggable by its corner/rotate handles, with edits
    debounced into an automatic re-run -- "once that is done, you can
    proceed with the rest". A single top-of-panel gauge-range slider
    restricts which band of the FFT spectrum the highest peak is picked
    from (replacing four separate per-side spatial-ROI sliders, which were
    hard to use and didn't address the actual failure mode: a spurious
    peak elsewhere in the full 6-18 band winning over the real one)."""

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
        self._gauge_range: tuple[float, float] = DEFAULT_GAUGE_RANGE

        self._box_timer = QTimer(self)
        self._box_timer.setSingleShot(True)
        self._box_timer.timeout.connect(self._run_perforation)
        self._viewer.perf_box_changed.connect(self._on_box_edited)

        self._gauge_timer = QTimer(self)
        self._gauge_timer.setSingleShot(True)
        self._gauge_timer.timeout.connect(self._run_perforation)

        layout = QVBoxLayout(self)

        self.condition_note = QLabel("")
        self.condition_note.setWordWrap(True)
        self.condition_note.setStyleSheet("color: #9a5700; font-style: italic;")
        self.condition_note.setVisible(False)
        layout.addWidget(self.condition_note)

        layout.addWidget(
            _hint_label(
                "<i>Drag the yellow rectangle's corner handles to resize "
                "it, the cyan handle to rotate it, so it hugs the stamp's "
                "true edges. Drag the gauge-range slider below to the band "
                "where you can see the real tooth-spacing peak in each "
                "side's spectrum, so a spurious peak elsewhere doesn't win "
                "instead. Both re-run measurement automatically.</i>"
            )
        )

        gauge_row = QHBoxLayout()
        gauge_row.addWidget(QLabel("Gauge search range:"))
        self.gauge_range_label = QLabel("")
        gauge_row.addWidget(self.gauge_range_label)
        layout.addLayout(gauge_row)
        self.gauge_range_slider = _RangeSlider()
        self.gauge_range_slider.set_domain(perforation_mod.GAUGE_MIN, perforation_mod.GAUGE_MAX)
        self.gauge_range_slider.set_values(*DEFAULT_GAUGE_RANGE)
        self.gauge_range_slider.range_changed.connect(self._on_gauge_range_changed)
        layout.addWidget(self.gauge_range_slider)
        self._update_gauge_range_label()

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
        # actually has.
        self.side_images: dict[str, QLabel] = {}
        for side_name in perforation_mod.SIDES:
            content_layout.addWidget(QLabel(f"<b>{side_name}</b>"))
            img_label = QLabel("(run perforation to see this side's profile)")
            img_label.setWordWrap(True)
            img_label.setScaledContents(False)
            content_layout.addWidget(img_label)
            self.side_images[side_name] = img_label
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll, stretch=1)

    def _update_gauge_range_label(self) -> None:
        lo, hi = self._gauge_range
        self.gauge_range_label.setText(f"{lo:.2f} – {hi:.2f}")

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
        self._gauge_range = stamp.perforation.gauge_range or DEFAULT_GAUGE_RANGE
        self.gauge_range_slider.blockSignals(True)
        self.gauge_range_slider.set_values(*self._gauge_range)
        self.gauge_range_slider.blockSignals(False)
        self._update_gauge_range_label()

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
        gauge_range = self._gauge_range

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
                manual_box_px=box,
                condition=stamp.condition,
                multiplicity=stamp.multiplicity,
                multiplicity_count=stamp.multiplicity_count,
                gauge_range=gauge_range,
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
            stamp.perforation.gauge_range = gauge_range
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
            png = perforation_mod.render_side_profile(side)
            img_label.setPixmap(_png_to_pixmap(png, max_width=320))
            img_label.setText("")

    # -- gauge-range slider --------------------------------------------------

    def _on_gauge_range_changed(self, lo: float, hi: float) -> None:
        self._gauge_range = (lo, hi)
        self._update_gauge_range_label()
        self._gauge_timer.start(ROI_DEBOUNCE_MS)


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
    bounding box on sheet..." hands off to ``SheetStampsPanel`` (the
    right-panel view shown for this stamp's sample sheet), which owns all
    bounding-box editing -- both correcting a detected stamp and adding one
    a detection pass missed -- so there is exactly one implementation of
    "drag this rectangle, then re-extract the crop", not two.
    """

    def __init__(self, library_root_getter, edit_box_on_sheet, parent=None) -> None:
        super().__init__(parent)
        self._library_root_getter = library_root_getter
        self._edit_box_on_sheet = edit_box_on_sheet  # callback(project_id, stamp_id)
        self._project_id: str | None = None
        self._stamp_id: str | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.width_label = QLabel("")
        self.height_label = QLabel("")
        self.rotation_label = QLabel("")
        form.addRow("Width", self.width_label)
        form.addRow("Height", self.height_label)
        form.addRow("Rotation applied", self.rotation_label)

        self.edit_box_button = QPushButton("Edit bounding box on sheet...")
        self.edit_box_button.clicked.connect(
            lambda: self._edit_box_on_sheet(self._project_id, self._stamp_id)
        )
        form.addRow("Bounding box", self.edit_box_button)

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
        self.multiplicity_count_spin.setRange(1, 50)
        self.multiplicity_count_spin.setValue(1)
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
        is_grouped = label not in (
            _MULTIPLICITY_LABELS[None],
            _MULTIPLICITY_LABELS[models.MULTIPLICITY_SINGLE],
        )
        self.multiplicity_count_spin.setEnabled(is_grouped)
        if not is_grouped:
            self.multiplicity_count_spin.setValue(1)
        elif self.multiplicity_count_spin.value() < 2:
            self.multiplicity_count_spin.setValue(2)

    def set_stamp(self, project_id: str, stamp_id: str) -> None:
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
        # An unset condition/multiplicity defaults to soaked off / single --
        # the common case -- rather than an explicit "(not recorded)", so a
        # stamp fresh out of detection doesn't need a click just to accept
        # the ordinary default. Saving with these untouched writes that
        # default, which is the point: it's a default value, not a nag.
        condition = stamp.condition or models.CONDITION_SOAKED_OFF
        multiplicity = stamp.multiplicity or models.MULTIPLICITY_SINGLE
        self.condition_combo.setCurrentText(_CONDITION_LABELS.get(condition, condition))
        self.multiplicity_combo.setCurrentText(_MULTIPLICITY_LABELS.get(multiplicity, multiplicity))
        if multiplicity == models.MULTIPLICITY_SINGLE:
            self.multiplicity_count_spin.setValue(1)
        else:
            self.multiplicity_count_spin.setValue(stamp.multiplicity_count or 2)

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
