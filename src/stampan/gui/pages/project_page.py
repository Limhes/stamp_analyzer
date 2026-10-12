"""The project page: overview, project-wide actions, and the cross-stamp
colour overview (SPEC §9/§10)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGridLayout,
    QMenu,
    QMessageBox,
    QToolButton,
)

from stampan.core import project as project_mod
from stampan.core import workflow
from stampan.core.calibration import reference as ref_mod
from stampan.core.io import read_tiff
from stampan.gui import icons, library_actions
from stampan.gui.dialogs.add_sheet import SOURCE_SCANNER, AddSheetDialog
from stampan.gui.library_tree import KIND_SAMPLE, KIND_STAMP
from stampan.gui.overview import OverviewWidget
from stampan.gui.pages.base import AppContext, Page
from stampan.gui.widgets import Banner, StatCard, button, label


class ProjectPage(Page):
    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(ctx, parent=parent)
        self.project_id: str | None = None
        self._project = None

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

        self.body_layout.addWidget(label("Comparison", "section"))
        self.body_layout.addWidget(
            label(
                "Every measured colour of the project is listed in the main area with its "
                "perforation, catalogue number and cancellation. Filter and sort there; the "
                "plots follow the visible, ticked rows. Ticked colours also go into the "
                "report's colour overview.",
                "caption",
                wrap=True,
            )
        )
        #: The comparison workspace; the main window shows it in the centre.
        self.overview = OverviewWidget(lambda: self.ctx.library_root)
        self.overview.open_stamp.connect(lambda pid, sid: self.ctx.navigate(KIND_STAMP, pid, sid))
        self.body_layout.addStretch(1)

    # -- loading ---------------------------------------------------------------

    def set_project(self, project_id: str) -> None:
        self.project_id = project_id
        root = self.ctx.library_root
        self._project = project = project_mod.load_project(root, project_id)
        self.header.set_text(project.name, f"Project · created {project.created}")
        n = n_perf = n_colour = 0
        for sample_id in project.sample_ids:
            sample = project_mod.load_sample(root, project_id, sample_id)
            for stamp_id in sample.stamp_ids:
                stamp = project_mod.load_stamp(root, project_id, stamp_id)
                n += 1
                n_perf += bool(stamp.perforation.auto or stamp.perforation.manual)
                n_colour += bool(stamp.colour.clusters)
        self.sheets_card.set_value(str(len(project.sample_ids)))
        self.stamps_card.set_value(str(n))
        self.perf_card.set_value(f"{n_perf} / {n}", "stamps measured")
        self.colour_card.set_value(f"{n_colour} / {n}", "stamps measured")
        self.overview.set_project(project_id)

    def deactivate(self) -> None:
        self.overview.save()

    def show_message(self, text: str, severity: str = "info") -> None:
        self.banner.show_message(text, severity)

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
