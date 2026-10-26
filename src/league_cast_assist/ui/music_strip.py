from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)

from league_cast_assist.music.models import Song
from league_cast_assist.music.paths import resolve_media

THUMB = 32


def clock(ms: int) -> str:
    seconds = max(0, int(ms) // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


class MusicStrip(QFrame):
    """Compact transport shown under the match view while music is active."""

    pause_requested = Signal()
    next_requested = Signal()
    end_break_requested = Signal()
    open_page_requested = Signal()

    def __init__(self, media_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MusicStrip")
        self.setFixedHeight(46)
        self._media_root = media_root

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        self._artwork = QLabel()
        self._artwork.setObjectName("TrackArtwork")
        self._artwork.setFixedSize(THUMB, THUMB)
        self._artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._artwork)

        self._title = QLabel("Waiting for music")
        self._title.setMinimumWidth(180)
        layout.addWidget(self._title)

        self._progress = QSlider(Qt.Orientation.Horizontal)
        self._progress.setRange(0, 1000)
        self._progress.setEnabled(False)
        self._progress.setFixedWidth(220)
        layout.addWidget(self._progress)

        self._time = QLabel("0:00 / 0:00")
        self._time.setObjectName("PlayerMeta")
        layout.addWidget(self._time)
        layout.addStretch(1)

        self._pause_button = QPushButton("⏸")
        self._pause_button.setObjectName("StripButton")
        self._pause_button.setToolTip("Pause / resume music")
        self._pause_button.clicked.connect(self.pause_requested.emit)
        layout.addWidget(self._pause_button)

        self._next_button = QPushButton("⏭")
        self._next_button.setObjectName("StripButton")
        self._next_button.setToolTip("Skip to the next weighted track")
        self._next_button.clicked.connect(self.next_requested.emit)
        layout.addWidget(self._next_button)

        self._stop_button = QPushButton("■ End Break")
        self._stop_button.setObjectName("StripButton")
        self._stop_button.clicked.connect(self.end_break_requested.emit)
        layout.addWidget(self._stop_button)

        self._open_button = QPushButton("Music page  ›")
        self._open_button.setObjectName("NavJump")
        self._open_button.setToolTip("Open the music page")
        self._open_button.clicked.connect(self.open_page_requested.emit)
        layout.addWidget(self._open_button)

    # ── state ──────────────────────────────────────────────────────────────
    def set_media_root(self, root: Path) -> None:
        self._media_root = root

    def set_song(self, song: Song | None) -> None:
        if song is None:
            self._title.setText("Waiting for music")
            self._artwork.setPixmap(QPixmap())
            self._artwork.setText("♪")
            self._progress.setValue(0)
            self._time.setText("0:00 / 0:00")
            return

        label = song.title
        if song.artist:
            label = f"{song.title} — {song.artist}"
        self._title.setText(label)

        pixmap = QPixmap()
        path = resolve_media(self._media_root, song.artwork_path)
        if path is not None and path.is_file():
            pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._artwork.setPixmap(QPixmap())
            self._artwork.setText("♪")
        else:
            self._artwork.setText("")
            self._artwork.setPixmap(
                pixmap.scaled(
                    THUMB,
                    THUMB,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def set_position(self, position: int, duration: int) -> None:
        self._progress.setValue(int(1000 * position / duration) if duration > 0 else 0)
        self._time.setText(f"{clock(position)} / {clock(duration)}")

    def set_playback_state(self, state: str) -> None:
        if state == "playing":
            self._pause_button.setText("⏸")
        elif state == "paused":
            self._pause_button.setText("▶")
        elif state == "fading":
            self._pause_button.setText("…")
        else:
            self._pause_button.setText("⏸")
