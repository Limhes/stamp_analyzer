"""The project comparison workspace (SPEC §9/§10), shown in the main area
while a project is selected.

One row per measured colour, with colour (Lab, LCh), perforation, catalogue
numbers and cancellation. Filter and sort the table, and the plots show
exactly the rows that pass the filters and are ticked. The plots and the
table are linked: hovering a point names it, clicking a point selects its
row, selecting rows highlights their points, and points can be coloured by
perforation, cancellation type or sheet to compare groups.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QBrush, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSpinBox,
    QSplitter,
    QStyledItemDelegate,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from stampan.core import overview as overview_mod
from stampan.core import perforation as perforation_mod
from stampan.core import project as project_mod
from stampan.core import workflow
from stampan.core.models import cancellation_date_text
from stampan.core.overview import OverviewEntry
from stampan.gui import icons, preferences, theme
from stampan.gui.widgets import Banner, EmptyState, FlowLayout, button, label

SORT_ROLE = Qt.ItemDataRole.UserRole + 1

# columns
C_USE, C_COLOUR, C_STAMP, C_SHEET, C_CAT, C_PERF, C_CTYPE, C_CDATE, C_NAME = range(9)
C_L, C_A, C_B, C_C, C_H = range(9, 14)
_HEADERS = [
    "", "Colour", "Stamp", "Sheet", "Catalogue", "Perforation", "Cancellation", "Date",
    "Philatelic name", "L*", "a*", "b*", "C*", "h°",
]  # fmt: skip
_NUMERIC = {C_L: ("lab", 0), C_A: ("lab", 1), C_B: ("lab", 2), C_C: ("lch", 1), C_H: ("lch", 2)}

KIND_INKS, KIND_PAPER, KIND_ALL = "inks", "paper", "all"
COLOUR_BY_SWATCH = "swatch"
_GROUPINGS = {
    COLOUR_BY_SWATCH: "Measured colour",
    "perforation": "Perforation",
    "cancellation": "Cancellation type",
    "sheet": "Sheet",
    "catalogue": "Catalogue no.",
}
# Distinct, colour-blind-friendly categorical palette (Okabe-Ito + extras).
_PALETTE = [
    "#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442",
    "#000000", "#999999", "#882255", "#44AA99", "#117733",
]  # fmt: skip


def _date_text(e: OverviewEntry) -> str:
    return cancellation_date_text(e.stamp.cancellation)


#: Columns edited in place. Each edit is saved at once through the same
#: workflow function the stamp page uses, so validation and clean-up are
#: identical wherever a value is typed.
EDITABLE = {C_COLOUR, C_CAT, C_PERF, C_CTYPE, C_CDATE}
_EDIT_HINTS = {
    C_CAT: "Catalogue numbers, comma-separated. Double-click to edit.",
    C_PERF: "Double-click to set by hand: 11, 11½ × 12, 11/12 × imperf. "
    "Empty: use the measured values.",
    C_CTYPE: "Double-click to choose the cancellation type.",
    C_CDATE: "YYYY, YYYY-MM or YYYY-MM-DD; “c. 1889” for an estimate. Double-click to edit.",
    C_COLOUR: "The name shown in plots and the published overview. Double-click to rename.",
}


class OverviewModel(QAbstractTableModel):
    edited = Signal()  # a value was saved to the library
    edit_failed = Signal(str)  # the user-facing reason an edit was refused

    def __init__(self, library_root_getter, parent=None) -> None:
        super().__init__(parent)
        self._root = library_root_getter
        self.project_id: str | None = None
        self.entries: list[OverviewEntry] = []

    def set_entries(self, project_id: str, entries: list[OverviewEntry]) -> None:
        self.beginResetModel()
        self.project_id = project_id
        self.entries = entries
        self.endResetModel()

    def rowCount(self, parent=None) -> int:  # noqa: N802
        return 0 if parent is not None and parent.isValid() else len(self.entries)

    def columnCount(self, parent=None) -> int:  # noqa: N802
        return len(_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal:
            if role == Qt.ItemDataRole.DisplayRole:
                return _HEADERS[section]
            if role == Qt.ItemDataRole.ToolTipRole:
                if section == C_USE:
                    return "Ticked colours are plotted and published"
                return _EDIT_HINTS.get(section)
        return None

    def flags(self, index):
        base = super().flags(index)
        if index.column() == C_USE:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        if index.column() in EDITABLE:
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def _edit_text(self, e: OverviewEntry, col: int) -> str:
        return {
            C_COLOUR: e.label,
            C_CAT: e.catalogue,
            C_PERF: perforation_mod.pretty_notation(e.perforation),
            C_CTYPE: e.cancellation_type,
            C_CDATE: _date_text(e),
        }[col]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        e = self.entries[index.row()]
        col = index.column()
        c = e.cluster
        if role == Qt.ItemDataRole.CheckStateRole and col == C_USE:
            return Qt.CheckState.Checked if c.overview_included else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DecorationRole and col == C_COLOUR and c.srgb_swatch:
            return QColor(*c.srgb_swatch)
        if role == Qt.ItemDataRole.EditRole and col in EDITABLE:
            return self._edit_text(e, col)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col in _NUMERIC:
                attr, i = _NUMERIC[col]
                return f"{getattr(e, attr)[i]:.1f}"
            if col == C_PERF:
                return self._edit_text(e, col) + ("  ✎" if e.perforation_manual else "")
            if col in EDITABLE:
                return self._edit_text(e, col)
            return {
                C_STAMP: e.stamp_label,
                C_SHEET: e.sheet,
                C_NAME: c.philatelic_name or "",
            }.get(col)
        if role == SORT_ROLE:
            if col in _NUMERIC:
                attr, i = _NUMERIC[col]
                return getattr(e, attr)[i]
            if col == C_STAMP:
                return (e.sample_id, e.stamp_id)
            if col == C_CDATE:
                return e.cancellation_date or "9999"  # undated last
            if col == C_USE:
                return int(c.overview_included)
            return str(self.data(index) or "").lower()
        if role == Qt.ItemDataRole.ToolTipRole:
            if col == C_PERF and e.perforation_manual:
                return "Set by hand. " + _EDIT_HINTS[C_PERF]
            if col == C_CDATE and e.cancellation_estimated:
                return "Estimated date (not read from a legible postmark). " + _EDIT_HINTS[C_CDATE]
            if col == C_COLOUR:
                return f"{e.stamp_id} — {_EDIT_HINTS[C_COLOUR]}"
            return _EDIT_HINTS.get(col)
        if role == Qt.ItemDataRole.TextAlignmentRole and col in _NUMERIC:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and not c.overview_included:
            return QBrush(QColor(theme.TEXT_FAINT))
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        e = self.entries[index.row()]
        col = index.column()
        root, project_id = self._root(), self.project_id
        if col == C_USE and role == Qt.ItemDataRole.CheckStateRole:
            included = Qt.CheckState(value) == Qt.CheckState.Checked
            return self._save(
                lambda: workflow.set_colour_overview(
                    root, project_id, e.stamp_id, e.cluster.label, included=included
                )
            )
        if role != Qt.ItemDataRole.EditRole or col not in EDITABLE:
            return False
        text = "" if value is None else str(value).strip()
        if text == self._edit_text(e, col).strip():
            return False  # unchanged: e.g. a measured perforation must not become "by hand"
        if col == C_COLOUR:
            label = None if text in ("", e.default_label) else text
            save = lambda: workflow.set_colour_overview(  # noqa: E731
                root, project_id, e.stamp_id, e.cluster.label, display_label=label
            )
        elif col == C_PERF:
            save = lambda: workflow.set_perforation_text(root, project_id, e.stamp_id, text)  # noqa: E731
        else:
            field = {C_CAT: "catalogue", C_CTYPE: "cancellation_type", C_CDATE: "cancellation_date"}
            save = lambda: workflow.update_stamp_details(  # noqa: E731
                root, project_id, e.stamp_id, **{field[col]: text}
            )
        return self._save(save)

    def _save(self, save) -> bool:
        try:
            stamp = save()
        except workflow.WorkflowError as exc:
            self.edit_failed.emit(str(exc))
            return False
        self._replace_stamp(stamp)
        self.edited.emit()
        return True

    def _replace_stamp(self, stamp) -> None:
        """Rebuild the rows of a stamp just saved, from the saved record."""
        rows = [i for i, e in enumerate(self.entries) if e.stamp_id == stamp.stamp_id]
        first = self.entries[rows[0]]
        fresh = overview_mod.stamp_entries(stamp, first.sample_id, first.sheet)
        if len(fresh) != len(rows):  # cannot happen through these edits; be safe
            self.set_entries(self.project_id, [*self.entries])
            return
        for row, entry in zip(rows, fresh, strict=True):
            self.entries[row] = entry
            self.dataChanged.emit(self.index(row, 0), self.index(row, len(_HEADERS) - 1))


class _CancellationTypeDelegate(QStyledItemDelegate):
    """A drop-down of the cancellation types from the preferences."""

    def createEditor(self, parent, option, index):  # noqa: N802
        combo = QComboBox(parent)
        combo.addItem("—  not recorded", "")
        current = index.data(Qt.ItemDataRole.EditRole) or ""
        types = preferences.cancellation_types()
        if current and current not in types:
            types.append(current)  # recorded before the list changed: keep it
        for text in types:
            combo.addItem(text, text)
        combo.setCurrentIndex(max(0, combo.findData(current)))
        # One click chooses: commit as soon as a type is picked.
        combo.activated.connect(lambda _i: self.commitData.emit(combo))
        combo.activated.connect(lambda _i: self.closeEditor.emit(combo))
        return combo

    def setEditorData(self, editor, index) -> None:  # noqa: N802
        editor.showPopup()

    def setModelData(self, editor, model, index) -> None:  # noqa: N802
        model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)


class OverviewFilter(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.text = ""
        self.kind = KIND_INKS
        self.perforation: tuple[str, str] | None = None  # ("any"|"all"|"exact", value)
        self.cancellation: str | None = None
        self.years: tuple[int, int] | None = None
        self.include_undated = True

    def entry(self, source_row: int) -> OverviewEntry:
        return self.sourceModel().entries[source_row]

    def filterAcceptsRow(self, row: int, parent) -> bool:  # noqa: N802
        e = self.entry(row)
        if self.kind == KIND_INKS and e.is_paper or self.kind == KIND_PAPER and not e.is_paper:
            return False
        if self.perforation is not None:
            mode, value = self.perforation
            sides = list(e.side_gauges.values())
            if mode == "exact" and e.perforation != value:
                return False
            if mode == "any" and value not in sides:
                return False
            if mode == "all" and (len(sides) < 4 or any(v != value for v in sides)):
                return False
        if self.cancellation is not None and e.cancellation_type != self.cancellation:
            return False
        if self.years is not None:
            if e.year is None:
                if not self.include_undated:
                    return False
            elif not self.years[0] <= e.year <= self.years[1]:
                return False
        if self.text:
            haystack = " ".join(
                (e.label, e.stamp_label, e.stamp_id, e.sheet, e.catalogue, e.country_issue,
                 e.perforation, e.cancellation_type, e.cancellation_date,
                 e.cluster.philatelic_name or "")
            ).lower()  # fmt: skip
            if not all(word in haystack for word in self.text.lower().split()):
                return False
        return True

    def lessThan(self, left, right) -> bool:  # noqa: N802
        a, b = left.data(SORT_ROLE), right.data(SORT_ROLE)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class _Scatter(pg.PlotWidget):
    """One linked scatter plot of the visible rows."""

    point_clicked = Signal(int, bool)  # source row, add to selection

    def __init__(self, title: str, x_label: str, y_label: str, x_of, y_of, equal_aspect=False):
        super().__init__(background=theme.SURFACE)
        self._x_of, self._y_of = x_of, y_of
        item = self.getPlotItem()
        item.setTitle(f"<span style='color:{theme.TEXT}; font-size:10pt'>{title}</span>")
        for axis, text in (("bottom", x_label), ("left", y_label)):
            ax = item.getAxis(axis)
            ax.setLabel(text, color=theme.TEXT_MUTED)
            ax.setPen(pg.mkPen(theme.BORDER_STRONG))
            ax.setTextPen(pg.mkPen(theme.TEXT_MUTED))
        item.showGrid(x=True, y=True, alpha=0.15)
        item.setMenuEnabled(False)
        if equal_aspect:
            self.setAspectLocked(True)
            for pos, angle in ((0, 0), (0, 90)):
                # The neutral axes for orientation, without forcing the view to
                # include the origin (autorange stays on the data).
                line = pg.InfiniteLine(pos, angle, pen=pg.mkPen(theme.BORDER_STRONG, width=1))
                item.addItem(line, ignoreBounds=True)
        self.scatter = pg.ScatterPlotItem(hoverable=True, tip=None)
        self.highlight = pg.ScatterPlotItem()
        item.addItem(self.scatter)
        item.addItem(self.highlight)
        self.legend = item.addLegend(offset=(8, 8), labelTextColor=theme.TEXT, brush="#ffffffd0")
        self._legend_items: list = []
        self.scatter.sigClicked.connect(self._on_click)
        self.scatter.sigHovered.connect(self._on_hover)
        self._tooltip_of = None

    def show_points(self, entries, rows, brushes, selected, groups, tooltip_of) -> None:
        self._tooltip_of = tooltip_of
        xs = np.array([self._x_of(entries[r]) for r in rows], dtype=float)
        ys = np.array([self._y_of(entries[r]) for r in rows], dtype=float)
        self.scatter.setData(
            x=xs, y=ys, data=list(rows), size=11, brush=brushes,
            pen=pg.mkPen("#1c2230", width=0.8),
        )  # fmt: skip
        sel = [i for i, r in enumerate(rows) if r in selected]
        self.highlight.setData(
            x=xs[sel], y=ys[sel], size=19, brush=None, pen=pg.mkPen(theme.ACCENT, width=2.5)
        )
        for item in self._legend_items:
            self.legend.removeItem(item)
        self._legend_items = []
        for name, colour in groups:
            proxy = pg.ScatterPlotItem(size=10, brush=pg.mkBrush(colour), pen=pg.mkPen("#1c2230"))
            self.legend.addItem(proxy, name)
            self._legend_items.append(proxy)
        self.legend.setVisible(bool(groups))
        self.getPlotItem().enableAutoRange()

    def _on_click(self, _item, points, event) -> None:
        if len(points):
            add = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self.point_clicked.emit(int(points[0].data()), add)

    def _on_hover(self, _item, points, _event) -> None:
        if len(points) and self._tooltip_of is not None:
            self.setToolTip("<br>".join(self._tooltip_of(int(p.data())) for p in points[:6]))
        else:
            self.setToolTip("")


class OverviewWidget(QWidget):
    """Filter bar, linked plots and the table."""

    #: Open a stamp (project_id, stamp_id) -- double-clicked row.
    open_stamp = Signal(str, str)
    saved = Signal()

    def __init__(self, library_root_getter, parent=None) -> None:
        super().__init__(parent)
        self._root = library_root_getter
        self.project_id: str | None = None
        self._replot_timer = QTimer(self, singleShot=True, interval=30)
        self._replot_timer.timeout.connect(self._replot)

        self.model = OverviewModel(library_root_getter, self)
        self.proxy = OverviewFilter(self)
        self.proxy.setSourceModel(self.model)
        self.model.edited.connect(self.saved)
        self.model.edited.connect(lambda: self.banner.show_message(""))
        self.model.edit_failed.connect(lambda message: self.banner.show_message(message, "error"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._filter_bar())

        self.empty = EmptyState(
            "palette",
            "No colours measured yet",
            "Open a stamp, go to its Colour tab and mark an area of ink or paper. "
            "Every measured colour of the project appears here for comparison.",
        )
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        plots = QWidget()
        plots_layout = QHBoxLayout(plots)
        plots_layout.setContentsMargins(8, 8, 8, 0)
        self.ab_plot = _Scatter(
            "CIELAB a*b*", "a*", "b*",
            lambda e: e.lab[1], lambda e: e.lab[2], equal_aspect=True,
        )  # fmt: skip
        self.lc_plot = _Scatter("L* vs C*", "C*", "L*", lambda e: e.lch[1], lambda e: e.lch[0])
        for plot in (self.ab_plot, self.lc_plot):
            plot.point_clicked.connect(self._select_from_plot)
            plots_layout.addWidget(plot)
        self.splitter.addWidget(plots)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(C_STAMP, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(C_COLOUR, QHeaderView.ResizeMode.Stretch)
        for col, width in (
            (C_USE, 30),
            (C_STAMP, 80),
            (C_SHEET, 110),
            (C_CAT, 90),
            (C_PERF, 150),
            (C_CTYPE, 150),
            (C_CDATE, 95),
            (C_NAME, 130),
        ):
            self.table.setColumnWidth(col, width)  # fmt: skip
        for col in _NUMERIC:
            self.table.setColumnWidth(col, 52)
        self.table.setItemDelegateForColumn(C_CTYPE, _CancellationTypeDelegate(self.table))
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        copy = QShortcut(QKeySequence.StandardKey.Copy, self.table, self.copy_selected)
        copy.setContext(Qt.ShortcutContext.WidgetShortcut)
        self.table.selectionModel().selectionChanged.connect(self._schedule_replot)
        self.proxy.layoutChanged.connect(self._schedule_replot)
        self.proxy.rowsInserted.connect(self._schedule_replot)
        self.proxy.rowsRemoved.connect(self._schedule_replot)
        self.proxy.modelReset.connect(self._schedule_replot)
        self.proxy.dataChanged.connect(self._schedule_replot)

        table_box = QWidget()
        table_layout = QVBoxLayout(table_box)
        table_layout.setContentsMargins(8, 4, 8, 8)
        table_layout.addWidget(self.table)
        hint = label(
            "Double-click Colour, Catalogue, Perforation, Cancellation or Date to edit it in "
            "place; elsewhere it opens the stamp. Click a point to select its row (Ctrl+click "
            "adds). Untick a colour to leave it out of the plots and the published overview. "
            "Ctrl+C copies the selected rows for a spreadsheet; right-click to export.",
            "caption",
            wrap=True,
        )
        table_layout.addWidget(hint)
        self.splitter.addWidget(table_box)
        self.splitter.setSizes([420, 380])
        layout.addWidget(self.splitter, stretch=1)
        self.banner = Banner()
        layout.addWidget(self.banner)
        layout.addWidget(self.empty, stretch=1)

    # -- filter bar --------------------------------------------------------------

    def _filter_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("FilterBar")
        bar.setStyleSheet(
            f"#FilterBar {{ background: {theme.SURFACE}; "
            f"border-bottom: 1px solid {theme.BORDER}; }}"
        )
        # A wrapping layout: on a narrow window the controls flow onto more
        # lines instead of forcing the side panels narrower.
        flow = FlowLayout(bar)
        flow.setContentsMargins(10, 6, 10, 6)

        def group(*widgets) -> QWidget:
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            for w in widgets:
                row.addWidget(w)
            flow.addWidget(box)
            return box

        def combo(width: int) -> QComboBox:
            c = QComboBox()
            c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            c.setFixedWidth(width)
            return c

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search stamps, catalogue no., names…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("zoom-in"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._apply_filters)
        group(self.search)

        self.kind_combo = combo(110)
        for key, text in ((KIND_INKS, "Inks"), (KIND_PAPER, "Paper"), (KIND_ALL, "All colours")):
            self.kind_combo.addItem(text, key)
        self.perf_combo = combo(170)
        self.cancel_combo = combo(170)
        for c in (self.kind_combo, self.perf_combo, self.cancel_combo):
            c.currentIndexChanged.connect(self._apply_filters)
        group(self.kind_combo)
        group(label("Perf."), self.perf_combo)
        group(label("Cancel"), self.cancel_combo)

        self.year_from, self.year_to = QSpinBox(), QSpinBox()
        for spin, special in ((self.year_from, "from"), (self.year_to, "to")):
            spin.setRange(1839, 2100)
            spin.setSpecialValueText(special)
            spin.setValue(1839)
            spin.setFixedWidth(82)
            spin.valueChanged.connect(self._apply_filters)
        group(label("Years"), self.year_from, self.year_to)

        self.group_combo = combo(150)
        for key, text in _GROUPINGS.items():
            self.group_combo.addItem(text, key)
        self.group_combo.currentIndexChanged.connect(self._schedule_replot)
        group(label("Colour by"), self.group_combo)
        group(
            button("", "refresh", "flat", "Clear all filters", self.reset_filters),
            self._export_button(),
        )
        self.count_label = label("", "caption")
        flow.addWidget(self.count_label)
        return bar

    def reset_filters(self) -> None:
        for widget in (
            self.search,
            self.kind_combo,
            self.perf_combo,
            self.cancel_combo,
            self.year_from,
            self.year_to,
        ):
            widget.blockSignals(True)  # fmt: skip
        self.search.clear()
        self.kind_combo.setCurrentIndex(0)
        self.perf_combo.setCurrentIndex(0)
        self.cancel_combo.setCurrentIndex(0)
        self.year_from.setValue(self.year_from.minimum())
        self.year_to.setValue(self.year_to.minimum())
        for widget in (
            self.search,
            self.kind_combo,
            self.perf_combo,
            self.cancel_combo,
            self.year_from,
            self.year_to,
        ):
            widget.blockSignals(False)  # fmt: skip
        self._apply_filters()

    def _fill_choice_combos(self) -> None:
        entries = self.model.entries
        perf, cancel = self.perf_combo.currentData(), self.cancel_combo.currentData()
        self.perf_combo.blockSignals(True)
        self.perf_combo.clear()
        self.perf_combo.addItem("Any", None)
        gauges = overview_mod.gauge_choices(entries)
        for value in gauges:
            self.perf_combo.addItem(f"{value} on all sides", ("all", value))
        for value in gauges:
            self.perf_combo.addItem(f"{value} on any side", ("any", value))
        for value in sorted({e.perforation for e in entries if e.perforation}):
            self.perf_combo.addItem(value.replace(" x ", " × "), ("exact", value))
        self.perf_combo.setCurrentIndex(max(0, self.perf_combo.findData(perf)))
        self.perf_combo.blockSignals(False)

        self.cancel_combo.blockSignals(True)
        self.cancel_combo.clear()
        self.cancel_combo.addItem("Any", None)
        for value in sorted({e.cancellation_type for e in entries if e.cancellation_type}):
            self.cancel_combo.addItem(value, value)
        self.cancel_combo.setCurrentIndex(max(0, self.cancel_combo.findData(cancel)))
        self.cancel_combo.blockSignals(False)

    def _apply_filters(self) -> None:
        p = self.proxy
        p.text = self.search.text().strip()
        p.kind = self.kind_combo.currentData()
        p.perforation = self.perf_combo.currentData()
        p.cancellation = self.cancel_combo.currentData()
        lo = self.year_from.value() if self.year_from.value() > self.year_from.minimum() else None
        hi = self.year_to.value() if self.year_to.value() > self.year_to.minimum() else None
        if lo is None and hi is None:
            p.years = None
        else:
            p.years = (lo or 0, hi or 9999)
            p.include_undated = False
        p.invalidateFilter()
        self._schedule_replot()

    # -- loading and saving ----------------------------------------------------

    def set_project(self, project_id: str) -> None:
        self.project_id = project_id
        project = project_mod.load_project(self._root(), project_id)
        self.model.set_entries(project_id, overview_mod.project_entries(self._root(), project))
        self._fill_choice_combos()
        self._apply_filters()
        self.banner.show_message("")
        has = bool(self.model.entries)
        self.splitter.setVisible(has)
        self.empty.setVisible(not has)

    # -- plots -----------------------------------------------------------------

    def _visible_rows(self) -> list[int]:
        rows = []
        for r in range(self.proxy.rowCount()):
            source = self.proxy.mapToSource(self.proxy.index(r, 0)).row()
            if self.model.entries[source].cluster.overview_included:
                rows.append(source)
        return rows

    def _selected_rows(self) -> set[int]:
        return {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}

    def _schedule_replot(self, *_args) -> None:
        self._replot_timer.start()

    def _group_of(self, e: OverviewEntry, key: str) -> str:
        return {
            "perforation": e.perforation.replace(" x ", " × ") or "not measured",
            "cancellation": e.cancellation_type or "not recorded",
            "sheet": e.sheet,
            "catalogue": e.catalogue or "no catalogue no.",
        }[key]

    def _replot(self) -> None:
        entries = self.model.entries
        rows = self._visible_rows()
        total = sum(
            1 for r in range(self.model.rowCount()) if self.proxy.filterAcceptsRow(r, QModelIndex())
        )
        self.count_label.setText(f"{len(rows)} plotted · {total} of {len(entries)} shown")
        key = self.group_combo.currentData()
        groups: list[tuple[str, str]] = []
        if key == COLOUR_BY_SWATCH:
            brushes = [
                pg.mkBrush(*(entries[r].cluster.srgb_swatch or (128, 128, 128))) for r in rows
            ]
        else:
            names = sorted({self._group_of(entries[r], key) for r in rows})
            colours = {n: _PALETTE[i % len(_PALETTE)] for i, n in enumerate(names)}
            brushes = [pg.mkBrush(colours[self._group_of(entries[r], key)]) for r in rows]
            groups = list(colours.items())
        selected = self._selected_rows()

        def tooltip(r: int) -> str:
            e = entries[r]
            parts = [f"<b>{e.label}</b>", e.cluster.philatelic_name or ""]
            if e.perforation:
                parts.append(f"perf. {e.perforation.replace(' x ', ' × ')}")
            if e.cancellation_date or e.cancellation_type:
                parts.append(f"{e.cancellation_type} {_date_text(e)}".strip())
            parts.append(f"L* {e.lch[0]:.1f}  C* {e.lch[1]:.1f}  h {e.lch[2]:.1f}°")
            return " · ".join(p for p in parts if p)

        for plot in (self.ab_plot, self.lc_plot):
            plot.show_points(entries, rows, brushes, selected, groups, tooltip)

    def _select_from_plot(self, source_row: int, add: bool) -> None:
        index = self.proxy.mapFromSource(self.model.index(source_row, 0))
        if not index.isValid():
            return
        flags = QItemSelectionModel.SelectionFlag.Rows | (
            QItemSelectionModel.SelectionFlag.Toggle
            if add
            else QItemSelectionModel.SelectionFlag.ClearAndSelect
        )
        self.table.selectionModel().select(index, flags)
        self.table.scrollTo(index)

    def _on_double_click(self, index) -> None:
        if index.column() not in EDITABLE:  # double-clicking an editable cell edits it
            self._open_row(index)

    def _open_row(self, index) -> None:
        if not index.isValid() or self.project_id is None:
            return
        e = self.model.entries[self.proxy.mapToSource(index).row()]
        self.open_stamp.emit(self.project_id, e.stamp_id)

    # -- export and clipboard ------------------------------------------------------

    def _export_button(self) -> QToolButton:
        tool = QToolButton()
        tool.setIcon(icons.icon("import"))
        tool.setText("Export")
        tool.setToolTip("Export the table or copy it to the clipboard")
        tool.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tool.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tool.setAutoRaise(True)
        tool.setMenu(self._export_menu(tool))
        return tool

    def _export_menu(self, parent) -> QMenu:
        menu = QMenu(parent)
        menu.addAction(icons.icon("details"), "Copy Selected Rows", self.copy_selected)
        menu.addAction(icons.icon("details"), "Copy All Visible Rows", self.copy_visible)
        menu.addSeparator()
        menu.addAction(
            icons.icon("report"), "Export to Excel (.xlsx)…", lambda: self.export("xlsx")
        )
        menu.addAction(icons.icon("report"), "Export to CSV…", lambda: self.export("csv"))
        return menu

    def _table_menu(self, pos) -> None:
        menu = self._export_menu(self)
        has_selection = bool(self.table.selectionModel().selectedRows())
        menu.actions()[0].setEnabled(has_selection)
        menu.insertSeparator(menu.actions()[0])
        open_action = QAction(icons.icon("chevron-right"), "Open Stamp", menu)
        index = self.table.indexAt(pos)
        open_action.setEnabled(index.isValid())
        open_action.triggered.connect(lambda: self._open_row(index))
        menu.insertAction(menu.actions()[0], open_action)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _rows_visible(self) -> list[OverviewEntry]:
        """Every row that passes the filters, in the table's sort order."""
        return [
            self.model.entries[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
            for r in range(self.proxy.rowCount())
        ]

    def _rows_selected(self) -> list[OverviewEntry]:
        rows = sorted(i.row() for i in self.table.selectionModel().selectedRows())
        return [self.model.entries[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
                for r in rows]  # fmt: skip

    def _copy(self, entries: list[OverviewEntry], what: str) -> None:
        if not entries:
            return
        mime = QMimeData()
        mime.setText(overview_mod.to_tsv(entries))
        mime.setHtml(overview_mod.to_html(entries))
        QGuiApplication.clipboard().setMimeData(mime)
        self.count_label.setText(f"Copied {len(entries)} {what} row(s) — paste into a spreadsheet")
        self._replot_timer.start(2500)  # restore the counter afterwards

    def copy_selected(self) -> None:
        self._copy(self._rows_selected() or self._rows_visible(), "selected")

    def copy_visible(self) -> None:
        self._copy(self._rows_visible(), "visible")

    def export(self, kind: str) -> None:
        entries = self._rows_visible()
        if not entries:
            return
        project = project_mod.load_project(self._root(), self.project_id)
        filters = {"xlsx": "Excel workbook (*.xlsx)", "csv": "CSV, comma-separated (*.csv)"}
        start = str(Path.home() / f"{project.name} – stamps.{kind}")
        path, _ = QFileDialog.getSaveFileName(self, "Export Table", start, filters[kind])
        if not path:
            return
        if not path.lower().endswith(f".{kind}"):
            path += f".{kind}"
        try:
            if kind == "xlsx":
                overview_mod.write_xlsx(path, entries, title=project.name)
            else:
                overview_mod.write_csv(path, entries)
        except OSError as exc:
            QMessageBox.warning(self, "Export Failed", f"Could not write {path}:\n{exc}")
            return
        self.count_label.setText(f"Exported {len(entries)} row(s) to {Path(path).name}")
        self._replot_timer.start(4000)
