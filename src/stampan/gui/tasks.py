"""Running work off the GUI thread, and reporting it in one place.

Every page owns a ``TaskRunner``. A runner runs at most one task at a
time; a request that arrives while one is in flight (e.g. another
debounced re-measure) replaces any earlier queued request instead of
starting a second thread -- dropping the only reference to a still-running
``QThread`` destroys it mid-run and aborts the whole process.

All runners report to the application-wide ``TaskHub``, which the main
window turns into a single busy indicator and status message, instead of
every panel showing its own status line.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from stampan.gui.workers import Worker


class TaskHub(QObject):
    """Application-wide activity: how many tasks are running, and what the
    most recent one is doing."""

    activity = Signal(bool, str)  # (busy, message)
    completed = Signal(str)  # a short success message

    def __init__(self) -> None:
        super().__init__()
        self._running = 0
        self._runners: weakref.WeakSet[TaskRunner] = weakref.WeakSet()

    def _register(self, runner: TaskRunner) -> None:
        self._runners.add(runner)

    def _started(self, message: str) -> None:
        self._running += 1
        self.activity.emit(True, message)

    def _progress(self, message: str) -> None:
        self.activity.emit(True, message)

    def _stopped(self) -> None:
        self._running = max(0, self._running - 1)
        if self._running == 0:
            self.activity.emit(False, "")

    def wait_all(self, timeout_ms: int = 15000) -> None:
        """Block until every running task has finished (on shutdown)."""
        for runner in list(self._runners):
            runner.wait(timeout_ms)


_hub: TaskHub | None = None


def hub() -> TaskHub:
    global _hub
    if _hub is None:
        _hub = TaskHub()
    return _hub


class TaskRunner(QObject):
    """Runs one background task at a time on behalf of ``owner``."""

    def __init__(self, owner: QWidget) -> None:
        super().__init__(owner)
        self._owner = owner
        self._worker: Worker | None = None
        self._pending: tuple | None = None
        hub()._register(self)

    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def wait(self, timeout_ms: int = 15000) -> None:
        if self.is_running():
            self._worker.wait(timeout_ms)

    def run(
        self,
        fn: Callable,
        *args,
        description: str = "Working…",
        on_done: Callable | None = None,
        on_fail: Callable | None = None,
        error_title: str = "Operation failed",
        **kwargs,
    ) -> None:
        call = (fn, args, kwargs, description, on_done, on_fail, error_title)
        if self.is_running():
            self._pending = call
            return
        self._start(*call)

    def _start(self, fn, args, kwargs, description, on_done, on_fail, error_title) -> None:
        worker = Worker(fn, *args, **kwargs)
        worker.signals.progress.connect(hub()._progress)
        worker.signals.finished.connect(lambda result: self._finished(result, on_done))
        worker.signals.failed.connect(
            lambda message, details: self._failed(message, details, on_fail, error_title)
        )
        self._worker = worker  # keep a reference so it isn't destroyed mid-run
        hub()._started(description)
        worker.start()

    def _finished(self, result, on_done) -> None:
        hub()._stopped()
        if on_done is not None:
            on_done(result)
        self._run_pending()

    def _failed(self, message: str, details: str, on_fail, error_title: str) -> None:
        hub()._stopped()
        if on_fail is not None:
            on_fail(message)
        else:
            show_error(self._owner, error_title, message, details)
        self._run_pending()

    def _run_pending(self) -> None:
        if self._pending is not None:
            call, self._pending = self._pending, None
            self._start(*call)


def show_error(parent: QWidget | None, title: str, message: str, details: str = "") -> None:
    """A readable error: the message up front, the traceback behind
    "Show Details…" rather than in the reader's face."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(title)
    box.setInformativeText(message)
    if details:
        box.setDetailedText(details)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
