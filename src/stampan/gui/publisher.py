"""Keeps every project's published/ export current (core.publish).

Pages report "project X changed"; a short debounce gathers a burst of
edits into one run, which happens in the background. A failure (e.g. a
sheet scan missing on this computer, so a picture cannot be rendered) is
reported in the status bar, never as a dialog: publishing is a side effect
of the user's work, not something they asked for this moment.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QWidget

from stampan.core import project as project_mod
from stampan.core import publish
from stampan.gui.tasks import TaskRunner

DEBOUNCE_MS = 1500


class Publisher(QObject):
    failed = Signal(str)

    def __init__(self, owner: QWidget, library_root_getter) -> None:
        super().__init__(owner)
        self._root = library_root_getter
        self._dirty: set[str] = set()
        self._runner = TaskRunner(owner)
        self._timer = QTimer(self, singleShot=True, interval=DEBOUNCE_MS)
        self._timer.timeout.connect(self._start)

    def mark_changed(self, project_id: str | None) -> None:
        if project_id:
            self._dirty.add(project_id)
            self._timer.start()

    def mark_all(self) -> None:
        """Every project: on start-up and when another library is opened.
        Cheap when nothing changed -- only the JSON is rewritten."""
        for project in project_mod.list_projects(self._root()):
            self._dirty.add(project.project_id)
        self._timer.start()

    def _start(self) -> None:
        if not self._dirty:
            return
        if self._runner.is_running():
            self._timer.start()  # try again once the current run is done
            return
        project_ids, self._dirty = sorted(self._dirty), set()
        self._runner.run(
            self._publish,
            self._root(),
            project_ids,
            description="Updating published files…",
            on_fail=self.failed.emit,
        )

    @staticmethod
    def _publish(root, project_ids: list[str], progress=None) -> None:
        problems = []
        for project_id in project_ids:
            if not project_mod.project_dir(root, project_id).is_dir():
                continue  # removed meanwhile
            try:
                publish.publish_project(root, project_id, progress=progress)
            except Exception as exc:  # noqa: BLE001 -- reported, never fatal
                problems.append(f"{project_id}: {exc}")
        if problems:
            raise RuntimeError("Could not update the published files — " + "; ".join(problems))
