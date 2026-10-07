"""Energy series derived from Camelot moves, with break segmentation at clashes.

Modeled on camelotwheel.org's Interactive Set Builder: energy is controlled by how
you move around the wheel. ``E[0]`` = baseline, ``E[i] = E[i-1] + move Δ``.
After a clash the line breaks; the next segment starts at the clashing track and
carries the previous level forward.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .camelot import Key, Move, classify
from .settings import Settings


@dataclass(frozen=True)
class EnergyPoint:
    value: float
    segment: int
    move: Optional[Move]  # move into this point (None for first/unknown)
    clash: bool
    unknown: bool  # key unknown on either side: level carried, not a clash


def series(
    keys: Sequence[Optional[Key]],
    s: Optional[Settings] = None,
    tags: Optional[Sequence[Optional[int]]] = None,
) -> list[EnergyPoint]:
    """Energy series for a plain key sequence (keylock assumed on)."""
    s = s or Settings()
    moves = s.moves()
    mvs = [None] + [
        classify(keys[i - 1], keys[i], moves, s.energy_moves_in_key) for i in range(1, len(keys))
    ]
    return series_from_moves(mvs, s, tags, [k is None for k in keys])


def series_from_moves(
    moves: Sequence[Optional[Move]],
    s: Optional[Settings] = None,
    tags: Optional[Sequence[Optional[int]]] = None,
    unknown: Optional[Sequence[bool]] = None,
) -> list[EnergyPoint]:
    """``moves[i]`` is the move into point i (``moves[0]`` is ignored)."""
    s = s or Settings()
    out: list[EnergyPoint] = []
    level: float = s.energy_baseline
    seg = 0
    for i, mv in enumerate(moves):
        tag = tags[i] if (s.energy_from_tags and tags is not None) else None
        unk = bool(unknown[i]) if unknown is not None else mv is None
        if i == 0:
            level = tag if tag is not None else s.energy_baseline
            out.append(EnergyPoint(level, seg, None, False, unk))
            continue
        clash = bool(mv and mv.is_clash)
        if clash:
            seg += 1
        if tag is not None:
            level = tag
        elif mv is not None and not clash and mv.energy is not None:
            level += mv.energy
        out.append(EnergyPoint(level, seg, mv, clash, unk or mv is None))
    return out
