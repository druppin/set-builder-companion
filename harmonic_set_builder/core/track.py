"""Track record shared by the library, sets and the pure logic modules."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .camelot import Key

_ENERGY_TAG = re.compile(r"\benergy\s*[:=]?\s*(\d{1,2})\b", re.IGNORECASE)


@dataclass
class Track:
    id: int
    artist: str = ""
    title: str = ""
    album: str = ""
    album_artist: str = ""
    genre: str = ""
    composer: str = ""
    grouping: str = ""
    year: str = ""
    tracknumber: str = ""
    duration: float = 0.0
    bpm: Optional[float] = None
    key: Optional[Key] = None
    key_text: str = ""
    rating: int = 0
    timesplayed: int = 0
    last_played_at: str = ""
    comment: str = ""
    datetime_added: str = ""
    bitrate: int = 0
    filetype: str = ""
    color: Optional[int] = None
    location: str = ""

    @property
    def display(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title

    @property
    def has_key(self) -> bool:
        return self.key is not None

    @property
    def has_bpm(self) -> bool:
        return bool(self.bpm and self.bpm > 0)

    @property
    def mixable(self) -> bool:
        """Tracks without key or BPM stay visible but get no suggestions."""
        return self.has_key and self.has_bpm

    @property
    def energy_tag(self) -> Optional[int]:
        """Mixed In Key style "Energy 6" comment tag, if present."""
        m = _ENERGY_TAG.search(self.comment or "")
        return int(m.group(1)) if m else None
