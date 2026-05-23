from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from time import monotonic

import httpx
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QPixmap

FAILED_IMAGE_TTL_SECONDS = 60.0
MAX_CACHED_PIXMAPS = 512


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
                timeout = httpx.Timeout(5.0, connect=2.0)
                response = httpx.get(self._source, timeout=timeout)
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
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(4)
        self._signals = ImageSignals()
        self._signals.loaded.connect(self._on_data_loaded)
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._failed_until: dict[str, float] = {}
        self._inflight: set[str] = set()

    def load(self, source: str | None) -> QPixmap | None:
        if not source:
            return None
        if source in self._cache:
            pixmap = self._cache[source]
            self._cache.move_to_end(source)
            return pixmap
        if self._failed_until.get(source, 0.0) > monotonic():
            return QPixmap()
        if source in self._inflight:
            return None

        self._inflight.add(source)
        self._pool.start(ImageLoadTask(source, self._signals))
        return None

    def forget(self, source: str | None) -> None:
        if source:
            self._cache.pop(source, None)
            self._failed_until.pop(source, None)

    def _cache_loaded(self, source: str, pixmap: QPixmap) -> None:
        if not pixmap.isNull():
            self._cache[source] = pixmap
            self._cache.move_to_end(source)
            self._failed_until.pop(source, None)
            while len(self._cache) > MAX_CACHED_PIXMAPS:
                self._cache.popitem(last=False)
        else:
            self._failed_until[source] = monotonic() + FAILED_IMAGE_TTL_SECONDS

    def _on_data_loaded(self, source: str, data: bytes) -> None:
        self._inflight.discard(source)
        pixmap = QPixmap()
        if data:
            pixmap.loadFromData(data)
        self._cache_loaded(source, pixmap)
        self.loaded.emit(source, pixmap)
