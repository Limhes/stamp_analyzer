"""Set a stamp's perforation by hand, side by side with what was measured
(SPEC §6.2). The measured result is kept; the override is stored next to it
and flagged on the report."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
)

from stampan.core import perforation as perforation_mod
from stampan.core.models import PerforationResult
from stampan.gui import icons, theme
from stampan.gui.widgets import button, label

_KINDS = {"perforated": "Perforated", "imperf": "Imperforate", "straight_edge": "Straight edge"}


def _measured_text(side) -> str:
    if side is None:
        return "not measured"
    if side.kind != "perforated":
        return _KINDS.get(side.kind, side.kind).lower()
    if side.gauge is None:
        return "?"
    unc = f" ± {side.uncertainty:.2f}" if side.uncertainty else ""
    return f"{side.gauge:.2f}{unc}  ({side.notation})"


class PerforationOverrideDialog(QDialog):
    def __init__(self, perforation: PerforationResult, stamp_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Set Perforation — {stamp_name}")
        self.setWindowIcon(icons.icon("perforation"))
        self.setMinimumWidth(780)
        layout = QVBoxLayout(self)
        layout.addWidget(
            label(
                "Tick a side to replace its measured value. Gauges are teeth per 2 cm; the "
                "notation is rounded to the nearest quarter. The measured values stay on "
                "record and the report marks the stamp as manually overridden.",
                "caption",
                wrap=True,
            )
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        for col, text in enumerate(("Side", "Measured", "Override", "Kind", "Gauge", "")):
            grid.addWidget(label(text, "section"), 0, col)
        self._rows = {}
        for row, side in enumerate(perforation_mod.SIDES, start=1):
            measured = perforation.auto.get(side)
            manual = perforation.manual.get(side)
            check = QCheckBox()
            kind = QComboBox()
            for key, text in _KINDS.items():
                kind.addItem(text, key)
            gauge = QDoubleSpinBox()
            gauge.setRange(perforation_mod.GAUGE_MIN, 30.0)
            gauge.setDecimals(2)
            gauge.setSingleStep(0.25)
            notation = label("", "mono")
            start = manual or measured
            kind.setCurrentIndex(max(0, kind.findData(start.kind if start else "perforated")))
            gauge.setValue(start.gauge if start and start.gauge else 12.0)
            check.setChecked(manual is not None)

            def sync(*_a, check=check, kind=kind, gauge=gauge, notation=notation) -> None:
                on = check.isChecked()
                kind.setEnabled(on)
                perforated = kind.currentData() == "perforated"
                gauge.setEnabled(on and perforated)
                notation.setText(
                    perforation_mod.notation_for_gauge(gauge.value()) if perforated else ""
                )

            for signal in (check.toggled, kind.currentIndexChanged, gauge.valueChanged):
                signal.connect(sync)
            sync()
            grid.addWidget(label(side.capitalize()), row, 0)
            grid.addWidget(label(_measured_text(measured), "mono"), row, 1)
            grid.addWidget(check, row, 2)
            grid.addWidget(kind, row, 3)
            grid.addWidget(gauge, row, 4)
            grid.addWidget(notation, row, 5)
            self._rows[side] = (check, kind, gauge)
        layout.addLayout(grid)

        # Shortcut: the common case of one gauge all round.
        all_row = QHBoxLayout()
        all_row.addWidget(label("All four sides perforated"))
        self.all_gauge = QDoubleSpinBox()
        self.all_gauge.setRange(perforation_mod.GAUGE_MIN, 30.0)
        self.all_gauge.setDecimals(2)
        self.all_gauge.setSingleStep(0.25)
        measured = [s.gauge for s in perforation.effective().values() if s.gauge]
        self.all_gauge.setValue(round(sum(measured) / len(measured) * 4) / 4 if measured else 12.0)
        self.all_gauge.setToolTip("Type a gauge and press Enter or Apply")
        all_row.addWidget(self.all_gauge)
        all_row.addWidget(button("Apply to All", "check", slot=self._apply_all))
        all_row.addStretch(1)
        layout.addLayout(all_row)

        self.reason_edit = QLineEdit(next(iter(perforation.manual_reason.values()), ""))
        self.reason_edit.setPlaceholderText("Reason (optional), e.g. “checked with Instanta gauge”")
        layout.addWidget(self.reason_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        theme.set_role(buttons.button(QDialogButtonBox.StandardButton.Save), "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def keyPressEvent(self, event) -> None:  # noqa: N802 -- Qt override
        # Enter in the all-sides field applies it instead of saving the dialog.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.all_gauge.hasFocus():
            self._apply_all()
            return
        super().keyPressEvent(event)

    def _apply_all(self) -> None:
        for check, kind, gauge in self._rows.values():
            check.setChecked(True)
            kind.setCurrentIndex(kind.findData("perforated"))
            gauge.setValue(self.all_gauge.value())

    def overrides(self) -> dict[str, tuple[str, float | None]]:
        result = {}
        for side, (check, kind, gauge) in self._rows.items():
            if check.isChecked():
                k = kind.currentData()
                result[side] = (k, gauge.value() if k == "perforated" else None)
        return result

    def reason(self) -> str:
        return self.reason_edit.text().strip()
