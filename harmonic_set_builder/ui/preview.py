"""Preview player: listen to a track from any table (▶ column, Space, or the
context menu). Uses Qt Multimedia, which ships with PySide6."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QStyle, QToolButton, QWidget

from ..core.camelot import format_key
from ..core.track import Track

SKIP_MS = 10_000


def _fmt(ms: int) -> str:
    s = max(ms, 0) // 1000
    return f"{s // 60}:{s % 60:02d}"


class SeekSlider(QSlider):
    """Jumps straight to the clicked position instead of paging."""

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            v = QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(ev.position().x()), self.width())
            self.setValue(v)
            self.sliderMoved.emit(v)
        super().mousePressEvent(ev)


class PreviewBar(QWidget):
    playingChanged = Signal(object)  # track id now playing, or None
    message = Signal(str)

    def __init__(self, volume: float = 0.8, parent=None):
        super().__init__(parent)
        self.track: Optional[Track] = None
        self.notation = "camelot"
        self.audio = QAudioOutput(self)
        self.audio.setVolume(volume)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(self._duration)
        self.player.playbackStateChanged.connect(self._state)
        self.player.errorOccurred.connect(lambda _e, text: self.message.emit(f"Preview failed: {text}"))
        self.player.mediaStatusChanged.connect(self._media_status)
        self._pending_ms: Optional[int] = None

        st = self.style()
        self.play_btn = QToolButton()
        self.play_btn.setIcon(st.standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.setToolTip("Play / pause preview (Space in a table)")
        self.play_btn.clicked.connect(self.toggle)
        self.stop_btn = QToolButton()
        self.stop_btn.setIcon(st.standardIcon(QStyle.SP_MediaStop))
        self.stop_btn.setToolTip("Stop preview")
        self.stop_btn.clicked.connect(self.stop)
        back = QToolButton(text="−10s")
        back.clicked.connect(lambda: self._skip(-SKIP_MS))
        fwd = QToolButton(text="+10s")
        fwd.clicked.connect(lambda: self._skip(SKIP_MS))
        self.cover = QLabel()
        self.cover.setFixedSize(44, 44)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet("background: #141414;")
        self.title = QLabel("Preview: pick a track and press ▶ or Space")
        self.title.setObjectName("hint")
        self.title.setMinimumWidth(220)
        self.slider = SeekSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.time = QLabel("0:00 / 0:00")
        self.time.setMinimumWidth(90)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(volume * 100))
        self.volume.setFixedWidth(90)
        self.volume.setToolTip("Preview volume")
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        for w in (self.play_btn, self.stop_btn, back, fwd, self.cover):
            lay.addWidget(w)
        lay.addWidget(self.title)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.time)
        lay.addWidget(QLabel("Vol"))
        lay.addWidget(self.volume)

    @property
    def playing_id(self) -> Optional[int]:
        if self.track and self.player.playbackState() == QMediaPlayer.PlayingState:
            return self.track.id
        return None

    def preview(self, track: Optional[Track]) -> None:
        """Play ``track``; the same track again toggles pause."""
        if track is None:
            return
        if self.track and self.track.id == track.id and self.player.source().isValid():
            self.toggle()
            return
        if not track.location or not os.path.isfile(track.location):
            self.message.emit(f"Can't preview “{track.title}”: file not found ({track.location}). "
                              "Is the drive mounted?")
            return
        self.track = track
        key = format_key(track.key, self.notation) if track.key else "?"
        bpm = f"{track.bpm:.1f}" if track.bpm else "?"
        who = f"{track.artist} – {track.title}" if track.artist else track.title
        self.title.setText(f"{who}   [{key}, {bpm} BPM]")
        self.title.setToolTip(track.location)
        self.player.setSource(QUrl.fromLocalFile(track.location))
        self.player.play()

    def preview_at(self, track: Optional[Track], seconds: float) -> None:
        """Play ``track`` from ``seconds`` (e.g. a section start)."""
        if track is None:
            return
        ms = int(seconds * 1000)
        if self.track and self.track.id == track.id and self.track.location == track.location \
                and self.player.source().isValid():
            self.player.setPosition(ms)
            self.player.play()
            return
        self._pending_ms = ms
        self.preview(track)
        if self.track is not track:
            self._pending_ms = None  # file not found

    def _media_status(self, status) -> None:
        if self._pending_ms is not None and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self.player.setPosition(self._pending_ms)
            self._pending_ms = None

    def set_cover(self, pixmap) -> None:
        self.cover.setPixmap(pixmap) if pixmap is not None else self.cover.clear()

    def toggle(self) -> None:
        if not self.player.source().isValid():
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self) -> None:
        self.player.stop()

    def _skip(self, ms: int) -> None:
        self.player.setPosition(max(0, min(self.player.duration(), self.player.position() + ms)))

    def _position(self, ms: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.time.setText(f"{_fmt(ms)} / {_fmt(self.player.duration())}")

    def _duration(self, ms: int) -> None:
        self.slider.setRange(0, ms)

    def _state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay))
        self.playingChanged.emit(self.playing_id)
