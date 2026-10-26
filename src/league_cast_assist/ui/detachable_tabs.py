"""Detachable tab widget.

Qt has no built-in tear-off for ``QTabWidget`` tabs, so this implements the
gesture directly: drag a tab past a small threshold and release it outside the
tab bar, and the page re-parents into its own window. Closing that window (or
pressing the Dock button / Ctrl+Shift+D) re-docks the *same* page instance, which
keeps playback and widget state alive across the move.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

LOGGER = logging.getLogger(__name__)

DRAG_THRESHOLD_PX = 40


def clamp_geometry(
    geometry: list[int] | tuple[int, ...] | None,
) -> tuple[int, int, int, int] | None:
    """Keep a saved detached-window geometry on an attached screen."""
    if not geometry or len(geometry) != 4:
        return None

    x, y, width, height = (int(value) for value in geometry)
    width = max(640, width)
    height = max(420, height)

    primary = QGuiApplication.primaryScreen()
    if primary is None:
        return None

    # A title-bar grip must be reachable for the window to be draggable.
    grip = QPoint(x + 120, y + 16)
    for screen in QGuiApplication.screens():
        if screen.availableGeometry().contains(grip):
            return x, y, width, height

    centre = primary.availableGeometry().center()
    return centre.x() - width // 2, centre.y() - height // 2, width, height


class DetachedPageWindow(QMainWindow):
    """Hosts a torn-off page. Closing it re-docks the page."""

    settings_requested = Signal()

    def __init__(self, tabs: DetachableTabWidget, page: QWidget, title: str) -> None:
        super().__init__()
        self._tabs = tabs
        self._page = page
        self.setWindowTitle(f"LeagueCastAssist — {title}")
        self.setWindowIcon(tabs.window().windowIcon())
        self.resize(1040, 640)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        bar.addStretch(1)
        settings = QPushButton("Settings")
        settings.setToolTip("Open LeagueCastAssist settings")
        settings.clicked.connect(self.settings_requested.emit)
        bar.addWidget(settings)
        dock = QPushButton("⤢ Dock")
        dock.setToolTip("Move this page back into the main window (Ctrl+Shift+D)")
        dock.clicked.connect(self.close)
        bar.addWidget(dock)
        layout.addLayout(bar)
        layout.addWidget(page, 1)

        self.setCentralWidget(container)
        page.show()  # a page removed from a tab stays hidden until shown again

    @property
    def page(self) -> QWidget:
        return self._page

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._tabs._reinsert_from_window(self._page)  # noqa: SLF001
        event.accept()


class DetachableTabWidget(QTabWidget):
    page_detached = Signal(object)
    page_redocked = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMovable(True)
        self.setDocumentMode(True)
        self._windows: dict[QWidget, DetachedPageWindow] = {}
        self._press_pos: QPoint | None = None
        self._press_index = -1
        self._dragging = False
        self.tabBar().installEventFilter(self)

    # ── event filter: the tear-off gesture ─────────────────────────────────
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is not self.tabBar():
            return super().eventFilter(watched, event)

        if (
            event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            index = self.tabBar().tabAt(event.position().toPoint())
            if index >= 0:
                self._press_index = index
                self._press_pos = event.position().toPoint()
                self._dragging = False

        elif event.type() == QEvent.Type.MouseMove and self._press_pos is not None:
            if event.buttons() & Qt.MouseButton.LeftButton and not self._dragging:
                delta = event.position().toPoint() - self._press_pos
                if abs(delta.x()) + abs(delta.y()) > DRAG_THRESHOLD_PX:
                    self._dragging = True

        elif event.type() == QEvent.Type.MouseButtonRelease and self._press_pos is not None:
            inside = self.tabBar().rect().contains(event.position().toPoint())
            if self._dragging and not inside and 0 <= self._press_index < self.count():
                self.detach(self._press_index)
            self._press_pos = None
            self._press_index = -1
            self._dragging = False

        return super().eventFilter(watched, event)

    # ── detach / redock ────────────────────────────────────────────────────
    def detach(
        self,
        index: int,
        geometry: tuple[int, int, int, int] | None = None,
    ) -> DetachedPageWindow | None:
        if index < 0 or index >= self.count():
            return None
        page = self.widget(index)
        if page is None or self.is_detached(page):
            return None

        title = self.tabText(index)
        self.removeTab(index)
        page.setParent(None)
        window = DetachedPageWindow(self, page, title)
        window.settings_requested.connect(self._forward_settings)
        self._windows[page] = window
        if geometry is not None:
            window.setGeometry(*geometry)
        window.show()
        window.raise_()
        window.activateWindow()
        self.page_detached.emit(page)
        LOGGER.info("Detached page %s", title)
        return window

    def detach_page(
        self,
        page: QWidget,
        geometry: tuple[int, int, int, int] | None = None,
    ) -> bool:
        index = self.indexOf(page)
        if index < 0:
            return False
        self.detach(index, geometry)
        return self.is_detached(page)

    def redock(self, page: QWidget) -> None:
        window = self._windows.get(page)
        if window is not None:
            window.close()  # closeEvent re-inserts the page
            return
        self._insert_page(page)

    def redock_all(self) -> None:
        for page in list(self._windows):
            self.redock(page)

    def is_detached(self, page: QWidget) -> bool:
        return page in self._windows

    def detached_window(self, page: QWidget) -> DetachedPageWindow | None:
        return self._windows.get(page)

    def detached_pages(self) -> list[QWidget]:
        return list(self._windows)

    def page_title(self, page: QWidget) -> str:
        title = getattr(page, "page_title", "")
        if isinstance(title, str) and title:
            return title
        return page.objectName() or "Page"

    # ── internals ──────────────────────────────────────────────────────────
    def _reinsert_from_window(self, page: QWidget) -> None:
        window = self._windows.pop(page, None)
        self._insert_page(page)
        if window is not None:
            window.deleteLater()

    def _insert_page(self, page: QWidget) -> None:
        if self.indexOf(page) < 0:
            page.setParent(None)
            self.addTab(page, self.page_title(page))
        self.setCurrentWidget(page)
        page.show()
        self.page_redocked.emit(page)
        LOGGER.info("Re-docked page %s", self.page_title(page))

    def _forward_settings(self) -> None:
        opener = getattr(self.window(), "open_settings", None)
        if callable(opener):
            opener()
