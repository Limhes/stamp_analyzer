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

import csv
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import (
    QAbstractTableModel,
    QItemSelectionModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QSpinBox,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from stampan.core import overview as overview_mod
from stampan.core import project as project_mod
from stampan.core.overview import OverviewEntry
from stampan.gui import icons, theme
from stampan.gui.widgets import EmptyState, FlowLayout, button, label

SAVE_DEBOUNCE_MS = 400
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
    if not e.cancellation_date:
        return ""
    return f"c. {e.cancellation_date}" if e.cancellation_estimated else e.cancellation_date


class OverviewModel(QAbstractTableModel):
    edited = Signal(str)  # stamp_id whose label or tick changed (to be saved)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.entries: list[OverviewEntry] = []

    def set_entries(self, entries: list[OverviewEntry]) -> None:
        self.beginResetModel()
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
            if role == Qt.ItemDataRole.ToolTipRole and section == C_USE:
                return "Ticked colours are plotted and go into the report"
        return None

    def flags(self, index):
        base = super().flags(index)
        if index.column() == C_USE:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        if index.column() == C_COLOUR:
            return base | Qt.ItemFlag.ItemIsEditable
        return base

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        e = self.entries[index.row()]
        col = index.column()
        c = e.cluster
        if role == Qt.ItemDataRole.CheckStateRole and col == C_USE:
            return Qt.CheckState.Checked if c.overview_included else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DecorationRole and col == C_COLOUR and c.srgb_swatch:
            return QColor(*c.srgb_swatch)
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            if col in _NUMERIC:
                attr, i = _NUMERIC[col]
                return f"{getattr(e, attr)[i]:.1f}"
            return {
                C_COLOUR: e.label,
                C_STAMP: e.stamp_label,
                C_SHEET: e.sheet,
                C_CAT: e.catalogue,
                C_PERF: e.perforation.replace(" x ", " × ")
                + ("  ✎" if e.perforation_manual else ""),
                C_CTYPE: e.cancellation_type,
                C_CDATE: _date_text(e),
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
                return "Perforation set by hand"
            if col == C_CDATE and e.cancellation_estimated:
                return "Estimated date (not read from a legible postmark)"
            if col == C_COLOUR:
                return f"{e.stamp_id} — double-click to rename"
        if role == Qt.ItemDataRole.TextAlignmentRole and col in _NUMERIC:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and not c.overview_included:
            return QBrush(QColor(theme.TEXT_FAINT))
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        e = self.entries[index.row()]
        if index.column() == C_USE and role == Qt.ItemDataRole.CheckStateRole:
            e.cluster.overview_included = Qt.CheckState(value) == Qt.CheckState.Checked
        elif index.column() == C_COLOUR and role == Qt.ItemDataRole.EditRole:
            text = str(value).strip()
            e.cluster.overview_label = None if text in ("", e.default_label) else text
        else:
            return False
        row = index.row()
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(_HEADERS) - 1))
        self.edited.emit(e.stamp_id)
        return True


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
        self._dirty: set[str] = set()
        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self.save)
        self._replot_timer = QTimer(self, singleShot=True, interval=30)
        self._replot_timer.timeout.connect(self._replot)

        self.model = OverviewModel(self)
        self.proxy = OverviewFilter(self)
        self.proxy.setSourceModel(self.model)
        self.model.edited.connect(self._on_edited)

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
        self.table.doubleClicked.connect(self._on_double_click)
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
            "Click a point to select its row (Ctrl+click adds); double-click a row to open the "
            "stamp, or its colour name to rename it. Untick a colour to leave it out of the "
            "plots and the report.",
            "caption",
            wrap=True,
        )
        table_layout.addWidget(hint)
        self.splitter.addWidget(table_box)
        self.splitter.setSizes([420, 380])
        layout.addWidget(self.splitter, stretch=1)
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
            button("", "import", "flat", "Export the visible rows as CSV", self._export),
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
        self.save()
        self.project_id = project_id
        project = project_mod.load_project(self._root(), project_id)
        self.model.set_entries(overview_mod.project_entries(self._root(), project))
        self._fill_choice_combos()
        self._apply_filters()
        has = bool(self.model.entries)
        self.splitter.setVisible(has)
        self.empty.setVisible(not has)

    def _on_edited(self, stamp_id: str) -> None:
        self._dirty.add(stamp_id)
        self._save_timer.start()

    def save(self) -> None:
        self._save_timer.stop()
        if not self._dirty or self.project_id is None:
            return
        stamps = {e.stamp_id: e.stamp for e in self.model.entries if e.stamp_id in self._dirty}
        for stamp in stamps.values():
            project_mod.save_stamp_results(self._root(), self.project_id, stamp)
        self._dirty.clear()
        self.saved.emit()

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
        if index.column() == C_COLOUR or self.project_id is None:
            return  # editing the name
        e = self.model.entries[self.proxy.mapToSource(index).row()]
        self.save()
        self.open_stamp.emit(self.project_id, e.stamp_id)

    # -- export ------------------------------------------------------------------

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Overview", "colour_overview.csv", "CSV (*.csv)"
        )
        if not path:
            return
        with Path(path).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["stamp_id", "colour", "sheet", "catalogue", "perforation",
                 "perforation_by_hand", "cancellation", "cancellation_date", "date_estimated",
                 "philatelic_name", "L*", "a*", "b*", "C*", "h", "included"]
            )  # fmt: skip
            for r in range(self.proxy.rowCount()):
                e = self.model.entries[self.proxy.mapToSource(self.proxy.index(r, 0)).row()]
                writer.writerow(
                    [e.stamp_id, e.label, e.sheet, e.catalogue, e.perforation,
                     e.perforation_manual, e.cancellation_type, e.cancellation_date,
                     e.cancellation_estimated, e.cluster.philatelic_name or "",
                     *(f"{v:.2f}" for v in e.lab), f"{e.lch[1]:.2f}", f"{e.lch[2]:.2f}",
                     e.cluster.overview_included]
                )  # fmt: skip
