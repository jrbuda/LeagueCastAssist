from __future__ import annotations

from pathlib import Path

import httpx
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QPixmap


class ImageSignals(QObject):
    loaded = Signal(str, bytes)


class ImageLoadTask(QRunnable):
    def __init__(self, source: str, signals: ImageSignals) -> None:
        super().__init__()
        self._source = source
        self._signals = signals

    @Slot()
    def run(self) -> None:
        data = b""
        if self._source.startswith("http://") or self._source.startswith("https://"):
            try:
                response = httpx.get(self._source, timeout=15.0)
                response.raise_for_status()
                data = response.content
            except httpx.HTTPError:
                data = b""
        elif Path(self._source).exists():
            try:
                data = Path(self._source).read_bytes()
            except OSError:
                data = b""

        self._signals.loaded.emit(self._source, data)


class ImageLoader(QObject):
    loaded = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self._pool = QThreadPool.globalInstance()
        self._signals = ImageSignals()
        self._signals.loaded.connect(self._on_data_loaded)
        self._cache: dict[str, QPixmap] = {}

    def load(self, source: str | None) -> QPixmap | None:
        if not source:
            return None
        if source in self._cache:
            return self._cache[source]

        self._pool.start(ImageLoadTask(source, self._signals))
        return None

    def _cache_loaded(self, source: str, pixmap: QPixmap) -> None:
        if not pixmap.isNull():
            self._cache[source] = pixmap

    def _on_data_loaded(self, source: str, data: bytes) -> None:
        pixmap = QPixmap()
        if data:
            pixmap.loadFromData(data)
        self._cache_loaded(source, pixmap)
        self.loaded.emit(source, pixmap)
