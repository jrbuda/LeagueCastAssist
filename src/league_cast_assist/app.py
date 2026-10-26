from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from league_cast_assist.logging_config import configure_logging
from league_cast_assist.resources import app_icon_path
from league_cast_assist.ui.main_window import MainWindow
from league_cast_assist.ui.theme import APP_STYLESHEET


def main() -> int:
    configure_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("LeagueCastAssist")
    app.setOrganizationName("LeagueCastAssist")
    # Application-level styling keeps detached pages themed.
    app.setStyleSheet(APP_STYLESHEET)
    icon_path = app_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
