from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from league_cast_assist.logging_config import configure_logging
from league_cast_assist.ui.main_window import MainWindow


def main() -> int:
    configure_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("LeagueCastAssist")
    app.setOrganizationName("LeagueCastAssist")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
