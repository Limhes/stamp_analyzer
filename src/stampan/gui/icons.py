# ruff: noqa: E501 -- SVG path data / stylesheet rules are data, not code
"""The application's icon set: small stroke icons drawn as inline SVG.

Kept in code (rather than a resource file or an icon-font dependency) so
the set stays self-contained and recolourable; ``icon(name)`` renders and
caches a ``QIcon`` at the sizes toolbars, menus and trees ask for.
"""

from __future__ import annotations

from functools import cache

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtSvg import QSvgRenderer

from stampan.gui import theme

_STROKE = (
    'fill="none" stroke="{c}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"'
)

# 24x24 viewBox, stroke-only, drawn in a single colour.
_PATHS: dict[str, str] = {
    "folder": '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>',
    "folder-plus": '<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/><path d="M12 10.5v5M9.5 13h5"/>',
    "library": '<path d="M4.5 4.5h4v15h-4zM10.5 4.5h4v15h-4zM16 5.6l3.6-1 3 13.8-3.6 1z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "scanner": '<rect x="3" y="10" width="18" height="9" rx="2"/><path d="M6.5 14.5h11M4 10l13.5-5.5"/>',
    "import": '<path d="M12 3.5v11M7.5 10l4.5 4.5 4.5-4.5"/><path d="M4 16.5v2A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5v-2"/>',
    "report": '<path d="M7 3h7l5 5v12.5a.5.5 0 0 1-.5.5h-11a.5.5 0 0 1-.5-.5z"/><path d="M14 3v5h5M9.5 12.5h5M9.5 16h5"/>',
    "target": '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".9"/>',
    "sheet": '<rect x="4" y="3" width="16" height="18" rx="1.5"/><rect x="7" y="6" width="4.5" height="5.5" rx=".5"/><rect x="12.5" y="6" width="4.5" height="5.5" rx=".5"/><rect x="7" y="13" width="4.5" height="5.5" rx=".5"/>',
    "stamp": '<rect x="4.5" y="3.5" width="15" height="17" rx=".5" stroke-dasharray="1.6 1.7"/><rect x="7.5" y="6.5" width="9" height="11" rx=".5"/>',
    "zoom-in": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.8-4.8M10.5 8v5M8 10.5h5"/>',
    "zoom-out": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M20 20l-4.8-4.8M8 10.5h5"/>',
    "fit": '<path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4"/>',
    "actual-size": '<path d="M6.5 9l2-1.2V17M15.5 9l2-1.2V17M12 10.5v.01M12 14.5v.01"/>',
    "pan": '<path d="M12 3v18M3 12h18M12 3l-2.5 2.5M12 3l2.5 2.5M12 21l-2.5-2.5M12 21l2.5-2.5M3 12l2.5-2.5M3 12l2.5 2.5M21 12l-2.5-2.5M21 12l-2.5 2.5"/>',
    "rect": '<rect x="4" y="6" width="16" height="12" rx="1.5"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="8.5" ry="6.5"/>',
    "polygon": '<path d="M5 9l6.5-5 8 5.5-2.5 10H7.5z"/>',
    "brush": '<path d="M18.5 3.5l2 2L11.5 15l-2.5-2.5z"/><path d="M9 12.8c-2.6 0-4.1 1.6-4.1 3.6 0 1.2-.7 2.3-1.9 2.6 1.6 1.3 3.4 1.5 4.9.9 2-.8 3.1-2.3 3.1-4.6z"/>',
    "include": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M12 8.5v7M8.5 12h7"/>',
    "erase": '<path d="M15.5 3.5l5 5-10 10H6l-3-3z"/><path d="M8.5 10.5l5 5M10.5 20.5H21"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "clear": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "refresh": '<path d="M20 11.5a8 8 0 1 1-2.4-5.7L20 8"/><path d="M20 3.5V8h-4.5"/>',
    "run": '<path d="M7.5 4.8v14.4L19 12z"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "chevron-left": '<path d="M15 6l-6 6 6 6"/>',
    "chevron-right": '<path d="M9 6l6 6-6 6"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.8v.01"/>',
    "warning": '<path d="M12 4l9 15.5H3z"/><path d="M12 10v4.2M12 17v.01"/>',
    "error": '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "success": '<circle cx="12" cy="12" r="9"/><path d="M8 12.3l2.7 2.7L16.2 9.5"/>',
    "palette": '<path d="M12 3.5c3 4 6 7.2 6 10.5a6 6 0 0 1-12 0c0-3.3 3-6.5 6-10.5z"/>',
    "perforation": '<rect x="3" y="7.5" width="18" height="9" rx="1"/><path d="M6.5 7.5v3M10 7.5v4.5M13.5 7.5v3M17 7.5v4.5"/>',
    "details": '<path d="M3.5 12V4.5a1 1 0 0 1 1-1H12l9 9-8.5 8.5z"/><circle cx="8" cy="8" r="1.4"/>',
    "open-external": '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "archive": '<rect x="3" y="4" width="18" height="4.5" rx="1"/><path d="M5 8.5V19a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8.5M10 12.5h4"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M14 6l4 4"/>',
    "compare": '<path d="M7 7h13l-3.5-3.5M17 17H4l3.5 3.5"/>',
    "box": '<rect x="5.5" y="5.5" width="13" height="13" stroke-dasharray="2 2"/><rect x="3.5" y="3.5" width="4" height="4" rx=".6"/><rect x="16.5" y="3.5" width="4" height="4" rx=".6"/><rect x="3.5" y="16.5" width="4" height="4" rx=".6"/><rect x="16.5" y="16.5" width="4" height="4" rx=".6"/>',
    "about": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.8v.01"/>',
    "quit": '<path d="M15 4h3.5a1.5 1.5 0 0 1 1.5 1.5v13a1.5 1.5 0 0 1-1.5 1.5H15M10 16l4-4-4-4M14 12H4"/>',
}


