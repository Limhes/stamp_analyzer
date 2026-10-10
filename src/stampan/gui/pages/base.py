"""Common scaffolding for the inspector pages (right-hand panel)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from stampan.gui.tasks import TaskRunner
from stampan.gui.viewer import ImageViewer
from stampan.gui.widgets import PageHeader


@dataclass
class AppContext:
    """What pages need from the main window, without depending on it."""

    library_root_getter: Callable[[], Path]
    viewer: ImageViewer
    #: Select (kind, project_id, item_id) in the library and show its page.
    navigate: Callable[[str, str, str], None]

    @property
    def library_root(self) -> Path:
        return self.library_root_getter()


class Page(QWidget):
    """A header plus a body. ``changed`` tells the window the library on
    disk was modified, so the tree can refresh."""

    changed = Signal()

    def __init__(self, ctx: AppContext, scrollable: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.runner = TaskRunner(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.header = PageHeader()
        outer.addWidget(self.header)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(14, 12, 14, 14)
        self.body_layout.setSpacing(10)
        if scrollable:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(self.body)
            outer.addWidget(scroll, stretch=1)
        else:
            outer.addWidget(self.body, stretch=1)

    def deactivate(self) -> None:
        """Called when another page takes over the inspector and viewer."""
