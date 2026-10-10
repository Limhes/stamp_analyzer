# ruff: noqa: E501 -- SVG path data / stylesheet rules are data, not code
"""Application look: Fusion style, a restrained light palette, one stylesheet.

Widgets opt into variants through a ``role`` dynamic property (e.g. a
primary button, a page title, a card frame) rather than per-widget inline
styles, so the whole look lives here.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

# Neutral greys with one blue accent. Status colours are used sparingly:
# only where a measurement's state needs to read at a glance.
WINDOW = "#f3f4f6"
SURFACE = "#ffffff"
SURFACE_ALT = "#f8f9fb"
BORDER = "#d5d9e0"
BORDER_STRONG = "#b8bec8"
TEXT = "#1c2230"
TEXT_MUTED = "#5f6b7a"
TEXT_FAINT = "#8a94a3"
ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"
ACCENT_SOFT = "#e3ebfd"
SUCCESS = "#15803d"
SUCCESS_SOFT = "#e5f4ea"
WARNING = "#b45309"
WARNING_SOFT = "#fdf3e1"
DANGER = "#b91c1c"
DANGER_SOFT = "#fbe9e9"
VIEWER_BG = "#1f2125"

_STYLESHEET = f"""
QMainWindow, QDialog {{ background: {WINDOW}; }}
QMainWindow::separator {{ background: {BORDER}; width: 1px; height: 1px; }}

QDockWidget {{ titlebar-close-icon: none; font-weight: 600; }}
QDockWidget::title {{
    background: {WINDOW}; padding: 6px 10px; border-bottom: 1px solid {BORDER};
    text-align: left; color: {TEXT_MUTED};
}}

QToolBar {{ background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER};
            spacing: 2px; padding: 3px 6px; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 4px 6px; }}
QToolButton {{ border: 1px solid transparent; border-radius: 5px; padding: 4px 6px; color: {TEXT}; }}
QToolButton:hover {{ background: {SURFACE_ALT}; border-color: {BORDER}; }}
QToolButton:pressed, QToolButton:checked {{ background: {ACCENT_SOFT}; border-color: #b9cdf8; }}
QToolButton:disabled {{ color: {TEXT_FAINT}; }}

QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: 5px;
    padding: 5px 12px; color: {TEXT}; min-height: 18px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_SOFT}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {BORDER}; background: {SURFACE_ALT}; }}
QPushButton[role="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[role="primary"]:disabled {{ background: #9db5ef; border-color: #9db5ef; color: #eef2fb; }}
QPushButton[role="danger"]:hover {{ border-color: {DANGER}; color: {DANGER}; }}
QPushButton[role="flat"] {{ border-color: transparent; background: transparent; padding: 4px 6px; }}
QPushButton[role="flat"]:hover {{ background: {SURFACE_ALT}; border-color: {BORDER}; }}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {SURFACE}; border: 1px solid {BORDER_STRONG}; border-radius: 5px;
    padding: 4px 6px; selection-background-color: {ACCENT};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 16px; border: none; background: transparent;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {SURFACE_ALT}; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 20px; border: none; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {SURFACE_ALT}; color: {TEXT_FAINT};
}}

QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; top: -1px; }}
QTabBar::tab {{
    background: transparent; border: none; border-bottom: 2px solid transparent;
    padding: 7px 14px; color: {TEXT_MUTED}; font-weight: 600;
}}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom-color: {ACCENT}; }}

QTreeView, QListView, QTableView {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    alternate-background-color: {SURFACE_ALT}; outline: none;
}}
QTreeView::item, QListView::item {{ padding: 3px 2px; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{
    background: {ACCENT_SOFT}; color: {TEXT};
}}
QTreeView::item:hover, QListView::item:hover {{ background: {SURFACE_ALT}; }}
QHeaderView::section {{
    background: {SURFACE_ALT}; border: none; border-bottom: 1px solid {BORDER};
    padding: 5px 6px; color: {TEXT_MUTED}; font-weight: 600;
}}
QTableView {{ gridline-color: {BORDER}; }}

QGroupBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    margin-top: 14px; padding: 12px 10px 10px 10px; font-weight: 600;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEXT_MUTED}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_MUTED}; }}
QStatusBar::item {{ border: none; }}
QProgressBar {{ border: 1px solid {BORDER}; border-radius: 4px; background: {SURFACE_ALT};
               max-height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QLabel[role="title"] {{ font-size: 15pt; font-weight: 600; color: {TEXT}; }}
QLabel[role="subtitle"] {{ color: {TEXT_MUTED}; }}
QLabel[role="section"] {{ font-weight: 700; color: {TEXT_MUTED}; text-transform: uppercase;
                         font-size: 8pt; letter-spacing: 0.5px; padding-top: 6px; }}
QLabel[role="caption"] {{ color: {TEXT_MUTED}; font-size: 9pt; }}
QLabel[role="value"] {{ font-size: 14pt; font-weight: 600; color: {TEXT}; }}
QLabel[role="mono"] {{ font-family: monospace; color: {TEXT_MUTED}; }}

QFrame[role="card"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame[role="header"] {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QFrame[severity="info"] {{ background: {ACCENT_SOFT}; border: 1px solid #c4d5fa; border-radius: 6px; }}
QFrame[severity="warning"] {{ background: {WARNING_SOFT}; border: 1px solid #f1d6a4; border-radius: 6px; }}
QFrame[severity="error"] {{ background: {DANGER_SOFT}; border: 1px solid #efc0c0; border-radius: 6px; }}
QFrame[severity="success"] {{ background: {SUCCESS_SOFT}; border: 1px solid #b9e0c6; border-radius: 6px; }}

QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 5px 24px 5px 10px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QMenuBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {ACCENT_SOFT}; border-radius: 4px; }}

QToolTip {{ background: {TEXT}; color: white; border: none; padding: 4px 6px; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: WINDOW,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: SURFACE_ALT,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: SURFACE,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: TEXT,
        QPalette.ColorRole.ToolTipText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: TEXT_FAINT,
        QPalette.ColorRole.Link: ACCENT,
        QPalette.ColorRole.Mid: BORDER,
    }
    for role, colour in roles.items():
        palette.setColor(role, QColor(colour))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(TEXT_FAINT))
    app.setPalette(palette)
    font = QFont(app.font())
    font.setPointSizeF(max(font.pointSizeF(), 10.0))
    app.setFont(font)
    app.setStyleSheet(_STYLESHEET + _arrow_rules())


def _arrow_rules() -> str:
    """Spin-box and combo-box arrows. Styling those widgets' frames makes
    Qt drop the native arrows, and stylesheets can only draw images from
    files, so small chevrons are rendered to the temporary directory."""
    from stampan.gui import icons  # icons imports this module

    folder = (
        Path(tempfile.gettempdir()) / f"stampan-theme-{os.getuid() if hasattr(os, 'getuid') else 0}"
    )
    folder.mkdir(exist_ok=True)
    paths = {}
    for name, svg in (("up", '<path d="M6 15l6-6 6 6"/>'), ("down", '<path d="M6 9l6 6 6-6"/>')):
        path = folder / f"arrow_{name}.png"
        icons.render_svg_path(svg, TEXT_MUTED, 10).save(str(path))
        paths[name] = path.as_posix()
    return f"""
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({paths["up"]}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow,
QComboBox::down-arrow {{ image: url({paths["down"]}); width: 10px; height: 10px; }}
"""


def set_role(widget: QWidget, role: str) -> QWidget:
    """Tag ``widget`` with a stylesheet variant (see ``_STYLESHEET``)."""
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    return widget
