"""`stampan-gui` entry point (SPEC §12, Phase 7).

Deliberately does not import PySide6 at module scope: the GUI is an optional
extra (``pip install stampan[gui]``), and the headless pipeline (CLI, core,
tests) must not require Qt to be installed.
"""

from __future__ import annotations

import sys


def main() -> None:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "stampan-gui: PySide6 is not installed. Install the 'gui' extra (uv sync --extra gui)."
        )
        return

    from stampan.gui import icons, theme
    from stampan.gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setOrganizationName("StampAnalyzer")
    app.setApplicationName("Stamp Analyzer")
    app.setApplicationDisplayName("Stamp Analyzer")
    theme.apply(app)
    app.setWindowIcon(icons.app_icon())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
