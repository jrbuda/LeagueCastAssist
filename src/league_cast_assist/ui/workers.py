from __future__ import annotations

import asyncio
import logging
import traceback

from PySide6.QtCore import QObject, QThread, Signal, Slot

from league_cast_assist.config import AppSettings
from league_cast_assist.data.controller import AppController
from league_cast_assist.models import MatchState

LOGGER = logging.getLogger(__name__)


class DataWorker(QObject):
    state_updated = Signal(object)
    status_updated = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self._settings = settings
        self._controller: AppController | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake_event: asyncio.Event | None = None

    @Slot()
    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception:  # noqa: BLE001
            traceback_text = traceback.format_exc()
            LOGGER.exception("Data worker failed")
            self.failed.emit(traceback_text)
        finally:
            self.finished.emit()

    @Slot()
    def stop(self) -> None:
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop_in_loop)
            except RuntimeError:
                self._stop_in_loop()
            return
        self._stop_in_loop()

    @Slot()
    def request_refresh(self) -> None:
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._wake_in_loop)
            except RuntimeError:
                self._wake_in_loop()
            return
        self._wake_in_loop()

    async def _run_async(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake_event = asyncio.Event()
        self._controller = AppController(
            settings=self._settings,
            state_callback=self._emit_state,
            status_callback=self.status_updated.emit,
        )
        try:
            await self._controller.run(self._wake_event)
        finally:
            self._controller = None
            self._wake_event = None
            self._loop = None

    def _emit_state(self, state: MatchState) -> None:
        self.state_updated.emit(state)

    def _stop_in_loop(self) -> None:
        if self._controller is not None:
            self._controller.stop()
        self._wake_in_loop()

    def _wake_in_loop(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()


def start_data_worker(settings: AppSettings) -> tuple[QThread, DataWorker]:
    thread = QThread()
    worker = DataWorker(settings)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    return thread, worker
