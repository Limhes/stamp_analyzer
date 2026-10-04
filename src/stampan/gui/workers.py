"""Background worker threads for long-running core calls (SPEC §12).

The GUI must not block on detection/perforation/colour/report, all of which
can take several seconds per stamp. ``Worker`` runs an arbitrary callable on
a Qt thread and reports back on the GUI thread via signals -- no core logic
lives here, just the thread plumbing.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal


class WorkerSignals(QObject):
    finished = Signal(object)  # the callable's return value
    failed = Signal(str)  # a formatted traceback
    progress = Signal(str)  # a short status line, e.g. "stamp 3/15"


class Worker(QThread):
    """Runs ``fn(*args, progress=callback, **kwargs)`` on a background
    thread. ``fn`` may ignore the ``progress`` callback if it has nothing
    incremental to report."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._cancelled = False

    def cancel(self) -> None:
        # Best-effort only: numpy/opencv calls in flight cannot be
        # preempted. A cancelled worker's result is simply discarded when it
        # finishes, rather than applied to the project.
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        def report(message: str) -> None:
            self.signals.progress.emit(message)

        try:
            result = self._fn(*self._args, progress=report, **self._kwargs)
        except TypeError:
            # fn doesn't accept a progress callback -- call it plainly.
            try:
                result = self._fn(*self._args, **self._kwargs)
            except Exception:  # noqa: BLE001 -- surface any failure to the GUI thread
                self.signals.failed.emit(traceback.format_exc())
                return
        except Exception:  # noqa: BLE001
            self.signals.failed.emit(traceback.format_exc())
            return
        if not self._cancelled:
            self.signals.finished.emit(result)
