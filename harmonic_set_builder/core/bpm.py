"""BPM distance, bands, half/double-time matching and keylock shift."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

SAFE, CAUTION, DANGER = "safe", "caution", "danger"
BAND_ORDER = {SAFE: 0, CAUTION: 1, DANGER: 2}
BAND_NAMES = {SAFE: "Safe", CAUTION: "Caution", DANGER: "Danger"}


@dataclass
class BpmSettings:
    safe: float = 3.0
    caution: float = 6.0
    percent: bool = False  # judge |Δ| as percent of the outgoing tempo
    half_double: bool = True


@dataclass(frozen=True)
class BpmDelta:
    delta: float  # matched incoming tempo minus outgoing tempo (BPM)
    factor: float  # 1, 2 (incoming played double: it is half-time) or 0.5
    band: str

    @property
    def label(self) -> str:
        if self.factor == 2:
            return "half-time"
        if self.factor == 0.5:
            return "double-time"
        return ""

    @property
    def abs_delta(self) -> float:
        return abs(self.delta)


def band_for(abs_delta: float, bpm_out: float, s: BpmSettings) -> str:
    value = abs_delta / bpm_out * 100 if s.percent else abs_delta
    # Small epsilon so 3.0000001 from float maths still counts as the edge.
    if value <= s.safe + 1e-9:
        return SAFE
    if value <= s.caution + 1e-9:
        return CAUTION
    return DANGER


def compare(bpm_out: Optional[float], bpm_in: Optional[float], s: Optional[BpmSettings] = None) -> Optional[BpmDelta]:
    """Compare incoming against outgoing tempo. None if either is unknown."""
    s = s or BpmSettings()
    if not bpm_out or not bpm_in or bpm_out <= 0 or bpm_in <= 0:
        return None
    factors = (1.0, 2.0, 0.5) if s.half_double else (1.0,)
    best = None
    for f in factors:
        d = bpm_in * f - bpm_out
        if best is None or abs(d) < abs(best[0]) - 1e-9:
            best = (d, f)
    d, f = best
    return BpmDelta(round(d, 2), f, band_for(abs(d), bpm_out, s))


def keylock_shift(bpm_out: float, bpm_in: float, factor: float = 1.0) -> int:
    """Semitones the incoming track moves when tempo-matched with keylock off."""
    if not bpm_out or not bpm_in:
        return 0
    return round(12 * math.log2(bpm_out / (bpm_in * factor)))
