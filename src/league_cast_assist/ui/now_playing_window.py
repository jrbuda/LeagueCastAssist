"""500x200 OBS capture surface, ported from the standalone music player.

The geometry and page timings are a contract with existing OBS scenes: keep the
window a fixed 500x200 and keep the 15 s song page / 5 s vote page cycle.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from league_cast_assist.music.models import Song
from league_cast_assist.music.paths import resolve_media

SONG_PAGE_MS = 15_000
VOTE_PAGE_MS = 5_000
FADE_MS = 350
ART_SIZE = 180


def clock(ms: int) -> str:
    seconds = max(0, int(ms) // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


class NowPlayingWindow(QWidget):
    """Fixed 500x200 content area intended for OBS Window Capture."""

    def __init__(self, media_root: Path) -> None:
        super().__init__()
        self._media_root = media_root
        self.setWindowTitle("LeagueCastAssist — Now Playing (OBS)")
        self.setFixedSize(500, 200)
        self.setObjectName("nowPlaying")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages)

        song_page = QWidget()
        song_page.setObjectName("songPage")
        layout = QHBoxLayout(song_page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(14)

        self.art = QLabel("No artwork")
        self.art.setObjectName("TrackArtwork")
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.art.setFixedSize(ART_SIZE, ART_SIZE)
        layout.addWidget(self.art)

        details = QVBoxLayout()
        details.setSpacing(5)
        heading = QLabel("NOW PLAYING")
        heading.setObjectName("NowPlayingTag")
        heading.setFixedHeight(16)
        details.addWidget(heading)

        self.title = QLabel("Waiting for music")
        self.title.setWordWrap(True)
        self.title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.title.setFixedHeight(44)
        self.title.setObjectName("TrackTitle")
        details.addWidget(self.title)

        self.artist = QLabel("")
        self.artist.setWordWrap(True)
        self.artist.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.artist.setFixedHeight(30)
        self.artist.setObjectName("TrackArtist")
        details.addWidget(self.artist)

        details.addStretch()
        self.progress = QSlider(Qt.Orientation.Horizontal)
        self.progress.setEnabled(False)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        details.addWidget(self.progress)

        self.time = QLabel("0:00 / 0:00")
        self.time.setObjectName("PlayerMeta")
        self.time.setAlignment(Qt.AlignmentFlag.AlignRight)
        details.addWidget(self.time)
        layout.addLayout(details, 1)
        self.pages.addWidget(song_page)

        vote_page = QWidget()
        vote_page.setObjectName("votePage")
        vote_layout = QVBoxLayout(vote_page)
        vote_layout.setContentsMargins(20, 15, 20, 15)
        vote_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vote_heading = QLabel("WANT TO HEAR THIS SONG MORE?")
        vote_heading.setObjectName("VoteHeading")
        vote_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vote_layout.addWidget(vote_heading)
        vote_command = QLabel("!upvote")
        vote_command.setObjectName("VoteCommand")
        vote_command.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vote_layout.addWidget(vote_command)
        vote_footer = QLabel("Type it in Twitch chat to boost this track")
        vote_footer.setObjectName("VoteFooter")
        vote_footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vote_layout.addWidget(vote_footer)
        self.pages.addWidget(vote_page)

        self.page_timer = QTimer(self)
        self.page_timer.setSingleShot(True)
        self.page_timer.timeout.connect(self.begin_page_transition)
        self.page_effect = QGraphicsOpacityEffect(self.pages)
        self.pages.setGraphicsEffect(self.page_effect)
        self.page_animation = QPropertyAnimation(self.page_effect, b"opacity", self)
        self.page_animation.setDuration(FADE_MS)
        self.page_animation.finished.connect(self.finish_page_transition)
        self.transition_fading_out = False
        self.vote_cycle_active = False

        self.setStyleSheet(
            """
            QWidget#nowPlaying { background: #111827; color: #eef2ff; }
            QWidget#songPage, QWidget#votePage { background: #111827; }
            QLabel { color: #d4d9ee; font-size: 12px; }
            QLabel#NowPlayingTag { color: #a78bfa; font-size: 10px; font-weight: 700; }
            QLabel#TrackTitle { font-weight: bold; font-size: 16px; color: white; }
            QLabel#TrackArtist { color: #cbd5e1; }
            QLabel#VoteHeading { color: #e9d5ff; font-size: 17px; font-weight: bold; }
            QLabel#VoteCommand { color: #fbbf24; font-size: 52px; font-weight: bold; }
            QLabel#VoteFooter { color: #cbd5e1; font-size: 13px; }
            QSlider::groove:horizontal { background: #374151; height: 5px; }
            QSlider::sub-page:horizontal { background: #a78bfa; }
            QSlider::handle:horizontal { width: 0px; }
            QLabel#TrackArtwork { background: #0d1016; border: 1px solid #343d4c;
                                  border-radius: 6px; color: #5b6472; }
            """
        )

    def show_song(self, song: Song) -> None:
        self.title.setText(song.title)
        self.artist.setText(song.artist)
        self.reset_vote_cycle()
        self.vote_cycle_active = True
        self.page_timer.start(SONG_PAGE_MS)

        pixmap = QPixmap()
        path = resolve_media(self._media_root, song.artwork_path)
        if path is not None and path.is_file():
            pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.art.setPixmap(QPixmap())
            self.art.setText("No artwork")
        else:
            self.art.setText("")
            self.art.setPixmap(
                pixmap.scaled(
                    ART_SIZE,
                    ART_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def update_progress(self, position: int, duration: int) -> None:
        self.progress.setValue(int(1000 * position / duration) if duration > 0 else 0)
        self.time.setText(f"{clock(position)} / {clock(duration)}")

    def clear_song(self) -> None:
        self.reset_vote_cycle()
        self.title.setText("Waiting for music")
        self.artist.clear()
        self.art.setPixmap(QPixmap())
        self.art.setText("No artwork")
        self.update_progress(0, 0)

    def reset_vote_cycle(self) -> None:
        self.vote_cycle_active = False
        self.page_timer.stop()
        self.page_animation.stop()
        self.transition_fading_out = False
        self.page_effect.setOpacity(1.0)
        self.pages.setCurrentIndex(0)

    def begin_page_transition(self) -> None:
        if not self.vote_cycle_active:
            return
        self.transition_fading_out = True
        self.page_animation.stop()
        self.page_animation.setStartValue(1.0)
        self.page_animation.setEndValue(0.0)
        self.page_animation.start()

    def finish_page_transition(self) -> None:
        if not self.vote_cycle_active:
            return
        if self.transition_fading_out:
            self.transition_fading_out = False
            self.pages.setCurrentIndex(1 - self.pages.currentIndex())
            self.page_animation.setStartValue(0.0)
            self.page_animation.setEndValue(1.0)
            self.page_animation.start()
        else:
            self.page_timer.start(
                VOTE_PAGE_MS if self.pages.currentIndex() == 1 else SONG_PAGE_MS
            )
