"""Relation between two tracks (move, mood, energy Δ, BPM Δ) and suggestion ordering."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from . import bpm as bpm_mod
from .camelot import CLASH, TIER_ORDER, Key, Move, classify, transpose
from .settings import Settings
from .track import Track


@dataclass(frozen=True)
class Relation:
    move: Optional[Move]  # None when a key is unknown
    bpm: Optional[bpm_mod.BpmDelta]  # None when a BPM is unknown
    effective_key: Optional[Key]  # incoming key after keylock-off pitch shift

    @property
    def tier(self) -> Optional[str]:
        return self.move.tier if self.move else None

    @property
    def is_clash(self) -> bool:
        return bool(self.move and self.move.tier == CLASH)

    @property
    def in_key(self) -> bool:
        return bool(self.move and self.move.tier != CLASH)


def effective_key(out: Track, inc: Track, s: Settings, delta: Optional[bpm_mod.BpmDelta] = None) -> Optional[Key]:
    if inc.key is None or s.keylock or not (out.has_bpm and inc.has_bpm):
        return inc.key
    factor = delta.factor if delta else 1.0
    return transpose(inc.key, bpm_mod.keylock_shift(out.bpm, inc.bpm, factor))


def relate(out: Optional[Track], inc: Track, s: Settings) -> Optional[Relation]:
    """How mixing ``out`` -> ``inc`` behaves. None when there is no reference."""
    if out is None:
        return None
    delta = bpm_mod.compare(out.bpm, inc.bpm, s.bpm)
    k2 = effective_key(out, inc, s, delta)
    move = classify(out.key, k2, s.moves(), s.energy_moves_in_key)
    return Relation(move, delta, k2)


def rank_key(rel: Optional[Relation], rating: int = 0) -> tuple:
    """Tier (Smooth > Energy > Clash), BPM band, smallest |Δbpm|, then rating."""
    if rel is None:
        return (9, 9, 1e9, -rating)
    tier = TIER_ORDER[rel.move.tier] if rel.move else 3
    band = bpm_mod.BAND_ORDER[rel.bpm.band] if rel.bpm else 3
    d = rel.bpm.abs_delta if rel.bpm else 1e9
    return (tier, band, d, -(rating or 0))


def rank(reference: Optional[Track], tracks: Iterable[Track], s: Settings) -> list[Track]:
    tracks = list(tracks)
    if reference is None:
        return tracks
    return sorted(tracks, key=lambda t: rank_key(relate(reference, t, s), t.rating))
