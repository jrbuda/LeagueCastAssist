"""Non-visual audio engine: two QMediaPlayer outputs and a fade-out.

Output A is what OBS captures (typically a virtual cable); output B is the
producer's own monitor. The engine never touches widgets, so the Music page can
be re-parented into a detached window without interrupting playback.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaDevices, QMediaPlayer

LOGGER = logging.getLogger(__name__)

FADE_INTERVAL_MS = 50
FADE_STEPS = 30
DRIFT_TOLERANCE_MS = 350


class PlaybackEngine(QObject):
    position_changed = Signal(int, int)  # position_ms, duration_ms
    duration_changed = Signal(int)
    state_changed = Signal(str)  # playing | paused | stopped | fading
    track_finished = Signal()
    fade_finished = Signal()
    error_occurred = Signal(str)
    monitor_state_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._volume = 65
        self._monitor_enabled = False
        self._fading = False

        self._audio = QAudioOutput(self)
        self._audio.setVolume(self._volume / 100)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)

        self._monitor_audio = QAudioOutput(self)
        self._monitor_audio.setVolume(0)
        self._monitor_player = QMediaPlayer(self)
        self._monitor_player.setAudioOutput(self._monitor_audio)

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_error)

        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(FADE_INTERVAL_MS)
        self._fade_timer.timeout.connect(self._fade_step)

    # ── devices ────────────────────────────────────────────────────────────
    @staticmethod
    def audio_outputs() -> list[QAudioDevice]:
        return list(QMediaDevices.audioOutputs())

    @staticmethod
    def default_output() -> QAudioDevice:
        return QMediaDevices.defaultAudioOutput()

    def output_device(self) -> QAudioDevice:
        return self._audio.device()

    def set_output_device(self, device: QAudioDevice) -> None:
        if device.isNull():
            return
        self._audio.setDevice(device)
        self._apply_monitor_routing()

    def monitor_device(self) -> QAudioDevice:
        return self._monitor_audio.device()

    # ── volume / monitor ───────────────────────────────────────────────────
    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, value: int) -> None:
        self._volume = max(0, min(100, int(value)))
        if not self._fading:
            self._audio.setVolume(self._volume / 100)
        self._apply_monitor_routing()

    @property
    def monitor_enabled(self) -> bool:
        return self._monitor_enabled

    def set_monitor_enabled(self, enabled: bool) -> bool:
        """Returns False when monitoring would duplicate the stream output."""
        if enabled and self._same_device():
            self._monitor_enabled = False
            self._monitor_audio.setVolume(0)
            return False
        self._monitor_enabled = enabled
        self._apply_monitor_routing()
        return True

    def _same_device(self) -> bool:
        return self._audio.device().id() == self._monitor_audio.device().id()

    def _apply_monitor_routing(self) -> None:
        if self._monitor_enabled and self._same_device():
            self._monitor_enabled = False
        target = self._audio.volume() if self._monitor_enabled else 0.0
        self._monitor_audio.setVolume(target if not self._fading else self._monitor_audio.volume())
        self.monitor_state_changed.emit(self._monitor_enabled)

    # ── transport ──────────────────────────────────────────────────────────
    @property
    def state(self) -> str:
        if self._fading:
            return "fading"
        state = self._player.playbackState()
        if state == QMediaPlayer.PlayingState:
            return "playing"
        if state == QMediaPlayer.PausedState:
            return "paused"
        return "stopped"

    @property
    def is_playing(self) -> bool:
        return self.state == "playing"

    @property
    def position(self) -> int:
        return int(self._player.position())

    @property
    def duration(self) -> int:
        return int(self._player.duration())

    def play_path(self, path: Path) -> None:
        self._fade_timer.stop()
        self._fading = False
        self._audio.setVolume(self._volume / 100)
        self._apply_monitor_routing()

        self._player.stop()
        self._monitor_player.stop()
        url = QUrl.fromLocalFile(str(path.resolve()))
        self._player.setSource(url)
        self._monitor_player.setSource(url)
        self._player.play()
        self._monitor_player.play()

    def pause(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
            self._monitor_player.pause()

    def resume(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PausedState:
            self._player.play()
            self._monitor_player.play()

    def toggle_pause(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self.pause()
        elif self._player.playbackState() == QMediaPlayer.PausedState:
            self.resume()

    def stop(self, fade: bool = False) -> None:
        self._fade_timer.stop()
        if fade and self._player.playbackState() != QMediaPlayer.StoppedState:
            self._fading = True
            self.state_changed.emit("fading")
            self._fade_timer.start()
            return
        self._stop_now()
        self.fade_finished.emit()

    def _stop_now(self) -> None:
        self._fading = False
        self._audio.setVolume(self._volume / 100)
        self._player.stop()
        self.monitor_stop()
        self._player.setSource(QUrl())
        self._monitor_player.setSource(QUrl())
        self._apply_monitor_routing()
        self.state_changed.emit("stopped")

    def monitor_stop(self) -> None:
        self._monitor_player.stop()

    # ── signals from QMediaPlayer ──────────────────────────────────────────
    def _on_position(self, position: int) -> None:
        self.position_changed.emit(int(position), self.duration)
        if (
            self._monitor_player.playbackState() == QMediaPlayer.PlayingState
            and abs(self._monitor_player.position() - position) > DRIFT_TOLERANCE_MS
        ):
            self._monitor_player.setPosition(position)

    def _on_duration(self, duration: int) -> None:
        self.duration_changed.emit(int(duration))

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        if self._fading:
            return
        if state == QMediaPlayer.PlayingState:
            self.state_changed.emit("playing")
        elif state == QMediaPlayer.PausedState:
            self.state_changed.emit("paused")
        else:
            self.state_changed.emit("stopped")

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and not self._fading:
            self.monitor_stop()
            self.track_finished.emit()

    def _on_error(self, _error: QMediaPlayer.Error, description: str) -> None:
        self._fading = False
        self._fade_timer.stop()
        self.monitor_stop()
        self._audio.setVolume(self._volume / 100)
        LOGGER.warning("Music playback error: %s", description)
        self.error_occurred.emit(description or "Unknown playback error")

    # ── fade ───────────────────────────────────────────────────────────────
    def _fade_step(self) -> None:
        step = (self._volume / 100) / FADE_STEPS
        new_volume = max(0.0, self._audio.volume() - step)
        self._audio.setVolume(new_volume)
        if self._monitor_enabled:
            self._monitor_audio.setVolume(new_volume)
        if new_volume <= 0.001:
            self._fade_timer.stop()
            self._stop_now()
            self.fade_finished.emit()
