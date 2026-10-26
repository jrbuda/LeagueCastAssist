from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QVBoxLayout,
)

from league_cast_assist.config import AppSettings
from league_cast_assist.music.catalog import MANIFEST_HINT
from league_cast_assist.music.paths import media_root
from league_cast_assist.ui.workers import (
    CatalogDownloadWorker,
    start_catalog_download_worker,
)


class MediaDownloadDialog(QDialog):
    """Download or verify the media library from the central server."""

    def __init__(
        self,
        settings: AppSettings,
        library_db_path: Path,
        parent=None,  # noqa: ANN001
    ) -> None:
        super().__init__(parent)
        self.settings = settings.model_copy(deep=True)
        self._library_db_path = library_db_path
        self._thread = None
        self._worker: CatalogDownloadWorker | None = None
        self.synced = False

        self.setWindowTitle("Music Media")
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        hint = QLabel(
            "Tracks and artwork are downloaded from the LeagueCastAssist media "
            "server into the local media folder. Files that already match are "
            "skipped, so this is safe to run again at any time."
        )
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)

        form = QFormLayout()
        self._base_url = QLineEdit(self.settings.catalog.base_url)
        self._base_url.setPlaceholderText("https://media.example.com/music")
        form.addRow("Media server URL", self._base_url)
        self._manifest = QLineEdit(self.settings.catalog.manifest)
        self._manifest.setPlaceholderText("manifest.json")
        form.addRow("Manifest file", self._manifest)
        folder = QLabel(str(media_root(self.settings)))
        folder.setObjectName("Muted")
        folder.setWordWrap(True)
        form.addRow("Local folder", folder)
        layout.addLayout(form)

        self._status = QLabel("Not started")
        self._status.setWordWrap(True)
        self._status.setObjectName("Muted")
        layout.addWidget(self._status)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        buttons = QDialogButtonBox()
        self._download = buttons.addButton(
            "Download / verify now", QDialogButtonBox.ButtonRole.ActionRole
        )
        self._download.clicked.connect(lambda: self._start(verify_only=False))
        self._verify = buttons.addButton(
            "Verify only", QDialogButtonBox.ButtonRole.ActionRole
        )
        self._verify.clicked.connect(lambda: self._start(verify_only=True))
        self._close = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self._close.clicked.connect(self._close_clicked)
        layout.addWidget(buttons)

    # ── run ────────────────────────────────────────────────────────────────
    def _collect(self) -> None:
        self.settings.catalog.base_url = self._base_url.text().strip()
        self.settings.catalog.manifest = (
            self._manifest.text().strip() or "manifest.json"
        )

    def _start(self, verify_only: bool) -> None:
        if self._running():
            return
        self._collect()
        if not self.settings.catalog.base_url:
            QMessageBox.information(self, "Media server not configured", MANIFEST_HINT)
            return

        self._download.setEnabled(False)
        self._verify.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._status.setText("Contacting the media server…")

        self._thread, self._worker = start_catalog_download_worker(
            self.settings,
            media_root(self.settings),
            self._library_db_path,
            verify_only=verify_only,
        )
        self._worker.progress_updated.connect(self._on_progress)
        self._worker.sync_finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._thread.start()

    def _on_progress(self, message: str, current: int, total: int) -> None:
        self._status.setText(message)
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(min(current, total))
        else:
            self._progress.setRange(0, 0)

    def _on_finished(self, report) -> None:  # noqa: ANN001
        self.synced = True
        self._progress.setVisible(False)
        text = (
            f"Downloaded {report.downloaded} file(s), already up to date: "
            f"{report.skipped}"
        )
        if report.bytes_downloaded:
            text += f", {report.bytes_downloaded / 1_000_000:.1f} MB transferred"
        if report.failed:
            text += f"\n{len(report.failed)} problem(s):\n" + "\n".join(report.failed[:8])
        self._status.setText(text)
        if report.failed:
            QMessageBox.warning(self, "Media sync finished with problems", text)

    def _on_failed(self, message: str) -> None:
        self._progress.setVisible(False)
        self._status.setText(message)
        QMessageBox.critical(self, "Media download failed", message)

    def _on_worker_finished(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
        self._download.setEnabled(True)
        self._verify.setEnabled(True)

    def _running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _close_clicked(self) -> None:
        if self._running() and self._worker is not None:
            self._status.setText("Cancelling…")
            self._worker.cancel()
            return
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._running() and self._worker is not None:
            self._worker.cancel()
            event.ignore()
            return
        super().closeEvent(event)
