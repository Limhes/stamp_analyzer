"""New Reference wizard (SPEC §4.4): acquire an IT8 scan, describe it, then
review the fitted calibration before it is saved to the library.

IT8 patch location is automatic (ArgyllCMS ``scanin``, SPEC §4.3); the
target may lie anywhere on the bed at any rotation. Geometric calibration
comes from the scan resolution. For a live capture that resolution has been
read back from the scanner by ``apply_and_verify``; for a loaded TIFF it is
taken from this app's embedded metadata or typed by hand, and the source is
recorded in the reference so its trustworthiness stays traceable.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from stampan.core import workflow
from stampan.core.calibration import reference as ref_mod
from stampan.core.io import read_tiff
from stampan.gui import icons, preferences
from stampan.gui.tasks import TaskRunner
from stampan.gui.widgets import Banner, ScaledImage, button, label

_DPI_VERIFIED_CAPTURE = "verified scanner setting (just captured)"
_DPI_VERIFIED_METADATA = "verified scanner setting (embedded scan metadata)"
_DPI_TYPED = "typed by hand (not embedded in this TIFF)"


def _dpi_spin(value: float = 1200.0) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(50.0, 4800.0)
    spin.setDecimals(1)
    spin.setValue(value)
    spin.setSuffix(" dpi")
    return spin


class _AcquirePage(QWizardPage):
    def __init__(self, wizard: ReferenceWizard) -> None:
        super().__init__()
        self.w = wizard
        self.setTitle("Acquire the IT8 target")
        self.setSubTitle(
            "Scan the target now, or load a scan made earlier. Place it anywhere on the bed — "
            "the patches are located automatically."
        )
        layout = QVBoxLayout(self)
        self.scanner_radio = QRadioButton("Scan the target now")
        self.file_radio = QRadioButton("Load an existing scan (16-bit TIFF)")
        group = QButtonGroup(self)
        group.addButton(self.scanner_radio)
        group.addButton(self.file_radio)

        layout.addWidget(self.scanner_radio)
        self.scanner_box = QWidget()
        form = QFormLayout(self.scanner_box)
        form.setContentsMargins(24, 0, 0, 0)
        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        device_row.addWidget(self.device_combo, stretch=1)
        device_row.addWidget(
            button("", "refresh", "flat", "Look for scanners again", self._refresh)
        )
        form.addRow("Scanner", device_row)
        self.dpi_spin = _dpi_spin(preferences.reference_dpi())
        form.addRow("Resolution", self.dpi_spin)
        form.addRow(
            "",
            label(
                "Applied and verified before scanning, using the capture settings in "
                "Preferences (by default every automatic correction off, identity gamma). "
                "Sample sheets later reuse exactly these settings.",
                "caption",
                wrap=True,
            ),
        )
        self.scan_button = button("Scan", "scanner", "primary", slot=self._scan)
        scan_row = QHBoxLayout()
        scan_row.addWidget(self.scan_button)
        scan_row.addStretch(1)
        form.addRow("", scan_row)
        layout.addWidget(self.scanner_box)

        layout.addWidget(self.file_radio)
        self.file_box = QWidget()
        file_row = QHBoxLayout(self.file_box)
        file_row.setContentsMargins(24, 0, 0, 0)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("Choose a TIFF file…")
        file_row.addWidget(self.path_edit, stretch=1)
        file_row.addWidget(button("Browse…", slot=self._browse))
        layout.addWidget(self.file_box)

        self.banner = Banner()
        layout.addWidget(self.banner)
        layout.addStretch(1)

        self.scanner_radio.toggled.connect(self._update_enabled)
        self._refresh()
        (self.scanner_radio if self.device_combo.currentData() else self.file_radio).setChecked(
            True
        )
        self._update_enabled()

    def _refresh(self) -> None:
        self.device_combo.clear()
        try:
            devices = workflow.list_scanners()
        except OSError:
            devices = []
        for d in devices:
            self.device_combo.addItem(icons.icon("scanner"), d.description, d.device_name)
        if not devices:
            self.device_combo.addItem("No scanner found", None)
        index = self.device_combo.findData(preferences.scanner_device())
        if index >= 0:
            self.device_combo.setCurrentIndex(index)
        self._update_enabled()

    def _update_enabled(self) -> None:
        scanning = self.scanner_radio.isChecked()
        self.scanner_box.setEnabled(scanning)
        self.file_box.setEnabled(not scanning)
        self.scan_button.setEnabled(scanning and self.device_combo.currentData() is not None)

    def _scan(self) -> None:
        device = self.device_combo.currentData()
        options = preferences.reference_capture_options(self.dpi_spin.value())
        self.scan_button.setEnabled(False)
        self.banner.show_message("Scanning…", "info")

        def done(result: workflow.CaptureResult) -> None:
            self._update_enabled()
            self.w.set_scan(result.image, result.metadata, _DPI_VERIFIED_CAPTURE, None)
            text = "Scan captured."
            if result.warnings:
                text += "<br>" + "<br>".join(result.warnings)
            self.banner.show_message(text, "warning" if result.warnings else "success")
            self.completeChanged.emit()

        def failed(message: str) -> None:
            self._update_enabled()
            self.banner.show_message(f"Scanning failed: {message}", "error")

        self.w.runner.run(
            workflow.capture_scan,
            options,
            device=device,
            description="Scanning IT8 target…",
            on_done=done,
            on_fail=failed,
        )

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "IT8 Target Scan", "", "TIFF images (*.tif *.tiff)"
        )
        if not path:
            return
        try:
            image, metadata = read_tiff(path)
        except Exception as exc:  # noqa: BLE001 -- any unreadable file is reported, not raised
            self.banner.show_message(f"Could not read that file: {exc}", "error")
            return
        self.path_edit.setText(path)
        resolution = metadata.scanner_options.get("resolution") if metadata else None
        source = _DPI_VERIFIED_METADATA if resolution is not None else _DPI_TYPED
        self.w.set_scan(image, metadata, source, float(resolution) if resolution else None)
        if resolution is None:
            self.banner.show_message(
                "This TIFF carries no scan resolution. Enter it on the next page; the scale "
                "is only as trustworthy as that number.",
                "warning",
            )
        else:
            self.banner.show_message(
                f"Loaded: {float(resolution):.0f} dpi from the file's metadata.", "success"
            )
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 -- Qt override
        return self.w.scan is not None and not self.w.runner.is_running()


class _DetailsPage(QWizardPage):
    def __init__(self, wizard: ReferenceWizard) -> None:
        super().__init__()
        self.w = wizard
        self.setTitle("Describe the reference")
        self.setSubTitle(
            "A name to recognise it by, its resolution, and the target's reference data."
        )
        form = QFormLayout(self)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. “Epson 1200U, Oct 2026”")
        self.name_edit.textChanged.connect(self.completeChanged)
        form.addRow("Name", self.name_edit)
        self.dpi_x = _dpi_spin()
        form.addRow("Resolution", self.dpi_x)
        self.dpi_y_check = QCheckBox("Vertical resolution differs")
        self.dpi_y = _dpi_spin()
        self.dpi_y.setEnabled(False)
        self.dpi_y_check.toggled.connect(self.dpi_y.setEnabled)
        form.addRow("", self.dpi_y_check)
        form.addRow("Vertical", self.dpi_y)
        self.dpi_source = label("", "caption", wrap=True)
        form.addRow("", self.dpi_source)
        cgats_row = QHBoxLayout()
        self.cgats_edit = QLineEdit(str(workflow.DEFAULT_CGATS_PATH))
        self.cgats_edit.textChanged.connect(self.completeChanged)
        cgats_row.addWidget(self.cgats_edit, stretch=1)
        cgats_row.addWidget(button("Browse…", slot=self._browse_cgats))
        form.addRow("Target data (CGATS)", cgats_row)
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Optional")
        self.notes_edit.setMaximumHeight(80)
        form.addRow("Notes", self.notes_edit)

    def initializePage(self) -> None:  # noqa: N802 -- Qt override
        locked = self.w.dpi_source != _DPI_TYPED
        if self.w.dpi is not None:
            self.dpi_x.setValue(self.w.dpi)
            self.dpi_y_check.setChecked(False)
        for widget in (self.dpi_x, self.dpi_y_check):
            widget.setEnabled(not locked)
        self.dpi_source.setText(f"Source: {self.w.dpi_source}")

    def _browse_cgats(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "CGATS Reference Data", "", "Text (*.txt *.cie *.it8)"
        )
        if path:
            self.cgats_edit.setText(path)

    def isComplete(self) -> bool:  # noqa: N802 -- Qt override
        return (
            bool(self.name_edit.text().strip()) and Path(self.cgats_edit.text().strip()).is_file()
        )


class _ReviewPage(QWizardPage):
    def __init__(self, wizard: ReferenceWizard) -> None:
        super().__init__()
        self.w = wizard
        self.setTitle("Review the calibration")
        self.setSubTitle("Check the patch location and the colour model's accuracy before saving.")
        self.setCommitPage(True)
        self.setButtonText(QWizard.WizardButton.CommitButton, "Save Reference")
        layout = QVBoxLayout(self)
        self.banner = Banner()
        layout.addWidget(self.banner)
        self.summary = label("", wrap=True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Model", "Mean ΔE00", "p95", "Max"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setMaximumHeight(150)
        layout.addWidget(self.table)
        self.image = ScaledImage(placeholder="Locating patches…")
        layout.addWidget(self.image, stretch=1)

    def initializePage(self) -> None:  # noqa: N802 -- Qt override
        self.w.result = None
        self.summary.setText("")
        self.table.setRowCount(0)
        self.image.clear()
        self.banner.show_message("Locating the patches and fitting the colour model…", "info")
        details = self.w.details_page
        dpi_y = details.dpi_y.value() if details.dpi_y_check.isChecked() else None

        def failed(message: str) -> None:
            self.banner.show_message(
                f"The reference could not be built: {message}<br>Go back to correct the input.",
                "error",
            )

        self.w.runner.run(
            ref_mod.build_reference,
            name=details.name_edit.text().strip(),
            scan=self.w.scan,
            scan_metadata=self.w.metadata,
            cgats_reference_path=Path(details.cgats_edit.text().strip()),
            dpi_x=details.dpi_x.value(),
            dpi_y=dpi_y,
            dpi_source=self.w.dpi_source,
            notes=details.notes_edit.toPlainText(),
            description="Building reference…",
            on_done=self._show,
            on_fail=failed,
        )

    def _show(self, result: ref_mod.ReferenceBuildResult) -> None:
        self.w.result = result
        ref = result.reference
        lines = []
        if ref.scale:
            lines.append(f"Scale {ref.scale.px_per_mm_x:.3f} × {ref.scale.px_per_mm_y:.3f} px/mm")
        if ref.colour_model:
            cv = ref.colour_model.cv_delta_e00
            lines.append(
                f"Chosen colour model <b>{ref.colour_model.kind}</b>: cross-validated ΔE00 "
                f"mean {cv.get('mean', float('nan')):.2f}, p95 {cv.get('p95', float('nan')):.2f}"
            )
        self.summary.setText("<br>".join(lines))
        chosen = ref.colour_model.kind if ref.colour_model else None
        self.table.setRowCount(len(result.model_comparison))
        for row, (kind, stats) in enumerate(result.model_comparison.items()):
            cells = [kind + ("  ✓" if kind == chosen else "")] + [
                f"{stats.get(k, float('nan')):.2f}" for k in ("mean", "p95", "max")
            ]
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        notes = [line for line in ref.notes.splitlines() if line.strip()]
        self.banner.show_message("<br>".join(notes), "info")
        self.image.set_png(
            result.diagnostics.get("it8_grid.png")
            or result.diagnostics.get("colour_model_error_heatmap.png")
        )
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 -- Qt override
        return self.w.result is not None


class ReferenceWizard(QWizard):
    #: Emitted with the new reference's id once it has been saved.
    saved = Signal(str)

    def __init__(self, library_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.library_root = library_root
        self.setWindowTitle("New Calibration Reference")
        self.setWindowIcon(icons.icon("target"))
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnLastPage, True)
        self.resize(720, 640)
        self.runner = TaskRunner(self)
        self.scan = None
        self.metadata = None
        self.dpi_source = _DPI_TYPED
        self.dpi: float | None = None
        self.result: ref_mod.ReferenceBuildResult | None = None

        self.acquire_page = _AcquirePage(self)
        self.details_page = _DetailsPage(self)
        self.review_page = _ReviewPage(self)
        for page in (self.acquire_page, self.details_page, self.review_page):
            self.addPage(page)
        self.button(QWizard.WizardButton.CommitButton).clicked.connect(self._save)

    def set_scan(self, image, metadata, dpi_source: str, dpi: float | None) -> None:
        self.scan, self.metadata, self.dpi_source = image, metadata, dpi_source
        if dpi is None and metadata is not None:
            res = metadata.scanner_options.get("resolution")
            dpi = float(res) if res is not None else None
        self.dpi = dpi

    def _save(self) -> None:
        if self.result is None:
            return
        ref_mod.save_reference(self.result, self.library_root)
        self.saved.emit(self.result.reference.ref_id)
        self.accept()

    def done(self, code: int) -> None:
        self.runner.wait()
        super().done(code)
