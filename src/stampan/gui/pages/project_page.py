"""The project page: overview, project-wide actions, and the cross-stamp
colour overview (SPEC §9/§10)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHeaderView,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
)

from stampan.core import colour as colour_mod
from stampan.core import project as project_mod
from stampan.core import workflow
from stampan.core.calibration import reference as ref_mod
from stampan.core.io import read_tiff
from stampan.gui import icons, library_actions
from stampan.gui.dialogs.add_sheet import SOURCE_SCANNER, AddSheetDialog
from stampan.gui.library_tree import KIND_SAMPLE
from stampan.gui.pages.base import AppContext, Page
from stampan.gui.widgets import Banner, ScaledImage, StatCard, button, label

SAVE_DEBOUNCE_MS = 400


class ProjectPage(Page):
    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(ctx, parent=parent)
        self.project_id: str | None = None
        self._project = None
        self._stamps: dict[str, object] = {}
        self._entries: list[tuple[str, object]] = []  # (stamp_id, cluster), one per table row

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_overview_choices)

        self.add_sheet_button = self.header.add_action(
            button(
                "Add Sheet…", "import", "primary", "Scan or import a sample sheet", self.add_sheet
            )
        )
        more = QToolButton()
        more.setIcon(icons.icon("report"))
        more.setText("Report")
        more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        more.setToolTip("Generate the PDF report with CSV/JSON export")
        more.clicked.connect(self.generate_report)
        menu = QMenu(more)
        menu.addAction(icons.icon("report"), "Generate Report", self.generate_report)
        menu.addAction(icons.icon("open-external"), "Open Last Report", self.open_report)
        menu.addSeparator()
        menu.addAction(
            icons.icon("perforation"), "Re-measure Perforation…", self.remeasure_perforation
        )
        menu.addAction(icons.icon("folder"), "Show Project Folder", self.open_folder)
        more.setMenu(menu)
        self.header.add_action(more)
        manage = QToolButton()
        manage.setIcon(icons.icon("edit"))
        manage.setToolTip("Rename or remove this project")
        manage.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        manage_menu = QMenu(manage)
        manage_menu.addAction(icons.icon("edit"), "Rename Project…", self.rename_project)
        manage_menu.addAction(icons.icon("clear"), "Remove Project…", self.remove_project)
        manage.setMenu(manage_menu)
        self.header.add_action(manage)

        self.banner = Banner()
        self.body_layout.addWidget(self.banner)

        stats = QGridLayout()
        stats.setSpacing(8)
        self.sheets_card = StatCard("Sheets")
        self.stamps_card = StatCard("Stamps")
        self.perf_card = StatCard("Perforation")
        self.colour_card = StatCard("Colour")
        for i, card in enumerate(
            (self.sheets_card, self.stamps_card, self.perf_card, self.colour_card)
        ):
            stats.addWidget(card, i // 2, i % 2)
        self.body_layout.addLayout(stats)

        self.body_layout.addWidget(label("Colour overview", "section"))
        self.body_layout.addWidget(
            label(
                "Every ink colour measured across this project. Untick a row to leave it out "
                "of the plot and the report; double-click a label to rename it.",
                "caption",
                wrap=True,
            )
        )
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Label", "Philatelic name", "L*  C*  h°"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(160)
        self.table.itemChanged.connect(self._on_item_changed)
        self.body_layout.addWidget(self.table)
        self.overview_plot = ScaledImage(placeholder="No ink colours measured yet")
        self.body_layout.addWidget(self.overview_plot)
        self.body_layout.addStretch(1)

    # -- loading ---------------------------------------------------------------

    def set_project(self, project_id: str) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_overview_choices()
        self.project_id = project_id
        root = self.ctx.library_root
        self._project = project = project_mod.load_project(root, project_id)
        self.header.set_text(project.name, f"Project · created {project.created}")
        self._stamps, self._entries = {}, []
        n_perf = n_colour = 0
        for sample_id in project.sample_ids:
            sample = project_mod.load_sample(root, project_id, sample_id)
            for stamp_id in sample.stamp_ids:
                stamp = project_mod.load_stamp(root, project_id, stamp_id)
                self._stamps[stamp_id] = stamp
                n_perf += bool(stamp.perforation.auto or stamp.perforation.manual)
                n_colour += bool(stamp.colour.clusters)
                for cluster in stamp.colour.clusters:
                    if cluster.label != "paper" and cluster.mean_lab is not None:
                        self._entries.append((stamp_id, cluster))
        n = len(self._stamps)
        self.sheets_card.set_value(str(len(project.sample_ids)))
        self.stamps_card.set_value(str(n))
        self.perf_card.set_value(f"{n_perf} / {n}", "stamps measured")
        self.colour_card.set_value(f"{n_colour} / {n}", "stamps measured")
        self._populate_table()
        self._replot()

    def show_message(self, text: str, severity: str = "info") -> None:
        self.banner.show_message(text, severity)

    # -- colour overview -------------------------------------------------------

    @staticmethod
    def _default_label(stamp_id: str, cluster) -> str:
        return f"{project_mod.stamp_label(stamp_id)} · {cluster.label}"

    def _populate_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._entries))
        for row, (stamp_id, cluster) in enumerate(self._entries):
            name = QTableWidgetItem(
                cluster.overview_label or self._default_label(stamp_id, cluster)
            )
            name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name.setCheckState(
                Qt.CheckState.Checked if cluster.overview_included else Qt.CheckState.Unchecked
            )
            if cluster.srgb_swatch:
                name.setData(Qt.ItemDataRole.DecorationRole, QColor(*cluster.srgb_swatch))
            name.setToolTip(stamp_id)
            self.table.setItem(row, 0, name)
            for col, text in (
                (1, cluster.philatelic_name or ""),
                (
                    2,
                    "  ".join(f"{v:.1f}" for v in cluster.mean_lch) if cluster.mean_lch else "",
                ),
            ):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.table.blockSignals(False)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or item.row() >= len(self._entries):
            return
        stamp_id, cluster = self._entries[item.row()]
        cluster.overview_included = item.checkState() == Qt.CheckState.Checked
        text = item.text().strip()
        cluster.overview_label = (
            None if text in ("", self._default_label(stamp_id, cluster)) else text
        )
        self._save_timer.start(SAVE_DEBOUNCE_MS)

    def _save_overview_choices(self) -> None:
        root = self.ctx.library_root
        for stamp in self._stamps.values():
            project_mod.save_stamp_results(root, self.project_id, stamp)
        self._replot()

    def _replot(self) -> None:
        entries = [
            (cluster.overview_label or self._default_label(stamp_id, cluster), cluster)
            for stamp_id, cluster in self._entries
            if cluster.overview_included
        ]
        self.overview_plot.set_png(colour_mod.render_colour_overview(entries) if entries else None)

    # -- actions ---------------------------------------------------------------

    def add_sheet(self) -> None:
        if self._project is None:
            return
        dialog = AddSheetDialog(self.ctx.library_root, self._project.name, self.window())
        if not dialog.exec():
            return
        source = dialog.result_source()
        root, project_id = self.ctx.library_root, self.project_id

        def do_add(progress=None):
            warnings: list[str] = []
            if source.source == SOURCE_SCANNER:
                reference = ref_mod.load_reference(root, source.ref_id)
                captured = workflow.capture_scan(
                    workflow.sample_scan_options(reference),
                    device=source.device,
                    ref_id=source.ref_id,
                    progress=progress,
                )
                image, metadata = captured.image, captured.metadata
                warnings += captured.warnings
            else:
                image, metadata = read_tiff(source.path)
            project = project_mod.load_project(root, project_id)
            result = workflow.ingest_sample(
                root, project, source.ref_id, image, metadata, progress=progress
            )
            return result, warnings + result.warnings

        def done(res) -> None:
            result, warnings = res
            text = (
                f"Added {project_mod.sheet_title(result.sample)} with {result.n_stamps} stamp(s)."
            )
            if result.n_flagged:
                text += f" {result.n_flagged} flagged for review."
            if warnings:
                text += "<br>" + "<br>".join(warnings)
            self.changed.emit()
            self.ctx.navigate(KIND_SAMPLE, project_id, result.sample.sample_id)
            self.show_message(text, "warning" if warnings else "success")

        self.runner.run(
            do_add,
            description="Adding sample sheet…",
            on_done=done,
            error_title="Could not add the sheet",
        )

    def remeasure_perforation(self) -> None:
        if self._project is None:
            return
        answer = QMessageBox.question(
            self.window(),
            "Re-measure Perforation",
            "Re-measure the perforation of every stamp in this project?\n\n"
            "Each stamp keeps its own adjusted box and gauge search range. Manual "
            "per-side corrections are left untouched.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        root, project = self.ctx.library_root, self._project

        def done(items) -> None:
            n_ok = sum(1 for i in items if i.measurement is not None)
            skipped = [f"{i.stamp_id}: {i.skipped}" for i in items if i.measurement is None]
            text = f"Re-measured perforation on {n_ok} of {len(items)} stamps."
            if skipped:
                text += "<br>Skipped — " + "; ".join(skipped)
            self.changed.emit()
            self.set_project(self.project_id)
            self.show_message(text, "warning" if skipped else "success")

        self.runner.run(
            workflow.measure_project_perforation,
            root,
            project,
            description="Re-measuring perforation…",
            on_done=done,
            error_title="Re-measuring failed",
        )

    def generate_report(self) -> None:
        if self._project is None:
            return
        root, project = self.ctx.library_root, self._project

        def done(files) -> None:
            url = QUrl.fromLocalFile(str(files.pdf)).toString()
            text = f'Report written for {files.n_stamps} stamp(s). <a href="{url}">Open PDF</a>'
            if files.notes:
                text += "<br>" + "<br>".join(files.notes)
            self.show_message(text, "warning" if files.notes else "success")

        self.runner.run(
            workflow.export_report,
            root,
            project,
            description="Generating report…",
            on_done=done,
            error_title="Could not generate the report",
        )

    def rename_project(self) -> None:
        if self.project_id and library_actions.rename_project(
            self.window(), self.ctx.library_root, self.project_id
        ):
            self.changed.emit()
            self.set_project(self.project_id)

    def remove_project(self) -> None:
        if self.project_id and library_actions.remove_project(
            self.window(), self.ctx.library_root, self.project_id
        ):
            self.project_id = None
            self.changed.emit()
            self.ctx.navigate("", "", "")

    def report_path(self):
        if self.project_id is None:
            return None
        path = project_mod.project_dir(self.ctx.library_root, self.project_id) / "report.pdf"
        return path if path.exists() else None

    def open_report(self) -> None:
        path = self.report_path()
        if path is None:
            self.show_message("No report has been generated for this project yet.", "info")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_folder(self) -> None:
        if self.project_id is not None:
            folder = project_mod.project_dir(self.ctx.library_root, self.project_id)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
