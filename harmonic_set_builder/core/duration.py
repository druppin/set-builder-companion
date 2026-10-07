"""Estimated set length: track durations minus the overlap of each mix.

The overlap is set in bars (4 beats) at the incoming track's tempo, so a
16-bar blend is 30 s at 128 BPM and about 22 s at 174 BPM. It never exceeds
half of either track.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class Item:
    duration: Optional[float]  # seconds; None = unknown
    bpm: Optional[float]


@dataclass(frozen=True)
class SetDuration:
    back_to_back: float  # plain sum of known durations
    mixed: float  # with overlaps subtracted
    tracks: int
    unknown: int  # items without a duration (not counted)


def overlap_seconds(a: Item, b: Item, bars: float) -> float:
    if bars <= 0 or not a.duration or not b.duration:
        return 0.0
    bpm = b.bpm or a.bpm
    if not bpm:
        return 0.0
    return min(bars * 4 * 60.0 / bpm, a.duration / 2, b.duration / 2)


def estimate(items: Sequence[Item], overlap_bars: float = 16) -> SetDuration:
    known = [i for i in items if i.duration]
    total = sum(i.duration for i in known)
    overlap = sum(overlap_seconds(a, b, overlap_bars) for a, b in zip(known, known[1:]))
    return SetDuration(total, total - overlap, len(items), len(items) - len(known))


def fmt(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
