"""Guidance shown when no virtual audio device exists.

Every Windows install can capture audio from an output device, but none ships a
*second* device that OBS could capture while the producer hears nothing. That
device has to come from somewhere, so the app explains it in one screen, links
the free option, and re-checks on demand instead of leaving the user stuck.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

VB_CABLE_URL = "https://vb-audio.com/Cable/"


class StreamDeviceHelpDialog(QDialog):
    """Explains the virtual-device requirement and re-scans on request."""

    def __init__(
        self,
        parent=None,  # noqa: ANN001
        refresh: Callable[[], int] | None = None,
    ) -> None:
        super().__init__(parent)
        self._refresh = refresh
        self.device_found = False
        self.setWindowTitle("Send music to the stream only")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("No virtual audio device found")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        body = QLabel(
            "To put the music in OBS without hearing it yourself, LeagueCastAssist "
            "has to play it to a separate virtual device that OBS captures. Windows "
            "does not include one, so it has to be installed once."
        )
        body.setWordWrap(True)
        layout.addWidget(body)

        recommended = QLabel("Recommended: VB-Cable — free, about a minute to install.")
        recommended.setWordWrap(True)
        layout.addWidget(recommended)

        open_button = QPushButton(f"Open {VB_CABLE_URL}")
        open_button.setToolTip("Opens the VB-Cable download page in your browser")
        open_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(VB_CABLE_URL))
        )
        layout.addWidget(open_button)

        already = QLabel(
            "Already using Voicemod, NVIDIA Broadcast (GeForce Experience) or Steam? "
            "Those include a virtual device — enable it, then press Re-check."
        )
        already.setObjectName("Muted")
        already.setWordWrap(True)
        layout.addWidget(already)

        self._status = QLabel("")
        self._status.setObjectName("Muted")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        recheck = QPushButton("Re-check")
        recheck.setDefault(True)
        recheck.clicked.connect(self._recheck)
        buttons.addWidget(recheck)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _recheck(self) -> None:
        found = 0
        if self._refresh is not None:
            found = self._refresh()
        if found > 0:
            self.device_found = True
            self._status.setText("Virtual audio device found — enabling stream-only.")
            self.accept()
            return
        self._status.setText(
            "Still no virtual audio device. Install one, then press Re-check."
        )
