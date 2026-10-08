"""Beat grids: decode Mixxx's stored grid, build beat/bar times, find the downbeat.

Mixxx stores the grid in ``library.beats`` as protobuf (``beats.proto``):

* ``BeatGrid-2.0``: ``BeatGrid { Bpm bpm = 1 { double bpm = 1 }; Beat first_beat = 2 { int32 frame_position = 1 } }``
* ``BeatMap-1.0``:  ``BeatMap { repeated Beat beat = 1 { int32 frame_position = 1; bool enabled = 2 } }``

Frame positions are at the file's sample rate (``library.samplerate``).
Using Mixxx's own grid keeps exported cues on Mixxx's beats.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

BEATS_PER_BAR = 4


@dataclass(frozen=True)
class MixxxGrid:
    """Either a constant grid (bpm + first beat) or an explicit beat list, in seconds."""

    bpm: Optional[float] = None
    first_beat: Optional[float] = None
    beats: Optional[tuple[float, ...]] = None

    def to_dict(self) -> dict:
        return {"bpm": self.bpm, "first_beat": self.first_beat, "beats": list(self.beats) if self.beats else None}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> Optional["MixxxGrid"]:
        if not d:
            return None
        return cls(d.get("bpm"), d.get("first_beat"), tuple(d["beats"]) if d.get("beats") else None)


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = out = 0
    while True:
        b = buf[i]
        i += 1
        out |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return out, i


def _fields(buf: bytes):
    """Yield (field number, wire type, value) of a protobuf message."""
    i = 0
    while i < len(buf):
        tag, i = _varint(buf, i)
        num, wt = tag >> 3, tag & 7
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 1:
            v = buf[i:i + 8]
            i += 8
        elif wt == 2:
            n, i = _varint(buf, i)
            v = buf[i:i + n]
            i += n
        elif wt == 5:
            v = buf[i:i + 4]
            i += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wt}")
        yield num, wt, v


def _int32(v: int) -> int:
    v &= 0xFFFFFFFF  # negative int32 values are sign-extended to 64 bits on the wire
    return v - (1 << 32) if v & 0x80000000 else v


def _beat_frame(msg: bytes) -> tuple[Optional[int], bool]:
    frame, enabled = None, True
    for num, wt, v in _fields(msg):
        if num == 1 and wt == 0:
            frame = _int32(v)
        elif num == 2 and wt == 0:
            enabled = bool(v)
    return frame, enabled


def decode_mixxx_beats(blob: Optional[bytes], version: Optional[str], samplerate: int) -> Optional[MixxxGrid]:
    if not blob or not samplerate:
        return None
    try:
        if version and version.startswith("BeatGrid"):
            bpm = first = None
            for num, wt, v in _fields(blob):
                if num == 1 and wt == 2:
                    for n2, w2, v2 in _fields(v):
                        if n2 == 1 and w2 == 1:
                            bpm = struct.unpack("<d", v2)[0]
                elif num == 2 and wt == 2:
                    frame, _ = _beat_frame(v)
                    first = frame / samplerate if frame is not None else 0.0
            if bpm and bpm > 0:
                return MixxxGrid(bpm=bpm, first_beat=first or 0.0)
        elif version and version.startswith("BeatMap"):
            beats = []
            for num, wt, v in _fields(blob):
                if num == 1 and wt == 2:
                    frame, enabled = _beat_frame(v)
                    if frame is not None and enabled:
                        beats.append(frame / samplerate)
            if len(beats) >= 8:
                return MixxxGrid(beats=tuple(sorted(beats)))
    except (ValueError, IndexError, struct.error):
        return None
    return None


def beat_times(duration: float, bpm: float, first_beat: float = 0.0) -> np.ndarray:
    """Constant grid covering [0, duration): extends backwards from the first beat too."""
    period = 60.0 / bpm
    k0 = -int(np.floor(first_beat / period))
    k1 = int(np.ceil((duration - first_beat) / period))
    beats = first_beat + period * np.arange(k0, k1)
    return beats[(beats >= -1e-6) & (beats < duration)]


def grid_beats(grid: Optional[MixxxGrid], duration: float, bpm: Optional[float]) -> Optional[np.ndarray]:
    """Beat times from a Mixxx grid, else from the BPM alone (phase found later), else None."""
    if grid and grid.beats:
        b = np.asarray(grid.beats, dtype=float)
        return b[(b >= 0) & (b < duration)]
    if grid and grid.bpm:
        return beat_times(duration, grid.bpm, grid.first_beat or 0.0)
    return None


def downbeat_phase(beat_novelty: Sequence[float], hints: Optional[Sequence[float]] = None,
                   beats: Optional[Sequence[float]] = None) -> int:
    """Which beat (0..3) starts the bars.

    With ``hints`` (another analyzer's downbeat times), pick the phase whose
    downbeats lie closest to them. Otherwise pick the phase where the music
    changes most: sections start on downbeats, so feature novelty lines up there.
    """
    if hints is not None and beats is not None and len(hints) and len(beats) >= BEATS_PER_BAR:
        b = np.asarray(beats)
        h = np.asarray(hints)
        idx = np.clip(np.searchsorted(b, h), 0, len(b) - 1)
        prev = np.clip(idx - 1, 0, len(b) - 1)
        nearest = np.where(np.abs(b[prev] - h) < np.abs(b[idx] - h), prev, idx)
        votes = np.bincount(nearest % BEATS_PER_BAR, minlength=BEATS_PER_BAR)
        return int(np.argmax(votes))
    nov = np.asarray(beat_novelty, dtype=float)
    if len(nov) < BEATS_PER_BAR * 2:
        return 0
    scores = [nov[p::BEATS_PER_BAR].sum() / max(len(nov[p::BEATS_PER_BAR]), 1) for p in range(BEATS_PER_BAR)]
    return int(np.argmax(scores))


def bar_starts(beats: np.ndarray, phase: int) -> np.ndarray:
    return np.asarray(beats)[phase::BEATS_PER_BAR]
