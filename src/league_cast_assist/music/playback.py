"""Non-visual audio engine: two QMediaPlayer outputs, perceptual volume, fades.

Output A is what OBS captures (typically a virtual cable); output B is the
producer's own monitor. The engine never touches widgets, so the Music page can
be re-parented into a detached window without interrupting playback.

Volume mapping
--------------
``QAudioOutput`` takes a linear amplitude, but loudness is perceived
logarithmically, so a linear slider is already loud near the bottom. The slider
position is therefore mapped through a selectable curve (``slider_to_gain``);
``linear`` reproduces the original behaviour for comparison during QA.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaDevices, QMediaPlayer

LOGGER = logging.getLogger(__name__)

FADE_INTERVAL_MS = 20
END_BREAK_FADE_MS = 1500
VOLUME_RAMP_MS = 100
DRIFT_TOLERANCE_MS = 350

CURVES = ("linear", "squared", "db40", "db60")


def slider_to_gain(percent: float, curve: str = "db40") -> float:
    """Map a 0-100 slider position to a QAudioOutput gain.

    ``db40``/``db60`` treat the slider as a dB attenuation (0 dB at 100 %), which
    is how the ear works; ``squared`` and ``linear`` are kept for A/B testing.
    """
    position = max(0.0, min(100.0, float(percent)))
    if position <= 0.0:
        return 0.0
    if curve == "linear":
        return position / 100.0
    if curve == "squared":
        return (position / 100.0) ** 2
    if curve == "db60":
        return 10 ** ((position - 100.0) * 0.03)
    return 10 ** ((position - 100.0) * 0.02)


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
        self._volume = 80
        self._curve = "db40"
        self._monitor_enabled = False
        self._fade_enabled = True
        self._fade_ms = 200

        # Fade bookkeeping. ``_announce_fading`` is only used by the End Break
        # fade so the UI can show "fading" without pause fades doing the same.
        self._fading = False
        self._announce_fading = False
        self._fade_from = 0.0
        self._fade_target = 0.0
        self._fade_steps = 1
        self._fade_index = 0
        self._fade_done: Callable[[], None] | None = None

        self._audio = QAudioOutput(self)
        self._audio.setVolume(self.gain)
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

        # Support-visible proof that the multimedia backend loaded (an empty
        # list usually means the FFmpeg plugin is missing from the package).
        LOGGER.info(
            "Playback engine ready: %d audio output(s), default=%s",
            len(QMediaDevices.audioOutputs()),
            QMediaDevices.defaultAudioOutput().description() or "none",
        )

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

    # ── volume / curve / fades ─────────────────────────────────────────────
    @property
    def volume(self) -> int:
        return self._volume

    @property
    def gain(self) -> float:
        """Target gain for the current slider position and curve."""
        return slider_to_gain(self._volume, self._curve)

    @property
    def volume_curve(self) -> str:
        return self._curve

    @property
    def fade_enabled(self) -> bool:
        return self._fade_enabled

    @property
    def fade_ms(self) -> int:
        return self._fade_ms

    def set_volume(self, value: int) -> None:
        self._volume = max(0, min(100, int(value)))
        if self._fading:
            self._apply_monitor_routing()
            return
        self._ramp_to(self.gain, VOLUME_RAMP_MS)

    def set_volume_curve(self, curve: str) -> None:
        self._curve = curve if curve in CURVES else "db40"
        if not self._fading:
            self._ramp_to(self.gain, VOLUME_RAMP_MS)

    def set_fade(self, enabled: bool, milliseconds: int) -> None:
        self._fade_enabled = bool(enabled)
        self._fade_ms = max(40, min(1000, int(milliseconds)))

    @property
    def monitor_enabled(self) -> bool:
        return self._monitor_enabled

    def set_monitor_enabled(self, enabled: bool) -> bool:
        """Returns False when monitoring would duplicate the stream output."""
        if enabled and self._same_device():
            self._monitor_enabled = False
            self._monitor_audio.setVolume(0)
            self.monitor_state_changed.emit(False)
            return False
        self._monitor_enabled = enabled
        self._apply_monitor_routing()
        return True

    def _same_device(self) -> bool:
        return self._audio.device().id() == self._monitor_audio.device().id()

    def _apply_monitor_routing(self) -> None:
        if self._monitor_enabled and self._same_device():
            self._monitor_enabled = False
        self._monitor_audio.setVolume(self._audio.volume() if self._monitor_enabled else 0.0)
        self.monitor_state_changed.emit(self._monitor_enabled)

    # ── transport ──────────────────────────────────────────────────────────
    @property
    def state(self) -> str:
        if self._announce_fading:
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
        self._cancel_fade()
        self._fade_timer.stop()
        self._announce_fading = False

        self._player.stop()
        self._monitor_player.stop()
        url = QUrl.fromLocalFile(str(path.resolve()))
        self._player.setSource(url)
        self._monitor_player.setSource(url)

        self._audio.setVolume(0.0 if self._fade_enabled else self.gain)
        self._apply_monitor_routing()
        self._player.play()
        self._monitor_player.play()
        if self._fade_enabled:
            self._start_fade(self.gain, self._fade_ms)

    def pause(self) -> None:
        if self._player.playbackState() != QMediaPlayer.PlayingState:
            return
        if not self._fade_enabled or self._fading:
            self._pause_now()
            return
        self._start_fade(0.0, self._fade_ms, self._pause_now)

    def _pause_now(self) -> None:
        self._player.pause()
        self._monitor_player.pause()
        self.state_changed.emit("paused")

    def resume(self) -> None:
        if self._player.playbackState() != QMediaPlayer.PausedState:
            return
        self._cancel_fade()
        self._audio.setVolume(0.0 if self._fade_enabled else self.gain)
        self._apply_monitor_routing()
        self._player.play()
        self._monitor_player.play()
        self.state_changed.emit("playing")
        if self._fade_enabled:
            self._start_fade(self.gain, self._fade_ms)

    def toggle_pause(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self.pause()
        elif self._player.playbackState() == QMediaPlayer.PausedState:
            self.resume()

    def stop(self, fade: bool = False) -> None:
        if fade and self._player.playbackState() != QMediaPlayer.StoppedState:
            self._cancel_fade()
            self._announce_fading = True
            self.state_changed.emit("fading")
            self._start_fade(0.0, END_BREAK_FADE_MS, self._finish_stop)
            return
        self._cancel_fade()
        self._stop_now()
        self.fade_finished.emit()

    def _finish_stop(self) -> None:
        self._stop_now()
        self.fade_finished.emit()

    def _stop_now(self) -> None:
        self._fading = False
        self._announce_fading = False
        self._audio.setVolume(self.gain)
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
        if self._announce_fading:
            return
        if state == QMediaPlayer.PlayingState:
            self.state_changed.emit("playing")
        elif state == QMediaPlayer.PausedState:
            self.state_changed.emit("paused")
        else:
            self.state_changed.emit("stopped")

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and not self._announce_fading:
            self.monitor_stop()
            self.track_finished.emit()

    def _on_error(self, _error: QMediaPlayer.Error, description: str) -> None:
        self._cancel_fade()
        self._announce_fading = False
        self.monitor_stop()
        self._audio.setVolume(self.gain)
        LOGGER.warning("Music playback error: %s", description)
        self.error_occurred.emit(description or "Unknown playback error")

    # ── fade machinery ─────────────────────────────────────────────────────
    def _ramp_to(self, target: float, duration_ms: int) -> None:
        if self._audio.volume() == target:
            self._apply_monitor_routing()
            return
        if not self._fade_enabled or duration_ms <= 0:
            self._cancel_fade()
            self._audio.setVolume(target)
            self._apply_monitor_routing()
            return
        self._start_fade(target, duration_ms)

    def _start_fade(
        self,
        target: float,
        duration_ms: int,
        done: Callable[[], None] | None = None,
    ) -> None:
        self._fade_timer.stop()
        self._fade_from = float(self._audio.volume())
        self._fade_target = max(0.0, min(1.0, target))
        self._fade_steps = max(1, int(max(1, duration_ms) / FADE_INTERVAL_MS))
        self._fade_index = 0
        self._fade_done = done
        self._fading = True
        self._fade_timer.start()

    def _cancel_fade(self) -> None:
        self._fade_timer.stop()
        self._fading = False
        self._fade_done = None

    def _fade_step(self) -> None:
        self._fade_index += 1
        progress = min(1.0, self._fade_index / self._fade_steps)
        gain = self._fade_from + (self._fade_target - self._fade_from) * progress
        self._audio.setVolume(gain)
        if self._monitor_enabled:
            self._monitor_audio.setVolume(gain)
        if progress < 1.0:
            return

        self._fade_timer.stop()
        self._fading = False
        self._audio.setVolume(self._fade_target)
        self._apply_monitor_routing()
        callback = self._fade_done
        self._fade_done = None
        if callback is not None:
            callback()
