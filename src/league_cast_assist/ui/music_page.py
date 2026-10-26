from __future__ import annotations

import random
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from league_cast_assist.config import AppSettings
from league_cast_assist.music import devices, selection
from league_cast_assist.music.library import MusicLibrary
from league_cast_assist.music.models import Song
from league_cast_assist.music.paths import resolve_media
from league_cast_assist.music.playback import PlaybackEngine

ARTWORK_SIZE = 88


def clock(ms: int) -> str:
    seconds = max(0, int(ms) // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


class MusicPage(QWidget):
    """Producer page: now-playing, transport, library and one-tap tracks."""

    page_title = "Music"
    track_changed = Signal(object)
    status_message = Signal(str)
    settings_changed = Signal()
    request_media_download = Signal()
    request_settings = Signal()

    def __init__(
        self,
        settings: AppSettings,
        library: MusicLibrary,
        engine: PlaybackEngine,
        media_root: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("MusicPage")
        self._settings = settings
        self._library = library
        self._engine = engine
        self._media_root = media_root
        self._current: Song | None = None
        self._pending_song_id: int | None = None
        self._rows: list[Song] = []
        self._build_ui()
        self._connect_engine()
        self.refresh_all()

    # ── construction ───────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 6, 0, 0)
        root.setSpacing(6)
        root.addWidget(self._build_now_playing())
        root.addLayout(self._build_transport())
        root.addWidget(self._build_body(), 1)

    def _build_now_playing(self) -> QFrame:
        card = QFrame()
        card.setObjectName("NowPlayingCard")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(14)

        self._artwork = QLabel("no art")
        self._artwork.setObjectName("TrackArtwork")
        self._artwork.setFixedSize(ARTWORK_SIZE, ARTWORK_SIZE)
        self._artwork.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._artwork)

        info = QVBoxLayout()
        info.setSpacing(2)
        self._state_tag = QLabel("NOW PLAYING")
        self._state_tag.setObjectName("NowPlayingTag")
        info.addWidget(self._state_tag)
        self._title = QLabel("Waiting for music")
        self._title.setObjectName("TrackTitle")
        self._title.setMinimumWidth(240)
        info.addWidget(self._title)
        self._artist = QLabel("")
        self._artist.setObjectName("TrackArtist")
        info.addWidget(self._artist)

        progress_row = QHBoxLayout()
        self._progress = QSlider(Qt.Orientation.Horizontal)
        self._progress.setRange(0, 1000)
        self._progress.setEnabled(False)
        progress_row.addWidget(self._progress, 1)
        self._time = QLabel("0:00 / 0:00")
        self._time.setObjectName("PlayerMeta")
        progress_row.addWidget(self._time)
        info.addLayout(progress_row)
        layout.addLayout(info, 1)

        stats = QHBoxLayout()
        stats.setSpacing(22)
        self._stat_used = self._stat_block(stats, "TRACKS USED")
        self._stat_plays = self._stat_block(stats, "PLAYS")
        self._stat_eligible = self._stat_block(stats, "ELIGIBLE")
        layout.addLayout(stats)

        session = QVBoxLayout()
        session.setSpacing(4)
        self._session_label = QLabel("STREAM SESSION")
        self._session_label.setObjectName("PlayerMeta")
        self._session_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        session.addWidget(self._session_label)
        self._new_stream = QPushButton("New Stream")
        self._new_stream.setToolTip("Reset the play history and repeat cooldowns")
        self._new_stream.clicked.connect(self._new_stream_session)
        session.addWidget(self._new_stream)
        session.addStretch(1)
        layout.addLayout(session)
        return card

    def _stat_block(self, layout: QHBoxLayout, label: str) -> QLabel:
        box = QVBoxLayout()
        box.setSpacing(0)
        value = QLabel("0")
        value.setObjectName("StatValue")
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(value)
        caption = QLabel(label)
        caption.setObjectName("StatLabel")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(caption)
        layout.addLayout(box)
        return value

    def _build_transport(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self._start_button = QPushButton("▶  Start Break")
        self._start_button.setObjectName("TransportPrimary")
        self._start_button.setMinimumWidth(130)
        self._start_button.clicked.connect(self.start_break)
        row.addWidget(self._start_button)

        self._pause_button = QPushButton("⏸  Pause")
        self._pause_button.clicked.connect(self.toggle_pause)
        row.addWidget(self._pause_button)

        self._next_button = QPushButton("⏭  Next Weighted")
        self._next_button.setToolTip("Pick the next eligible track using vote weighting")
        self._next_button.clicked.connect(self.next_weighted)
        row.addWidget(self._next_button)

        self._stop_button = QPushButton("■  End Break (Fade)")
        self._stop_button.setObjectName("TransportStop")
        self._stop_button.clicked.connect(self.end_break)
        row.addWidget(self._stop_button)

        row.addSpacing(12)
        row.addWidget(QLabel("Volume"))
        self._volume = QSlider(Qt.Orientation.Horizontal)
        self._volume.setRange(0, 100)
        self._volume.setValue(self._settings.music.volume)
        self._volume.setFixedWidth(120)
        self._volume.valueChanged.connect(self._on_volume)
        row.addWidget(self._volume)

        row.addSpacing(12)
        row.addWidget(QLabel("OBS output"))
        self._device = QComboBox()
        self._device.setMinimumWidth(140)
        self._device.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._device.setMinimumContentsLength(10)
        self._populate_devices()
        self._device.currentIndexChanged.connect(self._on_device_changed)
        row.addWidget(self._device)

        self._monitor = QPushButton("🔇  Monitor: OFF")
        self._monitor.setCheckable(True)
        self._monitor.setToolTip(
            "Play the same music on your own speakers. Requires a different "
            "device than the OBS output."
        )
        self._monitor.toggled.connect(self._on_monitor_toggled)
        row.addWidget(self._monitor)

        self._stream_only = QCheckBox("Send to stream only")
        self._stream_only.setToolTip(
            "Route the music to a virtual audio device that OBS captures, and keep "
            "it out of your speakers. In OBS use Audio Output Capture and pick the "
            "matching device."
        )
        self._stream_only.toggled.connect(self._on_stream_only_toggled)
        row.addWidget(self._stream_only)
        row.addStretch(1)
        return row

    def _build_body(self) -> QSplitter:
        split = QSplitter(Qt.Orientation.Horizontal)

        library_card = QFrame()
        library_card.setObjectName("DetailPanel")
        library_layout = QVBoxLayout(library_card)
        library_layout.setContentsMargins(10, 8, 10, 10)
        library_layout.setSpacing(6)

        head = QHBoxLayout()
        heading = QLabel("Library")
        heading.setObjectName("SectionTitle")
        head.addWidget(heading)
        self._library_summary = QLabel("")
        self._library_summary.setObjectName("Muted")
        head.addWidget(self._library_summary)
        head.addStretch(1)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search title, artist, album…")
        self._search.setFixedWidth(280)
        self._search.textChanged.connect(self._refresh_library)
        head.addWidget(self._search)
        library_layout.addLayout(head)

        self._library_stack = QStackedWidget()
        self._library_stack.addWidget(self._build_table())
        self._library_stack.addWidget(self._build_empty_state())
        library_layout.addWidget(self._library_stack, 1)
        split.addWidget(library_card)

        split.addWidget(self._build_sidebar())
        split.setSizes([1000, 330])
        return split

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Title", "Artist", "Album", "Votes", "Status"])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        for column in (0, 1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
            item = self._table.horizontalHeaderItem(column)
            if item is not None:
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.cellDoubleClicked.connect(self._play_selected_row)
        return self._table

    def _build_empty_state(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 24, 16, 24)
        layout.setSpacing(8)
        layout.addStretch(1)

        title = QLabel("No music library yet")
        title.setObjectName("SectionTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self._empty_detail = QLabel(
            "Tracks and artwork live in the media folder. Download them from the "
            "media server, or add files manually and restart the app."
        )
        self._empty_detail.setObjectName("Muted")
        self._empty_detail.setWordWrap(True)
        self._empty_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._empty_detail)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        download = QPushButton("Download media…")
        download.clicked.connect(self.request_media_download.emit)
        buttons.addWidget(download)
        open_folder = QPushButton("Open media folder")
        open_folder.clicked.connect(self._open_media_folder)
        buttons.addWidget(open_folder)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return page

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(6)

        one_tap = QFrame()
        one_tap.setObjectName("SideCard")
        one_tap_layout = QVBoxLayout(one_tap)
        one_tap_layout.setContentsMargins(10, 8, 10, 10)
        one_tap_layout.setSpacing(6)
        heading = QLabel("One-tap tracks")
        heading.setObjectName("SectionTitle")
        one_tap_layout.addWidget(heading)
        for label, group in (
            ("♫  Silver Scrapes", "silver_scrapes"),
            ("♫  Echoes from the Archives", "echoes"),
            ("♫  Random Worlds Orchestral", "worlds_orchestral"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, name=group: self.play_group(name))
            one_tap_layout.addWidget(button)
        side_layout.addWidget(one_tap)

        history = QFrame()
        history.setObjectName("SideCard")
        history_layout = QVBoxLayout(history)
        history_layout.setContentsMargins(10, 8, 10, 10)
        history_layout.setSpacing(4)
        history_heading = QLabel("Recently played")
        history_heading.setObjectName("SectionTitle")
        history_layout.addWidget(history_heading)
        self._history_layout = QVBoxLayout()
        self._history_layout.setSpacing(2)
        history_layout.addLayout(self._history_layout)
        history_layout.addStretch(1)
        side_layout.addWidget(history, 1)
        return side

    # ── engine wiring ──────────────────────────────────────────────────────
    def _connect_engine(self) -> None:
        self._engine.position_changed.connect(self._on_position)
        self._engine.duration_changed.connect(self._on_duration)
        self._engine.state_changed.connect(self._on_state)
        self._engine.track_finished.connect(self._on_track_finished)
        self._engine.fade_finished.connect(self._on_fade_finished)
        self._engine.error_occurred.connect(self._on_error)
        self._engine.monitor_state_changed.connect(self._sync_monitor_button)
        self._engine.set_volume(self._settings.music.volume)

        if self._settings.music.stream_only:
            output_index = self._loopback_index()
            if output_index is not None:
                self._device.setCurrentIndex(output_index)
            self._engine.set_monitor_enabled(False)
            self._settings.music.monitor_enabled = False
        self._sync_stream_only_controls()

        monitor_ok = self._engine.set_monitor_enabled(self._settings.music.monitor_enabled)
        if not monitor_ok and self._settings.music.monitor_enabled:
            self.status_message.emit(
                "Producer monitor needs a different output device than the OBS output."
            )
        self._sync_monitor_button(self._engine.monitor_enabled)

    def _populate_devices(self) -> None:
        self._device.blockSignals(True)
        self._device.clear()
        outputs = PlaybackEngine.audio_outputs()
        for device in outputs:
            loopback = devices.is_loopback_description(device.description())
            self._device.addItem(
                devices.device_label(device.description(), loopback),
                devices.device_id(device),
            )
        wanted = self._settings.music.stream_device_id
        index = 0
        default_id = devices.device_id(PlaybackEngine.default_output())
        for position in range(self._device.count()):
            if wanted and self._device.itemData(position) == wanted:
                index = position
                break
            if not wanted and self._device.itemData(position) == default_id:
                index = position
        if self._device.count():
            self._device.setCurrentIndex(index)
            self._engine.set_output_device(PlaybackEngine.audio_outputs()[index])
        self._device.blockSignals(False)

    def _loopback_index(self) -> int | None:
        outputs = PlaybackEngine.audio_outputs()
        return devices.pick_loopback_index([device.description() for device in outputs])

    def _selected_is_loopback(self) -> bool:
        outputs = PlaybackEngine.audio_outputs()
        index = self._device.currentIndex()
        if index < 0 or index >= len(outputs):
            return False
        return devices.is_loopback_description(outputs[index].description())

    def _sync_stream_only_controls(self) -> None:
        """Keep the monitor button and the stream-only box mutually consistent."""
        stream_only = self._settings.music.stream_only
        self._stream_only.blockSignals(True)
        self._stream_only.setChecked(stream_only)
        self._stream_only.blockSignals(False)
        self._monitor.setEnabled(not stream_only)
        if stream_only:
            self._monitor.setToolTip(
                "Turn off 'Send to stream only' to hear the music on your own speakers."
            )
        else:
            self._monitor.setToolTip(
                "Play the same music on your own speakers. Requires a different "
                "device than the OBS output."
            )

    # ── public API used by the shell / strip ───────────────────────────────
    @property
    def current_song(self) -> Song | None:
        return self._current

    @property
    def is_playing(self) -> bool:
        return self._engine.state == "playing"

    @property
    def playback_state(self) -> str:
        return self._engine.state

    def start_break(self) -> None:
        if self._engine.state == "playing":
            self.status_message.emit("Break already active.")
            return
        self.next_weighted()

    def next_weighted(self) -> None:
        song = selection.pick_weighted(
            self._library.songs.values(),
            self._library.play_history(),
            self._library.votes_by_song(),
            self._pending_song_id,
        )
        if song is None:
            self.status_message.emit(
                "No eligible tracks yet — five other songs are required between repeats."
            )
            return
        self.play_song(song)

    def toggle_pause(self) -> None:
        self._engine.toggle_pause()

    def end_break(self) -> None:
        if self._engine.state == "stopped":
            self._clear_now_playing()
            return
        self._engine.stop(fade=True)

    def play_group(self, group_name: str) -> None:
        songs = selection.group_songs(self._library.songs.values(), group_name)
        if not songs:
            self.status_message.emit(f"No tracks configured for {group_name}.")
            return
        if group_name == "worlds_orchestral":
            self.play_song(random.choice(songs))
        else:
            self.play_song(songs[0])

    def play_song(self, song: Song) -> None:
        if song.playback_mode == "disabled":
            self.status_message.emit(f"{song.title} is disabled.")
            return
        path = resolve_media(self._media_root, song.file_path)
        if path is None or not path.is_file():
            self.status_message.emit(f"Missing audio file: {song.file_path}")
            return
        self._current = song
        self._pending_song_id = song.song_id
        self._engine.play_path(path)
        self._show_now_playing(song)
        self.status_message.emit(f"Loading: {song.title}")
        self.track_changed.emit(song)

    def refresh_all(self) -> None:
        self._refresh_library()
        self._refresh_stats()
        self._refresh_history()
        self._refresh_session_label()

    def set_media_root(self, root: Path) -> None:
        self._media_root = root
        if self._current is not None:
            self._load_artwork(self._current)
        self._refresh_library()

    def apply_settings(self) -> None:
        """Re-apply persisted volume/monitor/stream-only values after Settings."""
        volume = self._settings.music.volume
        self._volume.blockSignals(True)
        self._volume.setValue(volume)
        self._volume.blockSignals(False)
        self._engine.set_volume(volume)

        if self._settings.music.stream_only:
            output_index = self._loopback_index()
            if output_index is not None and output_index != self._device.currentIndex():
                self._device.setCurrentIndex(output_index)
            self._engine.set_monitor_enabled(False)
            self._settings.music.monitor_enabled = False
        self._sync_stream_only_controls()

        wanted = self._settings.music.monitor_enabled
        if not self._engine.set_monitor_enabled(wanted):
            self._sync_monitor_button(False)
            self.status_message.emit(
                "Producer monitor needs a different output device than the OBS output."
            )
            return
        self._sync_monitor_button(wanted)

    # ── library / stats ────────────────────────────────────────────────────
    def _refresh_library(self) -> None:
        term = self._search.text().casefold() if hasattr(self, "_search") else ""
        history = self._library.play_history()
        votes = self._library.votes_by_song()
        songs = sorted(self._library.songs.values(), key=lambda song: song.title.casefold())
        rows = [song for song in songs if term in song.search_text] if term else songs

        self._rows = rows
        self._table.setRowCount(len(rows))
        for index, song in enumerate(rows):
            values = (
                song.title,
                song.artist,
                song.album or "",
                str(votes.get(song.song_id, 0)),
                selection.status_text(song, history),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, song.song_id)
                self._table.setItem(index, column, item)

        has_library = bool(self._library.songs) and self._library.available
        self._library_stack.setCurrentIndex(0 if has_library else 1)
        if not self._library.available:
            self._empty_detail.setText(
                f"Music library could not be opened.\n{self._library.error}\n\n"
                f"Expected at: {self._library.db_path}"
            )
        count = len(self._library.songs)
        size_mb = self._media_size_mb()
        self._library_summary.setText(
            f"{count} tracks · {size_mb:,.0f} MB" if count else "no tracks"
        )

    def _media_size_mb(self) -> float:
        music_dir = self._media_root / "music"
        if not music_dir.is_dir():
            return 0.0
        total = 0
        for path in music_dir.glob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total / 1_000_000

    def _refresh_stats(self) -> None:
        stats = self._library.stats()
        self._stat_used.setText(f"{stats.used}/{stats.total}" if stats.total else "0/0")
        self._stat_plays.setText(str(stats.plays))
        self._stat_eligible.setText(str(stats.eligible))

    def _refresh_history(self) -> None:
        while self._history_layout.count():
            item = self._history_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

        history = self._library.play_history()
        recent: list[tuple[int, int]] = []  # (song_id, repeats)
        for song_id in history:
            if recent and recent[-1][0] == song_id:
                recent[-1] = (song_id, recent[-1][1] + 1)
            else:
                recent.append((song_id, 1))
        recent = recent[-5:][::-1]
        if not recent:
            empty = QLabel("Nothing played yet in this session.")
            empty.setObjectName("PlayerMeta")
            self._history_layout.addWidget(empty)
            return
        for position, (song_id, repeats) in enumerate(recent):
            song = self._library.song(song_id)
            name = song.title if song else f"#{song_id}"
            suffix = f"  ×{repeats}" if repeats > 1 else ""
            label = QLabel(f"{position + 1}. {name}{suffix}")
            label.setWordWrap(True)
            self._history_layout.addWidget(label)

    def _clear_layout(self, layout) -> None:  # noqa: ANN001
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _refresh_session_label(self) -> None:
        session = self._library.session_id
        self._session_label.setText(f"STREAM SESSION #{session}" if session else "NO SESSION")

    # ── now playing ────────────────────────────────────────────────────────
    def _show_now_playing(self, song: Song) -> None:
        self._title.setText(song.title)
        subtitle = song.artist
        if song.album:
            subtitle = f"{song.artist} — {song.album}" if song.artist else song.album
        self._artist.setText(subtitle)
        self._load_artwork(song)
        self._progress.setValue(0)
        self._time.setText(f"0:00 / {clock(int(song.duration_seconds * 1000))}")

    def _load_artwork(self, song: Song) -> None:
        pixmap = QPixmap()
        path = resolve_media(self._media_root, song.artwork_path)
        if path is not None and path.is_file():
            pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._artwork.setPixmap(QPixmap())
            self._artwork.setText("no art")
            return
        self._artwork.setText("")
        self._artwork.setPixmap(
            pixmap.scaled(
                ARTWORK_SIZE,
                ARTWORK_SIZE,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _clear_now_playing(self) -> None:
        self._current = None
        self._pending_song_id = None
        self._title.setText("Waiting for music")
        self._artist.clear()
        self._artwork.setPixmap(QPixmap())
        self._artwork.setText("no art")
        self._progress.setValue(0)
        self._time.setText("0:00 / 0:00")
        self._state_tag.setText("NOW PLAYING")
        self.track_changed.emit(None)

    # ── engine slots ───────────────────────────────────────────────────────
    def _on_position(self, position: int, duration: int) -> None:
        self._progress.setValue(int(1000 * position / duration) if duration > 0 else 0)
        self._time.setText(f"{clock(position)} / {clock(duration)}")

    def _on_duration(self, duration: int) -> None:
        self._time.setText(f"{clock(self._engine.position)} / {clock(duration)}")

    def _on_state(self, state: str) -> None:
        if state == "playing":
            self._state_tag.setText("NOW PLAYING")
            self._pause_button.setText("⏸  Pause")
            if self._pending_song_id is not None:
                self._library.record_play(self._pending_song_id)
                self._pending_song_id = None
                self._refresh_library()
                self._refresh_stats()
                self._refresh_history()
            self.status_message.emit(
                f"Playing: {self._current.title}" if self._current else "Playing"
            )
        elif state == "paused":
            self._state_tag.setText("PAUSED")
            self._pause_button.setText("▶  Resume")
        elif state == "fading":
            self._state_tag.setText("FADING OUT")
        else:
            self._pause_button.setText("⏸  Pause")

    def _on_track_finished(self) -> None:
        if self._current is not None and self._current.playback_mode == "manual":
            self._clear_now_playing()
            self.status_message.emit("Special track finished.")
            return
        self.next_weighted()

    def _on_fade_finished(self) -> None:
        self._clear_now_playing()
        self.status_message.emit("Break ended.")

    def _on_error(self, description: str) -> None:
        self._pending_song_id = None
        self._clear_now_playing()
        self.status_message.emit(f"Playback error: {description}")

    # ── user actions ───────────────────────────────────────────────────────
    def _play_selected_row(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._rows):
            self.play_song(self._rows[row])

    def _on_volume(self, value: int) -> None:
        self._engine.set_volume(value)
        self._settings.music.volume = int(value)
        self.settings_changed.emit()

    def _on_device_changed(self, index: int) -> None:
        outputs = PlaybackEngine.audio_outputs()
        if index < 0 or index >= len(outputs):
            return
        self._engine.set_output_device(outputs[index])
        self._settings.music.stream_device_id = devices.device_id(outputs[index])

        # A physical output cannot be heard by the producer and captured by OBS
        # separately, so the stream-only promise no longer holds.
        if self._settings.music.stream_only and not self._selected_is_loopback():
            self._settings.music.stream_only = False
            self._sync_stream_only_controls()
            self.status_message.emit(
                "That output goes to your speakers, so 'Send to stream only' was "
                "turned off. Pick a virtual device (marked · virtual) for it."
            )

        if not self._engine.monitor_enabled:
            self._monitor.setChecked(False)
        self._sync_monitor_button(self._engine.monitor_enabled)
        self.settings_changed.emit()

    def _on_stream_only_toggled(self, enabled: bool) -> None:
        if enabled:
            output_index = self._loopback_index()
            if output_index is None:
                self._settings.music.stream_only = False
                self._sync_stream_only_controls()
                self.status_message.emit(
                    "No virtual audio device found. Install VB-Cable (or use "
                    "Voicemod / NVIDIA Broadcast) so OBS can capture the music "
                    "without playing it on your speakers."
                )
                return
            self._device.setCurrentIndex(output_index)  # routes audio + persists the device
            self._engine.set_monitor_enabled(False)
            self._settings.music.monitor_enabled = False
            self._sync_monitor_button(False)
            self._settings.music.stream_only = True
            self._sync_stream_only_controls()
            output = PlaybackEngine.audio_outputs()
            name = (
                output[self._device.currentIndex()].description()
                if 0 <= self._device.currentIndex() < len(output)
                else "the virtual device"
            )
            self.status_message.emit(
                f"Music goes to {name} only — you won't hear it. In OBS, capture "
                "that device with Audio Output Capture."
            )
        else:
            self._settings.music.stream_only = False
            self._sync_stream_only_controls()
            self.status_message.emit(
                "Stream-only off. Enable Monitor if you want to hear the music too."
            )
        self.settings_changed.emit()

    def _on_monitor_toggled(self, enabled: bool) -> None:
        if self._settings.music.stream_only:
            self._sync_monitor_button(False)
            return
        if not self._engine.set_monitor_enabled(enabled):
            self._monitor.blockSignals(True)
            self._monitor.setChecked(False)
            self._monitor.blockSignals(False)
            self.status_message.emit(
                "Choose a separate OBS output (for example a virtual cable) to "
                "monitor independently on your speakers."
            )
            return
        self._settings.music.monitor_enabled = enabled
        self._sync_monitor_button(enabled)
        self.settings_changed.emit()

    def _sync_monitor_button(self, enabled: bool) -> None:
        self._monitor.blockSignals(True)
        self._monitor.setChecked(enabled)
        self._monitor.setText("🔊  Monitor: ON" if enabled else "🔇  Monitor: OFF")
        self._monitor.blockSignals(False)

    def _new_stream_session(self) -> None:
        result = QMessageBox.question(
            self,
            "New stream session",
            "Start a new stream? This resets the play history and repeat "
            "cooldowns, but keeps all votes.",
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        self.end_break()
        self._library.new_stream_session()
        self.refresh_all()
        self.status_message.emit(f"Started stream session #{self._library.session_id}")

    def _open_media_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        folder = self._media_root
        if not folder.exists():
            self.status_message.emit(f"Media folder not found: {folder}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
