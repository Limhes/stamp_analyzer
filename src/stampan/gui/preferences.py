"""User preferences (QSettings) and the Preferences dialog.

General: where the library lives. Scanner: the default device and
resolution for new references, and the capture options a new reference is
scanned with. Existing references and their sheets are never affected: each
reference records the settings it was made with, and sheets are scanned
with their reference's own recorded set (SPEC §4.1).
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stampan.core import scanner as scanner_mod
from stampan.core import workflow
from stampan.gui import icons, theme
from stampan.gui.tasks import TaskRunner
from stampan.gui.widgets import Banner, button, label

_KEY_LIBRARY = "library/root"
_KEY_DEVICE = "scanner/device"
_KEY_DPI = "scanner/reference_dpi"
_KEY_OVERRIDES = "scanner/capture_overrides"
_KEY_CANCELLATIONS = "stamps/cancellation_types"

DEFAULT_CANCELLATION_TYPES = [
    "Circular date stamp (CDS)",
    "Double-ring CDS",
    "Numeral",
    "Barred / dotted obliterator",
    "Pen cancel",
    "Railway / TPO",
    "Ship / paquebot",
    "Machine cancel",
    "Rubber handstamp",
    "Cancelled to order (CTO)",
    "Fiscal / revenue",
    "Precancel",
    "Uncancelled",
]

_IDENTITY = ",".join(str(i) for i in range(256))


# -- stored values ---------------------------------------------------------------


def library_root() -> Path | None:
    value = QSettings().value(_KEY_LIBRARY, "", type=str)
    return Path(value) if value else None


def set_library_root(path: Path) -> None:
    QSettings().setValue(_KEY_LIBRARY, str(path))


def scanner_device() -> str | None:
    return QSettings().value(_KEY_DEVICE, "", type=str) or None


def reference_dpi() -> float:
    return float(QSettings().value(_KEY_DPI, 1200.0))


def cancellation_types() -> list[str]:
    text = QSettings().value(_KEY_CANCELLATIONS, "", type=str)
    try:
        value = json.loads(text) if text else None
    except ValueError:
        value = None
    if not isinstance(value, list):
        return list(DEFAULT_CANCELLATION_TYPES)
    return [str(v) for v in value if str(v).strip()]


def set_cancellation_types(types: list[str]) -> None:
    QSettings().setValue(_KEY_CANCELLATIONS, json.dumps(types))


def capture_overrides() -> dict[str, str]:
    text = QSettings().value(_KEY_OVERRIDES, "", type=str)
    try:
        value = json.loads(text) if text else {}
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def reference_capture_options(dpi: float) -> dict[str, str]:
    return workflow.reference_scan_options(dpi, capture_overrides())


def _display(value: str) -> str:
    return "identity (linear)" if value == _IDENTITY else value


def _stored(value: str) -> str:
    return _IDENTITY if value.strip() == "identity (linear)" else value.strip()


# -- dialog ----------------------------------------------------------------------


class PreferencesDialog(QDialog):
    def __init__(self, current_library: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setWindowIcon(icons.icon("edit"))
        self.resize(640, 560)
        self.runner = TaskRunner(self)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._general_tab(current_library), icons.icon("library"), "General")
        tabs.addTab(self._scanner_tab(), icons.icon("scanner"), "Scanner")
        tabs.addTab(self._stamps_tab(), icons.icon("stamp"), "Stamps")
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        theme.set_role(buttons.button(QDialogButtonBox.StandardButton.Ok), "primary")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- general ---------------------------------------------------------------

    def _general_tab(self, current_library: Path) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        row = QHBoxLayout()
        self.library_edit = QLineEdit(str(current_library))
        row.addWidget(self.library_edit, stretch=1)
        row.addWidget(button("Browse…", slot=self._browse_library))
        form.addRow("Library folder", row)
        form.addRow(
            "",
            label(
                "All references, projects, scans and measurements are stored here. "
                "Choosing another folder opens (or starts) a different library; nothing is "
                "moved or copied.",
                "caption",
                wrap=True,
            ),
        )
        return page

    def _browse_library(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Library Folder", self.library_edit.text())
        if path:
            self.library_edit.setText(path)

    # -- stamps ----------------------------------------------------------------

    def _stamps_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label("Cancellation types", "section"))
        layout.addWidget(
            label(
                "The choices offered for a stamp's cancellation. Double-click to edit; the "
                "order here is the order in the list. Renaming a type does not change stamps "
                "already recorded with the old name.",
                "caption",
                wrap=True,
            )
        )
        self.cancel_list = QListWidget()
        self.cancel_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for text in cancellation_types():
            self._add_cancel_item(text)
        layout.addWidget(self.cancel_list, stretch=1)
        row = QHBoxLayout()
        row.addWidget(button("Add Type", "plus", slot=self._new_cancel_type))
        row.addWidget(button("Remove", "clear", slot=self._remove_cancel_type))
        row.addWidget(button("Restore Defaults", "undo", slot=self._default_cancel_types))
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def _add_cancel_item(self, text: str) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.cancel_list.addItem(item)
        return item

    def _new_cancel_type(self) -> None:
        item = self._add_cancel_item("New type")
        self.cancel_list.setCurrentItem(item)
        self.cancel_list.editItem(item)

    def _remove_cancel_type(self) -> None:
        for item in self.cancel_list.selectedItems():
            self.cancel_list.takeItem(self.cancel_list.row(item))

    def _default_cancel_types(self) -> None:
        self.cancel_list.clear()
        for text in DEFAULT_CANCELLATION_TYPES:
            self._add_cancel_item(text)

    # -- scanner ---------------------------------------------------------------

    def _scanner_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        device_row = QHBoxLayout()
        self.device_combo = QComboBox()
        device_row.addWidget(self.device_combo, stretch=1)
        device_row.addWidget(
            button("", "refresh", "flat", "Look for scanners again", self._refresh_devices)
        )
        form.addRow("Default scanner", device_row)
        self.dpi_spin = QDoubleSpinBox()
        self.dpi_spin.setRange(50.0, 4800.0)
        self.dpi_spin.setDecimals(0)
        self.dpi_spin.setSuffix(" dpi")
        self.dpi_spin.setValue(reference_dpi())
        form.addRow("Reference resolution", self.dpi_spin)
        layout.addLayout(form)

        layout.addWidget(label("Capture settings for new references", "section"))
        layout.addWidget(
            label(
                "Applied and read back before every reference scan. The defaults switch all "
                "of the scanner's own processing off (identity gamma, no colour correction, "
                "no sharpening) so the colour model sees the sensor's response. Clear a value "
                "to leave that option unset. Existing references keep the settings they were "
                "made with, and their sheets are always scanned with exactly those.",
                "caption",
                wrap=True,
            )
        )
        self.options_table = QTableWidget(0, 2)
        self.options_table.setHorizontalHeaderLabels(["Option", "Value"])
        self.options_table.verticalHeader().setVisible(False)
        header = self.options_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.options_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.options_table, stretch=1)
        self._fill_options(reference_capture_options(self.dpi_spin.value()))

        row = QHBoxLayout()
        row.addWidget(button("Add Option", "plus", slot=self._add_option))
        row.addWidget(button("Remove", "clear", slot=self._remove_option))
        row.addWidget(button("Restore Defaults", "undo", slot=self._restore_defaults))
        row.addStretch(1)
        row.addWidget(button("Show Scanner Options…", "info", slot=self._show_scanner_options))
        layout.addLayout(row)
        self.scanner_banner = Banner()
        layout.addWidget(self.scanner_banner)
        self._refresh_devices()
        return page

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
        saved = scanner_device()
        index = self.device_combo.findData(saved) if saved else -1
        if index >= 0:
            self.device_combo.setCurrentIndex(index)

    def _fill_options(self, options: dict[str, str]) -> None:
        self.options_table.setRowCount(0)
        for key, value in options.items():
            if key == "resolution":
                continue  # set above
            self._append_row(key, _display(value))

    def _append_row(self, key: str, value: str) -> None:
        row = self.options_table.rowCount()
        self.options_table.insertRow(row)
        self.options_table.setItem(row, 0, QTableWidgetItem(key))
        self.options_table.setItem(row, 1, QTableWidgetItem(value))

    def _add_option(self) -> None:
        self._append_row("", "")
        row = self.options_table.rowCount() - 1
        self.options_table.setCurrentCell(row, 0)
        self.options_table.editItem(self.options_table.item(row, 0))

    def _remove_option(self) -> None:
        rows = sorted({i.row() for i in self.options_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.options_table.removeRow(row)

    def _restore_defaults(self) -> None:
        self._fill_options(workflow.reference_scan_options(self.dpi_spin.value()))

    def _table_options(self) -> dict[str, str]:
        options = {}
        for row in range(self.options_table.rowCount()):
            key_item, value_item = self.options_table.item(row, 0), self.options_table.item(row, 1)
            key = (key_item.text() if key_item else "").strip().lstrip("-")
            if key:
                options[key] = _stored(value_item.text() if value_item else "")
        return options

    def _show_scanner_options(self) -> None:
        device = self.device_combo.currentData()
        if device is None:
            self.scanner_banner.show_message("No scanner is connected.", "warning")
            return
        self.scanner_banner.show_message("Reading the scanner's options…", "info")

        def done(result: scanner_mod.ScannerOptions) -> None:
            self.scanner_banner.show_message("")
            _ScannerOptionsDialog(result, self).exec()

        def failed(message: str) -> None:
            self.scanner_banner.show_message(f"Could not read the options: {message}", "error")

        self.runner.run(
            scanner_mod.dump_options,
            device,
            description="Reading scanner options…",
            on_done=done,
            on_fail=failed,
        )

    # -- saving ----------------------------------------------------------------

    def _accept(self) -> None:
        settings = QSettings()
        settings.setValue(_KEY_DEVICE, self.device_combo.currentData() or "")
        settings.setValue(_KEY_DPI, self.dpi_spin.value())
        # Store only what differs from the built-in defaults, so later
        # improvements to the defaults still reach users who changed nothing.
        defaults = workflow.reference_scan_options(self.dpi_spin.value())
        table = self._table_options()
        overrides = {k: v for k, v in table.items() if defaults.get(k) != v}
        overrides.update({k: "" for k in defaults if k not in table and k != "resolution"})
        settings.setValue(_KEY_OVERRIDES, json.dumps(overrides))
        types = []
        for i in range(self.cancel_list.count()):
            text = self.cancel_list.item(i).text().strip()
            if text and text not in types:
                types.append(text)
        set_cancellation_types(types)
        self.runner.wait()
        self.accept()

    def library_path(self) -> Path:
        return Path(self.library_edit.text().strip()).expanduser()


class _ScannerOptionsDialog(QDialog):
    """Read-only view of every option the scanner reports right now."""

    def __init__(self, options: scanner_mod.ScannerOptions, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Scanner Options — {options.device}")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        table = QTableWidget(len(options.options), 3)
        table.setHorizontalHeaderLabels(["Option", "Current", "Allowed"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, opt in enumerate(options.options.values()):
            cells = (opt.name.lstrip("-"), opt.current or "—", opt.constraint)
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text if len(text) < 120 else text[:117] + "…")
                item.setToolTip(opt.description)
                table.setItem(row, col, item)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.reject)
        layout.addWidget(buttons)
        table.setTextElideMode(Qt.TextElideMode.ElideRight)
