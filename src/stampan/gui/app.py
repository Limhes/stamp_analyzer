"""`stampan-gui` entry point. Stub until Phase 7 (SPEC §12).

Deliberately does not import PySide6 at module scope: the GUI is an optional
extra (``pip install stampan[gui]``), and the headless pipeline (CLI, core,
tests) must not require Qt to be installed.
"""

from __future__ import annotations

import sys


def main() -> None:
    try:
        from PySide6.QtWidgets import QApplication, QLabel
    except ImportError:
        print(
            "stampan-gui: PySide6 is not installed. Install the 'gui' extra "
            "(uv sync --extra gui). The GUI itself is not implemented yet "
            "(see docs/PHASES.md, Phase 7)."
        )
        return

    app = QApplication(sys.argv)
    label = QLabel("stampan GUI — not implemented yet (Phase 7)")
    label.setMinimumSize(400, 100)
    label.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
