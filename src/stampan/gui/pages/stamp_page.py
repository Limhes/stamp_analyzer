"""The stamp page: identification and specimen details, perforation and
colour, for one stamp -- with its crop shown in the viewer."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from stampan.core import models, workflow
from stampan.core import project as project_mod
from stampan.gui import icons, preferences
from stampan.gui.library_tree import KIND_SAMPLE, KIND_STAMP
from stampan.gui.pages.base import AppContext, Page
from stampan.gui.pages.colour_tab import ColourTab
from stampan.gui.pages.perforation_tab import PerforationTab
from stampan.gui.viewer import TOOL_PAN
from stampan.gui.widgets import Banner, button, label

MODE_DETAILS = "details"
MODE_PERFORATION = "perforation"
MODE_COLOUR = "colour"
MODE_COLOUR_AREA = "colour-area"  # a colour is selected: its area can be drawn

_CONDITIONS = {
    models.CONDITION_SOAKED_OFF: "Soaked off",
    models.CONDITION_ON_PAPER: "On paper",
}
_MULTIPLICITIES = {
    models.MULTIPLICITY_SINGLE: "Single",
    models.MULTIPLICITY_PAIR: "Pair",
    models.MULTIPLICITY_STRIP: "Strip",
    models.MULTIPLICITY_BLOCK: "Block",
}


class DetailsTab(QWidget):
    """Catalogue numbers, issue, condition and multiplicity, notes, and the
    detected size. Condition and multiplicity feed the perforation
    measurement (SPEC §6): on paper lowers its confidence; a pair, strip
    or block may have edges shared with a neighbour."""

    saved = Signal()

    def __init__(self, ctx: AppContext, edit_rectangle, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.project_id: str | None = None
        self.stamp_id: str | None = None
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        ident = QGroupBox("Identification")
        form = QFormLayout(ident)
        self.catalogue_edit = QLineEdit()
        self.catalogue_edit.setPlaceholderText("e.g. SG 123, Mi 45 (comma-separated)")
        form.addRow("Catalogue no.", self.catalogue_edit)
        self.country_edit = QLineEdit()
        self.country_edit.setPlaceholderText("e.g. Netherlands, 1899 Wilhelmina")
        form.addRow("Country / issue", self.country_edit)
        layout.addWidget(ident)

        specimen = QGroupBox("Specimen")
        form = QFormLayout(specimen)
        self.condition_combo = QComboBox()
        for key, text in _CONDITIONS.items():
            self.condition_combo.addItem(text, key)
        form.addRow("Condition", self.condition_combo)
        multiplicity_row = QHBoxLayout()
        self.multiplicity_combo = QComboBox()
        for key, text in _MULTIPLICITIES.items():
            self.multiplicity_combo.addItem(text, key)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(2, 50)
        self.count_spin.setSuffix(" stamps")
        multiplicity_row.addWidget(self.multiplicity_combo, stretch=1)
        multiplicity_row.addWidget(self.count_spin)
        form.addRow("Multiplicity", multiplicity_row)
        layout.addWidget(specimen)

        cancel = QGroupBox("Cancellation")
        form = QFormLayout(cancel)
        self.cancel_type_combo = QComboBox()
        self.cancel_type_combo.setToolTip("The list is set in Edit ▸ Preferences ▸ Stamps")
        form.addRow("Type", self.cancel_type_combo)
        self.cancel_date_edit = QLineEdit()
        self.cancel_date_edit.setPlaceholderText("YYYY, YYYY-MM or YYYY-MM-DD")
        self.cancel_date_edit.setToolTip(
            "As precise as the postmark allows: a year alone, a month, or a full date"
        )
        form.addRow("Date", self.cancel_date_edit)
        self.cancel_estimated_check = QCheckBox("Estimated — not read from a legible postmark")
        self.cancel_estimated_check.setToolTip(
            "Tick when the date is inferred (partial strike, usage period, other stamps on "
            "the sheet) rather than read from the cancellation itself."
        )
        form.addRow("", self.cancel_estimated_check)
        layout.addWidget(cancel)

        geometry = QGroupBox("Size")
        form = QFormLayout(geometry)
        self.size_label = label("—")
        self.rotation_label = label("—")
        form.addRow("Width × height", self.size_label)
        form.addRow("Rotation corrected", self.rotation_label)
        form.addRow(
            "",
            button(
                "Adjust Rectangle on Sheet…",
                "box",
                tooltip="Correct the detected outline of this stamp on its sheet",
                slot=lambda: edit_rectangle(self.project_id, self.stamp_id),
            ),
        )
        layout.addWidget(geometry)

        notes = QGroupBox("Notes")
        notes_layout = QVBoxLayout(notes)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMinimumHeight(90)
        notes_layout.addWidget(self.notes_edit)
        layout.addWidget(notes)

        self.banner = Banner()
        layout.addWidget(self.banner)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.save_button = button("Save", "check", "primary", "Save details (Ctrl+S)", self.save)
        save_row.addWidget(self.save_button)
        layout.addLayout(save_row)
        layout.addStretch(1)
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save)

        for signal in (
            self.catalogue_edit.textChanged,
            self.country_edit.textChanged,
            self.notes_edit.textChanged,
            self.condition_combo.currentIndexChanged,
            self.multiplicity_combo.currentIndexChanged,
            self.count_spin.valueChanged,
            self.cancel_type_combo.currentIndexChanged,
            self.cancel_date_edit.textChanged,
            self.cancel_estimated_check.toggled,
        ):
            signal.connect(self._on_edited)

    def set_stamp(self, project_id: str, stamp_id: str) -> None:
        self.project_id, self.stamp_id = project_id, stamp_id
        stamp = project_mod.load_stamp(self.ctx.library_root, project_id, stamp_id)
        self._loading = True
        self.catalogue_edit.setText(", ".join(stamp.catalogue_numbers))
        self.country_edit.setText(stamp.country_issue or "")
        self.notes_edit.setPlainText(stamp.notes)
        # Unrecorded condition/multiplicity show the common case (soaked
        # off, single); saving then records that default explicitly.
        self.condition_combo.setCurrentIndex(
            self.condition_combo.findData(stamp.condition or models.CONDITION_SOAKED_OFF)
        )
        self.multiplicity_combo.setCurrentIndex(
            self.multiplicity_combo.findData(stamp.multiplicity or models.MULTIPLICITY_SINGLE)
        )
        self.count_spin.setValue(max(2, stamp.multiplicity_count or 2))
        self._fill_cancel_types(stamp.cancellation.type)
        self.cancel_date_edit.setText(stamp.cancellation.date or "")
        self.cancel_estimated_check.setChecked(stamp.cancellation.date_estimated)
        g = stamp.geometry
        self.size_label.setText(
            f"{g.width_mm:.2f} × {g.height_mm:.2f} mm" if g.width_mm and g.height_mm else "—"
        )
        self.rotation_label.setText(f"{g.rotation_deg:.2f}°" if g.rotation_deg is not None else "—")
        self._loading = False
        self._update_count_enabled()
        self.save_button.setEnabled(False)
        self.banner.show_message("")

    def _fill_cancel_types(self, current: str | None) -> None:
        self.cancel_type_combo.clear()
        self.cancel_type_combo.addItem("—  not recorded", None)
        types = preferences.cancellation_types()
        if current and current not in types:
            types.append(current)  # recorded before the list changed: keep it
        for text in types:
            self.cancel_type_combo.addItem(text, text)
        self.cancel_type_combo.setCurrentIndex(max(0, self.cancel_type_combo.findData(current)))

    def _update_count_enabled(self) -> None:
        self.count_spin.setEnabled(
            self.multiplicity_combo.currentData() != models.MULTIPLICITY_SINGLE
        )

    def _on_edited(self, *_args) -> None:
        self._update_count_enabled()
        if not self._loading:
            self.save_button.setEnabled(True)
            self.banner.show_message("")

    def save(self) -> None:
        if self.stamp_id is None or not self.save_button.isEnabled():
            return
        root = self.ctx.library_root
        stamp = project_mod.load_stamp(root, self.project_id, self.stamp_id)
        stamp.catalogue_numbers = [
            s.strip() for s in self.catalogue_edit.text().split(",") if s.strip()
        ]
        stamp.country_issue = self.country_edit.text().strip() or None
        stamp.notes = self.notes_edit.toPlainText()
        stamp.condition = self.condition_combo.currentData()
        stamp.multiplicity = self.multiplicity_combo.currentData()
        stamp.multiplicity_count = (
            self.count_spin.value() if stamp.multiplicity != models.MULTIPLICITY_SINGLE else None
        )
        stamp.cancellation.type = self.cancel_type_combo.currentData()
        stamp.cancellation.date_estimated = self.cancel_estimated_check.isChecked()
        date_problem = ""
        try:
            stamp.cancellation.date = models.parse_partial_date(self.cancel_date_edit.text())
        except ValueError as exc:
            date_problem = f"The cancellation date was not saved: {exc}."
        project_mod.save_stamp_results(root, self.project_id, stamp)
        if date_problem:
            self.banner.show_message(date_problem, "error")
            return  # leave Save enabled until the date is fixed
        self._loading = True  # show the normalised date without marking it edited
        self.cancel_date_edit.setText(stamp.cancellation.date or "")
        self._loading = False
        self.save_button.setEnabled(False)
        self.banner.show_message("Saved.", "success")
        self.saved.emit()


class StampPage(Page):
    mode_changed = Signal(str)
    #: The user asked to correct this stamp's rectangle on its sheet.
    edit_rectangle_requested = Signal(str)

    def __init__(self, ctx: AppContext, sibling_stamp, parent=None) -> None:
        super().__init__(ctx, parent=parent)
        self._sibling_stamp = sibling_stamp  # (project_id, stamp_id, step) -> stamp_id | None
        self.project_id: str | None = None
        self.stamp_id: str | None = None

        self.prev_button = self._nav_button("chevron-left", "Previous stamp (Alt+Left)", -1)
        self.next_button = self._nav_button("chevron-right", "Next stamp (Alt+Right)", +1)
        self.header.add_action(self.prev_button)
        self.header.add_action(self.next_button)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.details = DetailsTab(ctx, self._edit_rectangle)
        self.perforation = PerforationTab(ctx, self.runner)
        self.colour = ColourTab(ctx, self.runner)
        self.tabs.addTab(self.details, icons.icon("details"), "Details")
        self.tabs.addTab(self.perforation, icons.icon("perforation"), "Perforation")
        self.tabs.addTab(self.colour, icons.icon("palette"), "Colour")
        self.body_layout.addWidget(self.tabs)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.details.saved.connect(self.changed)
        self.details.saved.connect(self._refresh_header)
        self.perforation.changed.connect(self.changed)
        self.colour.changed.connect(self.changed)
        self.colour.area_editing.connect(
            lambda on: self.mode_changed.emit(MODE_COLOUR_AREA if on else MODE_COLOUR)
        )

    def _nav_button(self, icon_name: str, tooltip: str, step: int) -> QToolButton:
        tool = QToolButton()
        tool.setIcon(icons.icon(icon_name))
        tool.setToolTip(tooltip)
        tool.clicked.connect(lambda: self.step(step))
        return tool

    def set_stamp(self, project_id: str, stamp_id: str) -> None:
        self.details.save()  # keep unsaved edits when moving to another stamp
        self.project_id, self.stamp_id = project_id, stamp_id
        root = self.ctx.library_root
        crop = project_mod.load_stamp_crop(root, project_id, stamp_id)
        sample, reference = workflow.reference_for_stamp(root, project_id, stamp_id)
        scale = (
            (reference.scale.px_per_mm_x, reference.scale.px_per_mm_y)
            if reference.scale
            else (None, None)
        )
        model = workflow.colour_model_for_stamp(root, project_id, stamp_id)
        self.ctx.viewer.set_image(crop, *scale, model.predict_lab if model else None)
        self.ctx.viewer.set_tool(TOOL_PAN)

        self._sample_id = sample.sample_id
        self._sheet_title = project_mod.sheet_title(sample)
        self._project_name = project_mod.load_project(root, project_id).name
        self._refresh_header()
        self.prev_button.setEnabled(self._sibling_stamp(project_id, stamp_id, -1) is not None)
        self.next_button.setEnabled(self._sibling_stamp(project_id, stamp_id, +1) is not None)

        self.details.set_stamp(project_id, stamp_id)
        self.perforation.set_stamp(project_id, stamp_id, crop)
        self.colour.set_stamp(project_id, stamp_id)
        self._on_tab_changed(self.tabs.currentIndex())

    def _refresh_header(self) -> None:
        stamp = project_mod.load_stamp(self.ctx.library_root, self.project_id, self.stamp_id)
        title = project_mod.stamp_label(self.stamp_id)
        if stamp.catalogue_numbers:
            title += f"  ·  {', '.join(stamp.catalogue_numbers)}"
        subtitle = stamp.country_issue or ""
        self.header.set_text(
            title, subtitle, breadcrumb=f"{self._project_name}  ›  {self._sheet_title}"
        )

    def _on_tab_changed(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is not self.perforation:
            self.perforation.deactivate()
        if widget is not self.colour:
            self.colour.deactivate()
        if widget is self.perforation:
            self.perforation.activate()
            self.mode_changed.emit(MODE_PERFORATION)
        elif widget is self.colour:
            self.ctx.viewer.set_tool(TOOL_PAN)
            self.mode_changed.emit(MODE_COLOUR)
        else:
            self.ctx.viewer.set_tool(TOOL_PAN)
            self.mode_changed.emit(MODE_DETAILS)

    def deactivate(self) -> None:
        self.details.save()
        self.perforation.deactivate()
        self.colour.deactivate()

    def step(self, step: int) -> None:
        if self.stamp_id is None:
            return
        target = self._sibling_stamp(self.project_id, self.stamp_id, step)
        if target is not None:
            self.ctx.navigate(KIND_STAMP, self.project_id, target)

    def _edit_rectangle(self, project_id: str | None, stamp_id: str | None) -> None:
        if project_id is None or stamp_id is None:
            return
        self.ctx.navigate(KIND_SAMPLE, project_id, self._sample_id)
        self.edit_rectangle_requested.emit(stamp_id)
