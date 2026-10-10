"""Choose how a sample sheet enters a project: scanned now, or an existing
TIFF -- and which calibration reference it is measured against."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from stampan.core import workflow
from stampan.core.calibration import reference as ref_mod
from stampan.gui import icons, preferences, theme
from stampan.gui.widgets import Banner, button, label

SOURCE_SCANNER = "scanner"
SOURCE_FILE = "file"


@dataclass
class SheetSource:
    ref_id: str
    source: str  # SOURCE_SCANNER | SOURCE_FILE
    device: str | None = None
    path: Path | None = None


class AddSheetDialog(QDialog):
    def __init__(self, library_root: Path, project_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Sample Sheet")
        self.setWindowIcon(icons.icon("sheet"))
        self.setMinimumWidth(520)
        self._refs = [r for r in ref_mod.list_references(library_root) if not r.archived]

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(label(f"Add a sheet to “{project_name}”", "title"))
        layout.addWidget(
            label(
                "Stamps on the sheet are detected and cropped automatically. "
                "Measurements use the calibration reference chosen here.",
                "subtitle",
                wrap=True,
            )
        )

        ref_box = QGroupBox("Calibration reference")
        ref_form = QFormLayout(ref_box)
        self.ref_combo = QComboBox()
        for r in self._refs:
            cv = r.colour_model.cv_delta_e00.get("mean") if r.colour_model else None
            detail = f"ΔE00 {cv:.2f}" if cv is not None else "no colour model"
            self.ref_combo.addItem(icons.icon("target"), f"{r.name}  ({r.created}, {detail})", r)
        if self._refs:
            self.ref_combo.setCurrentIndex(len(self._refs) - 1)  # newest
        ref_form.addRow("Reference", self.ref_combo)
        layout.addWidget(ref_box)

        source_box = QGroupBox("Source")
        source_layout = QVBoxLayout(source_box)
        self.scanner_radio = QRadioButton("Scan the sheet now")
        self.file_radio = QRadioButton("Import an existing scan (16-bit TIFF)")
        group = QButtonGroup(self)
        group.addButton(self.scanner_radio)
        group.addButton(self.file_radio)

        source_layout.addWidget(self.scanner_radio)
        scanner_row = QWidget()
        scanner_form = QFormLayout(scanner_row)
        scanner_form.setContentsMargins(24, 0, 0, 0)
        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        device_row.addWidget(self.device_combo, stretch=1)
        device_row.addWidget(
            button("", "refresh", "flat", "Look for scanners again", self._refresh_devices)
        )
        scanner_form.addRow("Scanner", device_row)
        self.settings_note = label("", "caption", wrap=True)
        scanner_form.addRow("", self.settings_note)
        source_layout.addWidget(scanner_row)

        source_layout.addWidget(self.file_radio)
        file_row = QWidget()
        file_layout = QHBoxLayout(file_row)
        file_layout.setContentsMargins(24, 0, 0, 0)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Choose a TIFF file…")
        file_layout.addWidget(self.path_edit, stretch=1)
        file_layout.addWidget(button("Browse…", slot=self._browse))
        source_layout.addWidget(file_row)
        layout.addWidget(source_box)

        self.banner = Banner()
        layout.addWidget(self.banner)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        theme.set_role(self.ok_button, "primary")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._scanner_widgets = [scanner_row]
        self._file_widgets = [file_row]
        self.scanner_radio.toggled.connect(self._validate)
        self.file_radio.toggled.connect(self._validate)
        self.ref_combo.currentIndexChanged.connect(self._validate)
        self.path_edit.textChanged.connect(self._validate)
        self.device_combo.currentIndexChanged.connect(self._validate)

        self._refresh_devices()
        (self.scanner_radio if self.device_combo.currentData() else self.file_radio).setChecked(
            True
        )
        self._validate()

    def _refresh_devices(self) -> None:
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
        self._validate()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Sample Sheet Scan", "", "TIFF images (*.tif *.tiff)"
        )
        if path:
            self.path_edit.setText(path)
            self.file_radio.setChecked(True)

    def _validate(self) -> None:
        scanning = self.scanner_radio.isChecked()
        for w in self._scanner_widgets:
            w.setEnabled(scanning)
        for w in self._file_widgets:
            w.setEnabled(not scanning)

        problem = ""
        ref = self.ref_combo.currentData()
        if ref is None:
            problem = (
                "There is no calibration reference yet. Create one first (File ▸ New Reference)."
            )
        elif ref.scale is None:
            problem = "This reference has no geometric calibration, so stamps can't be measured."
        elif scanning:
            if self.device_combo.currentData() is None:
                problem = "No scanner is connected."
            else:
                try:
                    options = workflow.sample_scan_options(ref)
                    self.settings_note.setText(
                        f"The reference's own settings are applied and verified before "
                        f"scanning: {options['resolution']} dpi, {options.get('mode', 'Color')}, "
                        "no automatic corrections."
                    )
                except workflow.WorkflowError as exc:
                    problem = str(exc)
        else:
            path = self.path_edit.text().strip()
            if not path:
                problem = " "  # incomplete, not an error worth showing
            elif not Path(path).is_file():
                problem = "That file does not exist."
        self.banner.show_message(problem.strip(), "warning")
        self.ok_button.setEnabled(not problem)

    def result_source(self) -> SheetSource:
        ref = self.ref_combo.currentData()
        if self.scanner_radio.isChecked():
            return SheetSource(ref.ref_id, SOURCE_SCANNER, device=self.device_combo.currentData())
        return SheetSource(ref.ref_id, SOURCE_FILE, path=Path(self.path_edit.text().strip()))
