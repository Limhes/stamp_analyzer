"""Background worker thread for long-running core calls (SPEC §12).

``Worker`` runs a callable on a ``QThread`` and reports back on the GUI
thread through signals. No core logic lives here, only thread plumbing.
"""

from __future__ import annotations

import inspect
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal


class WorkerSignals(QObject):
    finished = Signal(object)  # the callable's return value
    failed = Signal(str, str)  # (user-facing message, formatted traceback)
    progress = Signal(str)  # a short status line, e.g. "Measuring 3 of 15…"


def _accepts_progress(fn: Callable[..., Any]) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "progress" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


class Worker(QThread):
    """Runs ``fn(*args, **kwargs)`` on a background thread, adding a
    ``progress`` callback keyword when ``fn`` declares one.

    Whether to pass ``progress`` is decided from ``fn``'s signature, not by
    catching ``TypeError`` -- the earlier "call it, and on TypeError call it
    again without progress" approach re-ran the whole task whenever the task
    itself raised a TypeError (a scan would have been acquired twice).
    """

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        kwargs = dict(self._kwargs)
        if _accepts_progress(self._fn):
            kwargs["progress"] = self.signals.progress.emit
        try:
            result = self._fn(*self._args, **kwargs)
        except Exception as exc:  # noqa: BLE001 -- surface any failure to the GUI thread
            message = str(exc).strip() or type(exc).__name__
            self.signals.failed.emit(message, traceback.format_exc())
            return
        self.signals.finished.emit(result)
