"""Cover art loading off the GUI thread, with a memory cache and a small on-disk
cache of scaled PNGs (so the USB drive isn't re-read every launch)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

from ..core.track import Track
from ..data.covers import find_cover

CACHE_PX = 300  # stored size; thumbnails are scaled from this


class _Relay(QObject):
    done = Signal(int, object)  # track id, QImage | None


class _Job(QRunnable):
    def __init__(self, track: Track, cache_dir: Path, relay: _Relay):
        super().__init__()
        self.t, self.dir, self.relay = track, cache_dir, relay

    def run(self):
        img = None
        try:
            img = self._load()
        finally:
            self.relay.done.emit(self.t.id, img)

    def _load(self) -> Optional[QImage]:
        t = self.t
        try:
            st = os.stat(t.location)
        except OSError:
            return None  # unmounted / moved: try again next time, don't cache "none"
        key = hashlib.sha1(f"{t.location}|{st.st_mtime_ns}|{st.st_size}|{t.cover_location}".encode()).hexdigest()
        png, none = self.dir / f"{key}.png", self.dir / f"{key}.none"
        if png.is_file():
            img = QImage(str(png))
            if not img.isNull():
                return img
        if none.is_file():
            return None
        data = find_cover(t.location, t.cover_type, t.cover_location)
        img = QImage.fromData(data) if data else QImage()
        if img.isNull():
            none.touch()
            return None
        img = img.scaled(CACHE_PX, CACHE_PX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img.save(str(png), "PNG")
        return img


class CoverCache(QObject):
    loaded = Signal(int)  # track id whose cover finished loading (found or not)

    def __init__(self, cache_dir: Path, parent=None):
        super().__init__(parent)
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)  # gentle on a USB drive
        self.images: dict[int, Optional[QImage]] = {}
        self.scaled: dict[tuple[int, int], QPixmap] = {}
        self.pending: set[int] = set()
        self.relay = _Relay()
        self.relay.done.connect(self._done)

    def pixmap(self, track: Optional[Track], size: int) -> Optional[QPixmap]:
        """Cover scaled to ``size`` px, or None (loading starts in the background)."""
        if track is None or not track.location:
            return None
        if track.id not in self.images:
            self._request(track)
            return None
        img = self.images[track.id]
        if img is None:
            return None
        key = (track.id, size)
        if key not in self.scaled:
            self.scaled[key] = QPixmap.fromImage(img.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        return self.scaled[key]

    def has_cover(self, track: Optional[Track]) -> bool:
        return bool(track and self.images.get(track.id) is not None)

    def _request(self, track: Track) -> None:
        if track.id in self.pending:
            return
        self.pending.add(track.id)
        self.pool.start(_Job(track, self.dir, self.relay))

    def _done(self, track_id: int, img) -> None:
        self.pending.discard(track_id)
        if img is not None or track_id not in self.images:
            self.images[track_id] = img
        self.loaded.emit(track_id)

    def clear_memory(self) -> None:
        """After a library refresh: ids may have changed, files may be mounted now."""
        self.images.clear()
        self.scaled.clear()