def _svg(name: str, colour: str) -> bytes:
    body = _PATHS[name]
    return (
        (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" {_STROKE}>{body}</svg>')
        .format(c=colour)
        .encode()
    )


def _render(svg: bytes, size: int, scale: float = 2.0) -> QPixmap:
    pixmap = QPixmap(int(size * scale), int(size * scale))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg)).render(painter, QRectF(0, 0, pixmap.width(), pixmap.height()))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def render_svg_path(body: str, colour: str, size: int) -> QPixmap:
    """Render a 24×24 stroke path (same style as the icons) at ``size`` px."""
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" {_STROKE}>{body}</svg>'
    return _render(svg.format(c=colour).encode(), size, scale=1.0)


@cache
def icon(name: str, colour: str = "#3b4555") -> QIcon:
    svg = _svg(name, colour)
    faded = _svg(name, theme.TEXT_FAINT)
    result = QIcon()
    for size in (16, 20, 24, 32):
        result.addPixmap(_render(svg, size))
        result.addPixmap(_render(faded, size), QIcon.Mode.Disabled)
    return result


@cache
def status_icon(n_done: int, n_total: int) -> QIcon:
    """A small progress dot for a stamp's measurement state: hollow when
    nothing is measured, amber when partly, green with a check when all."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(7, 7, 18, 18)
    if n_done == 0:
        painter.setPen(QColor(theme.BORDER_STRONG))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(rect)
    elif n_done < n_total:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e9a23b"))
        painter.drawEllipse(rect)
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.SUCCESS))
        painter.drawEllipse(rect)
        pen = painter.pen()
        pen.setColor(QColor("white"))
        pen.setWidthF(2.4)
        pen.setStyle(Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(11, 16), QPointF(14.5, 19.5), QPointF(21, 12.5)]))
    painter.end()
    pixmap.setDevicePixelRatio(2.0)
    return QIcon(pixmap)


def _perforated_outline(x0: float, y0: float, x1: float, y1: float, r: float, pitch: float) -> str:
    """SVG path of a rectangle whose edges are bitten by semicircular
    perforation holes -- the outline of a postage stamp."""

    def holes(length: float) -> list[float]:
        n = max(int(length // pitch), 1)
        start = (length - (n - 1) * pitch) / 2
        return [start + k * pitch for k in range(n)]

    d = [f"M{x0},{y0}"]
    for c in holes(x1 - x0):  # top, left to right
        d.append(f"L{x0 + c - r},{y0} A{r},{r} 0 0 0 {x0 + c + r},{y0}")
    d.append(f"L{x1},{y0}")
    for c in holes(y1 - y0):  # right, top to bottom
        d.append(f"L{x1},{y0 + c - r} A{r},{r} 0 0 0 {x1},{y0 + c + r}")
    d.append(f"L{x1},{y1}")
    for c in holes(x1 - x0):  # bottom, right to left
        d.append(f"L{x1 - c + r},{y1} A{r},{r} 0 0 0 {x1 - c - r},{y1}")
    d.append(f"L{x0},{y1}")
    for c in holes(y1 - y0):  # left, bottom to top
        d.append(f"L{x0},{y1 - c + r} A{r},{r} 0 0 0 {x0},{y1 - c - r}")
    d.append("Z")
    return " ".join(d)


@cache
def app_icon() -> QIcon:
    outline = _perforated_outline(10, 6, 54, 58, 2.3, 6.2)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
      <path d="{outline}" fill="{theme.ACCENT}"/>
      <rect x="16" y="12" width="32" height="40" rx="1.5" fill="none" stroke="white" stroke-width="2"/>
      <circle cx="30" cy="29" r="8" fill="none" stroke="white" stroke-width="3.2"/>
      <path d="M35.8 34.8 L42 41" stroke="white" stroke-width="3.6" stroke-linecap="round"/>
    </svg>""".encode()
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(_render(svg, size, scale=1.0))
    return result
