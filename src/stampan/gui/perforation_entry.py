"""Typing a stamp's perforation by hand (SPEC §6.2), in three levels of
detail: one gauge all round, horizontal × vertical, or per side. The level
last used is remembered. Parsing and validation are the core's
(``perforation.sides_from_notation`` / ``parse_side_value``), the same as
for the overview table's perforation cell.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from stampan.core import perforation as perforation_mod
from stampan.core.models import PerforationResult, PerforationSide
from stampan.gui import theme
from stampan.gui.widgets import button, label

MODE_ONE, MODE_HV, MODE_SIDES = "one", "hv", "sides"
_MODES = {MODE_ONE: "One value", MODE_HV: "H × V", MODE_SIDES: "Per side"}
_MODE_KEY = "perforation/entry_mode"
_SIDE_WORDS = {"imperf": "imperf", "straight_edge": "straight edge"}


def _side_text(side) -> str:
    if side is None:
        return ""
    if side.kind in _SIDE_WORDS:
        return _SIDE_WORDS[side.kind]
    return perforation_mod.pretty_notation(side.notation or "")


def _field(placeholder: str) -> QLineEdit:
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    return edit


class PerforationEntry(QWidget):
    """Emits ``apply_requested`` with {side: (kind, gauge)} -- an empty dict
    when every field is empty (no override)."""

    apply_requested = Signal(dict)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        modes = QHBoxLayout()
        modes.setSpacing(0)
        self._mode_group = QButtonGroup(self)
        self._mode_buttons: dict[str, QPushButton] = {}
        for key, text in _MODES.items():
            mode_button = QPushButton(text)
            mode_button.setCheckable(True)
            theme.set_role(mode_button, "segment")
            self._mode_group.addButton(mode_button)
            self._mode_buttons[key] = mode_button
            mode_button.clicked.connect(lambda _c=False, key=key: self.set_mode(key))
            modes.addWidget(mode_button)
        modes.addStretch(1)
        layout.addLayout(modes)

        self.stack = QStackedWidget()
        self.one = _field("e.g. 11½, 11.25 or imperf")
        self.horizontal = _field("top/bottom, e.g. 11 or 11/12")
        self.vertical = _field("left/right")
        self.side_edits = {side: _field("measured") for side in perforation_mod.SIDES}

        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(label("All four sides"))
        row.addWidget(self.one, stretch=1)
        self._pages = {MODE_ONE: page}

        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(label("Horizontal"), 0, 0)
        grid.addWidget(self.horizontal, 0, 1)
        grid.addWidget(label("× Vertical"), 0, 2)
        grid.addWidget(self.vertical, 0, 3)
        self._pages[MODE_HV] = page

        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        for i, side in enumerate(perforation_mod.SIDES):
            grid.addWidget(label(side.capitalize()), i // 2, (i % 2) * 2)
            grid.addWidget(self.side_edits[side], i // 2, (i % 2) * 2 + 1)
        self._pages[MODE_SIDES] = page
        for page in self._pages.values():
            self.stack.addWidget(page)
        layout.addWidget(self.stack)

        bottom = QHBoxLayout()
        self.preview = label("", "caption", wrap=True)
        bottom.addWidget(self.preview, stretch=1)
        self.apply_button = button(
            "Set", "check", "primary", "Set the perforation by hand (Enter)", self._apply
        )
        bottom.addWidget(self.apply_button)
        layout.addLayout(bottom)

        for edit in self._edits():
            edit.textChanged.connect(self._update_preview)
            edit.returnPressed.connect(self._apply)

        mode = QSettings().value(_MODE_KEY, MODE_ONE)
        self.set_mode(mode if mode in _MODES else MODE_ONE, remember=False)

    def _edits(self) -> list[QLineEdit]:
        return [self.one, self.horizontal, self.vertical, *self.side_edits.values()]

    # -- mode ------------------------------------------------------------------

    def mode(self) -> str:
        return next(k for k, b in self._mode_buttons.items() if b.isChecked())

    def set_mode(self, mode: str, remember: bool = True) -> None:
        self._mode_buttons[mode].setChecked(True)
        self.stack.setCurrentWidget(self._pages[mode])
        if remember:
            QSettings().setValue(_MODE_KEY, mode)
        self._update_preview()
        self.focus()

    def focus(self) -> None:
        """Put the cursor in the first field, all selected: type and Enter."""
        first = {MODE_ONE: self.one, MODE_HV: self.horizontal}.get(
            self.mode(), self.side_edits["top"]
        )
        first.setFocus(Qt.FocusReason.OtherFocusReason)
        first.selectAll()

    # -- values ----------------------------------------------------------------

    def set_perforation(self, perforation: PerforationResult) -> None:
        """Start from what the stamp has now: hand-set values, else measured."""
        sides = perforation.effective()
        texts = {side: _side_text(sides.get(side)) for side in perforation_mod.SIDES}
        for side, edit in self.side_edits.items():
            # Only hand-set sides are filled; the others show "measured".
            edit.setText(texts[side] if side in perforation.manual else "")

        def pair(a: str, b: str) -> str:
            if texts[a] == texts[b]:
                return texts[a]
            return f"{texts[a]}/{texts[b]}" if texts[a] and texts[b] else ""

        horizontal, vertical = pair("top", "bottom"), pair("left", "right")
        self.horizontal.setText(horizontal)
        self.vertical.setText(vertical)
        self.one.setText(horizontal if horizontal == vertical and "/" not in horizontal else "")
        self._update_preview()

    def sides(self) -> dict[str, tuple[str, float | None]]:
        """The entered values; raises ValueError with a user-facing message."""
        mode = self.mode()
        if mode == MODE_ONE:
            text = self.one.text().strip()
            return perforation_mod.sides_from_notation(text) if text else {}
        if mode == MODE_HV:
            h, v = self.horizontal.text().strip(), self.vertical.text().strip()
            if not h and not v:
                return {}
            if not h or not v:
                raise ValueError("enter both the horizontal and the vertical gauge")
            return perforation_mod.sides_from_notation(f"{h} x {v}")
        return {
            side: perforation_mod.parse_side_value(edit.text())
            for side, edit in self.side_edits.items()
            if edit.text().strip()
        }

    def _update_preview(self) -> None:
        try:
            sides = self.sides()
        except ValueError as exc:
            self.preview.setText(f"<span style='color:{theme.DANGER}'>{exc}</span>")
            self.apply_button.setEnabled(False)
            return
        self.apply_button.setEnabled(True)
        if not sides:
            self.preview.setText("Empty: the measured values are used.")
            return
        shown = {
            name: PerforationSide(
                kind=kind,
                gauge=gauge,
                notation=perforation_mod.notation_for_gauge(gauge) if gauge else None,
            )
            for name, (kind, gauge) in sides.items()
        }
        notation = perforation_mod.perforation_result_notation(shown)
        self.preview.setText(f"Sets {perforation_mod.pretty_notation(notation)}")

    def _apply(self) -> None:
        try:
            sides = self.sides()
        except ValueError:
            return  # the preview already says what is wrong
        self.apply_requested.emit(sides)
