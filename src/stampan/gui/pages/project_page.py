"""The project page: overview, project-wide actions, and the cross-stamp
colour overview (SPEC §9/§10)."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGridLayout,
    QMenu,
    QMessageBox,
    QToolButton,
)

from stampan.core import project as project_mod
from stampan.core import publish, workflow
from stampan.core.calibration import reference as ref_mod
from stampan.core.io import read_tiff
from stampan.gui import icons, library_actions
from stampan.gui.dialogs.add_sheet import SOURCE_SCANNER, AddSheetDialog
from stampan.gui.library_tree import KIND_SAMPLE, KIND_STAMP
from stampan.gui.overview import OverviewWidget
from stampan.gui.pages.base import AppContext, Page
from stampan.gui.tasks import TaskRunner
from stampan.gui.widgets import Banner, ScaledImage, StatCard, button, label, rgb_to_pixmap

PICTURE_MAX_HEIGHT = 320


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
        manage = QToolButton()
        manage.setIcon(icons.icon("edit"))
        manage.setToolTip("Re-measure, rename or remove this project")
        manage.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(manage)
        menu.addAction(
            icons.icon("perforation"), "Re-measure Perforation…", self.remeasure_perforation
        )
        menu.addAction(icons.icon("folder"), "Show Project Folder", self.open_folder)
        menu.addSeparator()
        menu.addAction(icons.icon("edit"), "Rename Project…", self.rename_project)
        menu.addAction(icons.icon("clear"), "Remove Project…", self.remove_project)
        manage.setMenu(menu)
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

        #: The stamp selected in the overview table, in calibrated colour.
        self.picture_title = label("", "section")
        self.picture = ScaledImage(upscale=True, max_height=PICTURE_MAX_HEIGHT)
        self.picture_caption = label("", "caption", wrap=True)
        for widget in (self.picture_title, self.picture, self.picture_caption):
            widget.setVisible(False)
            self.body_layout.addWidget(widget)

        self.body_layout.addWidget(label("Comparison", "section"))
        self.body_layout.addWidget(
            label(
                "Every measured colour of the project is listed in the main area with its "
                "perforation, catalogue number and cancellation. Filter and sort there; the "
                "plots follow the visible, ticked rows. Ticked colours are also the ones "
                "published for comparison.",
                "caption",
                wrap=True,
            )
        )
        #: The comparison workspace; the main window shows it in the centre.
        self.overview = OverviewWidget(lambda: self.ctx.library_root)
        self.overview.open_stamp.connect(lambda pid, sid: self.ctx.navigate(KIND_STAMP, pid, sid))
        self.overview.saved.connect(self.changed)
        self.overview.stamp_selected.connect(self._show_stamp)
        self._picture_runner = TaskRunner(self)  # separate: never queued behind long tasks
        self.body_layout.addStretch(1)

    # -- loading ---------------------------------------------------------------

    def set_project(self, project_id: str) -> None:
        self.project_id = project_id
        self._show_stamp(None)  # the table is reloaded without a selection
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
                root,
                project,
                source.ref_id,
                image,
                metadata,
                detect=source.detect_whole_sheet,
                progress=progress,
            )
            return result, warnings + result.warnings

        def done(res) -> None:
            result, warnings = res
            title = project_mod.sheet_title(result.sample)
            text = (
                f"Added {title} with {result.n_stamps} stamp(s)."
                if source.detect_whole_sheet
                else f"Added {title}. Select a region on it to find its stamps."
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

    def _show_stamp(self, stamp_id: str | None) -> None:
        for widget in (self.picture_title, self.picture, self.picture_caption):
            widget.setVisible(stamp_id is not None)
        if stamp_id is None or self.project_id is None:
            return
        project_id = self.project_id
        stamp = project_mod.load_stamp(self.ctx.library_root, project_id, stamp_id)
        sample_id = project_mod.find_sample_for_stamp(self.ctx.library_root, project_id, stamp_id)
        sheet = project_mod.sheet_title(
            project_mod.load_sample(self.ctx.library_root, project_id, sample_id)
        )
        title = f"{project_mod.stamp_label(stamp_id)} · {sheet}"
        if stamp.catalogue_numbers:
            title += f" · {', '.join(stamp.catalogue_numbers)}"
        self.picture_title.setText(title)
        self.picture_caption.setText("Rendering…")
        self._shown_stamp = stamp_id

        def done(result) -> None:
            if getattr(self, "_shown_stamp", None) != stamp_id:
                return  # another row was selected meanwhile
            rgb, calibrated = result
            self.picture.set_pixmap(rgb_to_pixmap(rgb))
            self.picture_caption.setText(
                "Calibrated colour, through the reference's colour model. Double-click the "
                "row to open the stamp."
                if calibrated
                else "No colour model: contrast-stretched scanner RGB, not calibrated colour."
            )

        self._picture_runner.run(
            publish.stamp_picture,
            self.ctx.library_root,
            project_id,
            stamp_id,
            max_px=2 * PICTURE_MAX_HEIGHT,
            description="Loading stamp picture…",
            on_done=done,
            on_fail=lambda message: self.picture_caption.setText(message),
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

    def open_folder(self) -> None:
        if self.project_id is not None:
            folder = project_mod.project_dir(self.ctx.library_root, self.project_id)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
