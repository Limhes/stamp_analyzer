"""Reusable presentation widgets: page headers, cards, banners, scaled images,
empty states and the gauge-range slider. No application logic here."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from stampan.gui import icons, theme


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        theme.set_role(widget, role)
    widget.setWordWrap(wrap)
    return widget


def button(
    text: str,
    icon_name: str | None = None,
    role: str | None = None,
    tooltip: str | None = None,
    slot=None,
) -> QPushButton:
    widget = QPushButton(text)
    if icon_name:
        colour = "#ffffff" if role == "primary" else "#3b4555"
        widget.setIcon(icons.icon(icon_name, colour))
    if role:
        theme.set_role(widget, role)
    if tooltip:
        widget.setToolTip(tooltip)
    if slot is not None:
        widget.clicked.connect(slot)
    return widget


def png_to_pixmap(png: bytes | Path | str) -> QPixmap:
    data = Path(png).read_bytes() if isinstance(png, Path | str) else png
    arr = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return QPixmap()
    arr = np.ascontiguousarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))
    h, w = arr.shape[:2]
    return QPixmap.fromImage(QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy())


class PageHeader(QFrame):
    """Breadcrumb, title, subtitle and a row of page-level actions."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        theme.set_role(self, "header")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 12, 12, 12)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.breadcrumb = label("", "caption")
        self.title = label("", "title")
        self.subtitle = label("", "subtitle", wrap=True)
        text_col.addWidget(self.breadcrumb)
        text_col.addWidget(self.title)
        text_col.addWidget(self.subtitle)
        outer.addLayout(text_col, stretch=1)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(4)
        outer.addLayout(self._actions)
        outer.setAlignment(self._actions, Qt.AlignmentFlag.AlignTop)

    def set_text(self, title: str, subtitle: str = "", breadcrumb: str = "") -> None:
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.subtitle.setVisible(bool(subtitle))
        self.breadcrumb.setText(breadcrumb)
        self.breadcrumb.setVisible(bool(breadcrumb))

    def add_action(self, widget: QWidget) -> QWidget:
        self._actions.addWidget(widget)
        return widget


class Banner(QFrame):
    """An inline message with a severity: info, warning, error or success."""

    _ICONS = {"info": "info", "warning": "warning", "error": "error", "success": "success"}
    _COLOURS = {
        "info": theme.ACCENT,
        "warning": theme.WARNING,
        "error": theme.DANGER,
        "success": theme.SUCCESS,
    }

    def __init__(self, text: str = "", severity: str = "info", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)
        self._icon = QLabel()
        self._icon.setFixedSize(18, 18)
        layout.addWidget(self._icon, alignment=Qt.AlignmentFlag.AlignTop)
        self._text = QLabel()
        self._text.setWordWrap(True)
        self._text.setTextFormat(Qt.TextFormat.RichText)
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._text.setOpenExternalLinks(True)
        layout.addWidget(self._text, stretch=1)
        self.show_message(text, severity)

    def show_message(self, text: str, severity: str = "info") -> None:
        self.setProperty("severity", severity)
        self.style().unpolish(self)
        self.style().polish(self)
        self._icon.setPixmap(
            icons.icon(self._ICONS[severity], self._COLOURS[severity]).pixmap(18, 18)
        )
        self._text.setText(text)
        self.setVisible(bool(text))


