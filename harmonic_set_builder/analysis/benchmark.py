"""Score section/cue predictions against reference positions (spec milestone 1).

The reference is wherever the DJ actually put cues: their hand-placed Mixxx hot
cues (and, later, corrected sections). Recall is the headline number: how many
of *your* cue positions a method finds. Precision is shown too, but a method can
rightly mark sections you never cued, so it's a weaker signal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

MIN_REF_SEC = 2.0  # a cue at the very start isn't something to detect


@dataclass
class Match:
    refs: int
    preds: int
    hits: int  # references with a prediction within tolerance
    used: int  # predictions within tolerance of some reference
    errors: list[float] = field(default_factory=list)  # |offset| of each hit, seconds


def match(refs: Sequence[float], preds: Sequence[float], tol: float) -> Match:
    refs = sorted(r for r in refs if r >= MIN_REF_SEC)
    preds = sorted(p for p in preds if p >= MIN_REF_SEC - tol)
    p = np.asarray(preds, dtype=float)
    hits, errors = 0, []
    for r in refs:
        if len(p):
            d = np.min(np.abs(p - r))
            if d <= tol:
                hits += 1
                errors.append(float(d))
    r_arr = np.asarray(refs, dtype=float)
    used = sum(1 for x in preds if len(r_arr) and np.min(np.abs(r_arr - x)) <= tol)
    return Match(len(refs), len(preds), hits, used, errors)


def cluster_candidates(cands: Sequence[Sequence[float]], gap: float = 0.5, min_score: float = 0.6,
                       min_count: int = 3) -> list[float]:
    """CUE-DETR scores many overlapping windows, so each real cue shows up as a tight
    clump of candidates. Merge candidates closer than ``gap`` seconds and keep clumps
    whose best score reaches ``min_score`` and that at least ``min_count`` windows agree on."""
    pts = sorted((float(t), float(s)) for t, s in cands if s >= min_score * 0.8)
    out, cur = [], []
    for t, s in pts:
        if cur and t - cur[-1][0] > gap:
            out.append(cur)
            cur = []
        cur.append((t, s))
    if cur:
        out.append(cur)
    times = []
    for c in out:
        best = max(s for _, s in c)
        if best >= min_score and len(c) >= min_count:
            w = sum(s for _, s in c)
            times.append(sum(t * s for t, s in c) / w)
    return times


@dataclass
class Score:
    method: str
    tracks: int
    refs: int
    preds: int
    recall_half_sec: float
    recall_bar: float
    precision_half_sec: float
    median_error: float  # seconds, of hits within a bar

    def row(self) -> list[str]:
        return [self.method, f"{100 * self.recall_half_sec:.0f}%", f"{100 * self.recall_bar:.0f}%",
                f"{100 * self.precision_half_sec:.0f}%", f"{self.preds / max(self.tracks, 1):.1f}",
                f"{self.median_error:.2f}s" if self.median_error == self.median_error else "–"]


HEADER = ["Method", "Your cues found (±0.5 s)", "(±1 bar)", "Predictions near a cue", "Predictions/track",
          "Median offset"]


def score(method: str, items: Sequence[tuple[Sequence[float], Sequence[float], float]]) -> Score:
    """``items``: (reference times, predicted times, bar length in seconds) per track."""
    half = [match(r, p, 0.5) for r, p, _ in items]
    bar = [match(r, p, b) for r, p, b in items]
    refs = sum(m.refs for m in half)
    preds = sum(m.preds for m in half)
    errs = [e for m in bar for e in m.errors]
    return Score(method, len(items), refs, preds,
                 sum(m.hits for m in half) / refs if refs else 0.0,
                 sum(m.hits for m in bar) / refs if refs else 0.0,
                 sum(m.used for m in half) / preds if preds else 0.0,
                 float(np.median(errs)) if errs else float("nan"))


def table(scores: Sequence[Score]) -> str:
    rows = [HEADER] + [s.row() for s in scores]
    widths = [max(len(r[i]) for r in rows) for i in range(len(HEADER))]
    line = lambda r: "  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(r, widths)))  # noqa: E731
    return "\n".join([line(rows[0]), "  ".join("-" * w for w in widths)] + [line(r) for r in rows[1:]])
