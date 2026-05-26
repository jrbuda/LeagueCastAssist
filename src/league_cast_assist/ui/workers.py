from __future__ import annotations

import asyncio
import logging
import traceback

from PySide6.QtCore import QObject, QThread, Signal, Slot

from league_cast_assist.config import AppSettings
from league_cast_assist.data.asset_resolver import AssetResolver
from league_cast_assist.data.controller import AppController
from league_cast_assist.data.simulation import simulated_match_state
from league_cast_assist.data.static_data import StaticDataService
from league_cast_assist.models import MatchState
from league_cast_assist.update import UpdateRelease, UpdateService, fetch_release_notes

LOGGER = logging.getLogger(__name__)


class DataWorker(QObject):
    state_updated = Signal(object)
    status_updated = Signal(str)
    patch_update_available = Signal(str, str)
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
            patch_update_callback=self.patch_update_available.emit,
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


class StaticDataDownloadWorker(QObject):
    progress_updated = Signal(str, int, int)
    status_updated = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self._settings = settings
        self._cancelled = False

    @Slot()
    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception:  # noqa: BLE001
            traceback_text = traceback.format_exc()
            LOGGER.exception("Static data download failed")
            self.failed.emit(traceback_text)
        finally:
            self.finished.emit()

    async def _run_async(self) -> None:
        service = StaticDataService(
            version=self._settings.assets.version,
            download_assets=True,
        )
        service.set_progress_callback(self.progress_updated.emit)
        service.set_cancel_callback(lambda: self._cancelled)
        self.status_updated.emit("Checking CommunityDragon patch version")
        version_status = await service.patch_version_status()
        if version_status.update_available and version_status.live_version:
            self.status_updated.emit(f"Downloading CommunityDragon {version_status.live_version}")
        else:
            self.status_updated.emit("Downloading all in-game CommunityDragon data")
        await service.ensure_all_in_game_data(version_status)
        self.status_updated.emit("All in-game CommunityDragon data downloaded")


def start_static_data_download_worker(
    settings: AppSettings,
) -> tuple[QThread, StaticDataDownloadWorker]:
    thread = QThread()
    worker = StaticDataDownloadWorker(settings)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    return thread, worker


class UpdateCheckWorker(QObject):
    update_checked = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, current_version: str) -> None:
        super().__init__()
        self._current_version = current_version
        self._cancelled = False

    @Slot()
    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            result = asyncio.run(UpdateService().check_latest(self._current_version))
            if not self._cancelled:
                self.update_checked.emit(result)
        except Exception:  # noqa: BLE001
            if not self._cancelled:
                traceback_text = traceback.format_exc()
                LOGGER.exception("App update check failed")
                self.failed.emit(traceback_text)
        finally:
            self.finished.emit()


def start_update_check_worker(current_version: str) -> tuple[QThread, UpdateCheckWorker]:
    thread = QThread()
    worker = UpdateCheckWorker(current_version)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    return thread, worker


class UpdateDownloadWorker(QObject):
    progress_updated = Signal(str, int, int)
    update_downloaded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, release: UpdateRelease) -> None:
        super().__init__()
        self._release = release
        self._cancelled = False

    @Slot()
    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            downloaded_path = asyncio.run(
                UpdateService().download_release(
                    self._release,
                    progress_callback=self.progress_updated.emit,
                    cancel_callback=lambda: self._cancelled,
                )
            )
            if not self._cancelled:
                self.update_downloaded.emit(downloaded_path)
        except Exception:  # noqa: BLE001
            if not self._cancelled:
                traceback_text = traceback.format_exc()
                LOGGER.exception("App update download failed")
                self.failed.emit(traceback_text)
        finally:
            self.finished.emit()


def start_update_download_worker(release: UpdateRelease) -> tuple[QThread, UpdateDownloadWorker]:
    thread = QThread()
    worker = UpdateDownloadWorker(release)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    return thread, worker


class ReleaseNotesWorker(QObject):
    release_notes_ready = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, version: str) -> None:
        super().__init__()
        self._version = version
        self._cancelled = False

    @Slot()
    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            notes = asyncio.run(fetch_release_notes(self._version))
            if not self._cancelled:
                self.release_notes_ready.emit(notes)
        except Exception:  # noqa: BLE001
            if not self._cancelled:
                traceback_text = traceback.format_exc()
                LOGGER.exception("Release notes fetch failed")
                self.failed.emit(traceback_text)
        finally:
            self.finished.emit()


def start_release_notes_worker(version: str) -> tuple[QThread, ReleaseNotesWorker]:
    thread = QThread()
    worker = ReleaseNotesWorker(version)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    return thread, worker


class DebugDataWorker(QObject):
    champions_ready = Signal(object, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self._settings = settings
        self._cancelled = False

    @Slot()
    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            static_data = StaticDataService(
                version=self._settings.assets.version,
                download_assets=False,
            )
            static_data.set_cancel_callback(lambda: self._cancelled)
            asyncio.run(static_data.ensure_core_data())
            item_lookup = static_data.item_lookup()
            items = [
                item_lookup[item_id]
                for item_id in static_data.summoners_rift_item_ids()
                if item_id in item_lookup
            ]
            self.champions_ready.emit(list(static_data.champion_summary().values()), items)
        except Exception:  # noqa: BLE001
            traceback_text = traceback.format_exc()
            LOGGER.exception("Debug data loading failed")
            self.failed.emit(traceback_text)
        finally:
            self.finished.emit()


def start_debug_data_worker(settings: AppSettings) -> tuple[QThread, DebugDataWorker]:
    thread = QThread()
    worker = DebugDataWorker(settings)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    return thread, worker


class DebugSimulationWorker(QObject):
    state_ready = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        settings: AppSettings,
        champion_ids: list[int],
        item_ids_by_player: list[list[int]],
    ) -> None:
        super().__init__()
        self._settings = settings
        self._champion_ids = champion_ids
        self._item_ids_by_player = item_ids_by_player
        self._cancelled = False

    @Slot()
    def cancel(self) -> None:
        self._cancelled = True

    @Slot()
    def run(self) -> None:
        try:
            state = asyncio.run(self._run_async())
            self.state_ready.emit(state)
        except Exception:  # noqa: BLE001
            traceback_text = traceback.format_exc()
            LOGGER.exception("Debug simulation failed")
            self.failed.emit(traceback_text)
        finally:
            self.finished.emit()

    async def _run_async(self) -> MatchState:
        static_data = StaticDataService(
            version=self._settings.assets.version,
            download_assets=self._settings.assets.mode == "local",
        )
        static_data.set_cancel_callback(lambda: self._cancelled)
        asset_resolver = AssetResolver(
            local_assets=self._settings.assets.mode == "local",
            version=self._settings.assets.version,
        )
        return await simulated_match_state(
            static_data,
            asset_resolver,
            self._champion_ids,
            self._item_ids_by_player,
        )


def start_debug_simulation_worker(
    settings: AppSettings,
    champion_ids: list[int],
    item_ids_by_player: list[list[int]],
) -> tuple[QThread, DebugSimulationWorker]:
    thread = QThread()
    worker = DebugSimulationWorker(settings, champion_ids, item_ids_by_player)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    return thread, worker