class StatCard(QFrame):
    def __init__(self, caption: str, parent=None) -> None:
        super().__init__(parent)
        theme.set_role(self, "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)
        layout.addWidget(label(caption.upper(), "section"))
        self.value = label("—", "value")
        layout.addWidget(self.value)
        self.hint = label("", "caption", wrap=True)
        layout.addWidget(self.hint)
        self.hint.setVisible(False)

    def set_value(self, value: str, hint: str = "") -> None:
        self.value.setText(value)
        self.hint.setText(hint)
        self.hint.setVisible(bool(hint))


class ScaledImage(QWidget):
    """An image that fills the available width, keeping its aspect ratio.

    Lays out with height-for-width, so several can be stacked in one scroll
    area. ``upscale`` False caps the drawn size at the image's own size,
    which keeps small plots crisp.
    """

    def __init__(self, upscale: bool = False, placeholder: str = "", parent=None) -> None:
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._upscale = upscale
        self._placeholder = placeholder
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap or QPixmap()
        self.updateGeometry()
        self.update()

    def set_png(self, png: bytes | Path | str | None) -> None:
        self.set_pixmap(png_to_pixmap(png) if png else None)

    def clear(self) -> None:
        self.set_pixmap(None)

    def has_image(self) -> bool:
        return not self._pixmap.isNull()

    def _target_size(self, width: int) -> QSize:
        if self._pixmap.isNull():
            return QSize(width, 0)
        pw, ph = self._pixmap.width(), self._pixmap.height()
        w = width if self._upscale else min(width, pw)
        return QSize(w, round(ph * w / pw))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 -- Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        if self._pixmap.isNull():
            return 40 if self._placeholder else 0
        return self._target_size(width).height()

    def sizeHint(self) -> QSize:  # noqa: N802
        if self._pixmap.isNull():
            return QSize(200, 40 if self._placeholder else 0)
        return QSize(
            min(self._pixmap.width(), 480), self.heightForWidth(min(self._pixmap.width(), 480))
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        if self._pixmap.isNull():
            if self._placeholder:
                painter.setPen(QColor(theme.TEXT_FAINT))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._placeholder)
            painter.end()
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        size = self._target_size(self.width())
        x = (self.width() - size.width()) // 2
        painter.drawPixmap(QRectF(x, 0, size.width(), size.height()).toRect(), self._pixmap)
        painter.end()


class EmptyState(QWidget):
    """A centred icon, title and explanation for a view with nothing to show."""

    def __init__(self, icon_name: str, title: str, text: str = "", dark: bool = False, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        colour = "#6b7280" if dark else theme.TEXT_FAINT
        icon_label = QLabel()
        icon_label.setPixmap(icons.icon(icon_name, colour).pixmap(56, 56))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(icon_label)
        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.title.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {'#c9ced6' if dark else theme.TEXT_MUTED};"
        )
        layout.addWidget(self.title)
        self.text = QLabel(text)
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.text.setStyleSheet(f"color: {colour};")
        self.text.setMaximumWidth(420)
        layout.addWidget(self.text, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.actions = QHBoxLayout()
        self.actions.addStretch(1)
        self.actions.addStretch(1)
        layout.addSpacing(8)
        layout.addLayout(self.actions)
        layout.addStretch(2)

    def add_action(self, widget: QWidget) -> None:
        self.actions.insertWidget(self.actions.count() - 1, widget)


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet(f"color: {theme.BORDER};")
    return line


class RangeSlider(QWidget):
    """A two-handle range slider over a float domain (the perforation
    gauge search band, SPEC §6)."""

    range_changed = Signal(float, float)

    _HANDLE_RADIUS = 7
    _HIT_RADIUS = 11

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setMinimumWidth(140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._domain_lo, self._domain_hi = 0.0, 1.0
        self._val_lo, self._val_hi = 0.0, 1.0
        self._dragging: int | None = None

    def set_domain(self, lo: float, hi: float) -> None:
        self._domain_lo, self._domain_hi = lo, hi if hi > lo else lo + 1e-6
        self.update()

    def set_values(self, lo: float, hi: float) -> None:
        self._val_lo = max(self._domain_lo, min(lo, hi))
        self._val_hi = min(self._domain_hi, max(lo, hi))
        self.update()

    def values(self) -> tuple[float, float]:
        return self._val_lo, self._val_hi

    def _track(self) -> QRectF:
        margin = self._HANDLE_RADIUS + 2
        return QRectF(margin, self.height() / 2 - 2, self.width() - 2 * margin, 4)

    def _x_for(self, value: float) -> float:
        track = self._track()
        span = self._domain_hi - self._domain_lo
        return track.left() + ((value - self._domain_lo) / span if span else 0) * track.width()

    def _value_for(self, x: float) -> float:
        track = self._track()
        frac = 0.0 if track.width() <= 0 else (x - track.left()) / track.width()
        return self._domain_lo + max(0.0, min(1.0, frac)) * (self._domain_hi - self._domain_lo)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(track, 2, 2)
        x_lo, x_hi = self._x_for(self._val_lo), self._x_for(self._val_hi)
        painter.setBrush(QColor(theme.ACCENT if self.isEnabled() else theme.BORDER_STRONG))
        painter.drawRoundedRect(
            QRectF(x_lo, track.top(), max(x_hi - x_lo, 0.0), track.height()), 2, 2
        )
        painter.setPen(QColor(theme.ACCENT if self.isEnabled() else theme.BORDER_STRONG))
        painter.setBrush(QColor(theme.SURFACE))
        r = self._HANDLE_RADIUS
        for x in (x_lo, x_hi):
            painter.drawEllipse(QRectF(x - r, self.height() / 2 - r, 2 * r, 2 * r))
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        self._dragging = (
            0 if abs(x - self._x_for(self._val_lo)) <= abs(x - self._x_for(self._val_hi)) else 1
        )
        self._drag_to(x)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging is not None:
            self._drag_to(event.position().x())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = None

    def _drag_to(self, x: float) -> None:
        value = self._value_for(x)
        if self._dragging == 0:
            self._val_lo = min(value, self._val_hi)
        else:
            self._val_hi = max(value, self._val_lo)
        self.update()
        self.range_changed.emit(self._val_lo, self._val_hi)


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps onto further lines when the
    space runs out, so a toolbar-like row never imposes a wide minimum."""

    def __init__(self, parent=None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing

    def addItem(self, item) -> None:  # noqa: N802 -- Qt override
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line_h > 0:
                x, y = area.x(), y + line_h + self._spacing
                line_h = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()
