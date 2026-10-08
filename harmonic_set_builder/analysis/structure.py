"""Section detection backends. Both return the same raw shape (allin1's label
vocabulary) so ``labels.py`` maps them to DJ labels the same way.

* ``builtin``: self-similarity novelty on bar-synchronous features, tuned for
  dance music (sections are defined by what drops in and out).
* ``allin1``: the All-In-One Music Structure Analyzer, run in its own Python
  environment through ``allin1_worker.py``.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from .energy import BarFeatures, kick_present

BUILTIN = "builtin"
ALLIN1 = "allin1"
BUILTIN_VERSION = "builtin==1"
ALLIN1_LABELS = ("start", "end", "intro", "outro", "break", "bridge", "inst", "solo", "verse", "chorus")
WORKER = Path(__file__).with_name("allin1_worker.py")


@dataclass
class RawStructure:
    analyzer: str
    bpm: Optional[float] = None
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)  # {"start", "end", "label"} in seconds

    def to_dict(self) -> dict:
        return {"analyzer": self.analyzer, "bpm": self.bpm, "beats": self.beats, "downbeats": self.downbeats,
                "segments": self.segments}

    @classmethod
    def from_dict(cls, d: dict) -> "RawStructure":
        return cls(d["analyzer"], d.get("bpm"), list(d.get("beats") or []), list(d.get("downbeats") or []),
                   list(d.get("segments") or []))


# ----------------------------------------------------------------- builtin
def novelty(m: np.ndarray, half: int = 4) -> np.ndarray:
    """Foote novelty: a checkerboard kernel slid along the bar self-similarity
    matrix. ``out[b]`` scores a section change at the start of bar b."""
    n = len(m)
    if n < 2:
        return np.zeros(n)
    norm = np.linalg.norm(m, axis=1, keepdims=True)
    norm[norm == 0] = 1
    u = m / norm
    ssm = u @ u.T
    k = np.arange(-half, half)
    sign = np.where(k < 0, -1.0, 1.0)
    g = np.exp(-((k + 0.5) / (half * 0.6)) ** 2)
    kernel = np.outer(sign * g, sign * g)
    pad = np.pad(ssm, half, mode="edge")
    out = np.zeros(n)
    for b in range(1, n):
        win = pad[b:b + 2 * half, b:b + 2 * half]
        out[b] = (win * kernel).sum()
    out[out < 0] = 0
    return out / (out.max() or 1)


def pick_boundaries(score: np.ndarray, min_gap: int = 4) -> list[int]:
    """Peaks of ``score`` above an adaptive threshold, at least ``min_gap`` bars apart.
    Bars on the 8-bar phrase grid get a bonus: that's where dance tracks change."""
    n = len(score)
    if n < 2 * min_gap:
        return []
    # The phrase grid may start a bar or more after the first downbeat (pickup bars):
    # put the bonus where the strongest changes line up.
    sums = [score[o::8].sum() for o in range(8)]
    off = int(np.argmax(sums)) if max(sums) > 1.2 * sums[0] else 0
    bonus = np.array([0.15 if (b - off) % 8 == 0 else 0.05 if (b - off) % 4 == 0 else 0.0 for b in range(n)])
    s = score + bonus * (score > 0.05)
    thresh = np.median(s) + 0.5 * s.std()
    cands = [b for b in range(min_gap, n - min_gap // 2)
             if s[b] >= thresh and s[b] == s[max(0, b - 2):b + 3].max()]
    chosen: list[int] = []
    for b in sorted(cands, key=lambda b: -s[b]):
        if all(abs(b - c) >= min_gap for c in chosen):
            chosen.append(b)
    return sorted(chosen)


def builtin_segments(f: BarFeatures) -> list[tuple[int, int, str]]:
    """(start bar, end bar, raw label) using allin1's label words."""
    n = len(f)
    if n == 0:
        return []
    kick = kick_present(f).astype(float)
    nov = novelty(f.matrix(), half=4)
    # A kick/bass dropping in or out is the strongest section cue in dance music.
    flips = np.abs(np.diff(kick, prepend=kick[:1]))
    smooth_flip = np.convolve(flips, [0.5, 1, 0.5], mode="same")
    score = nov + 0.6 * smooth_flip
    bounds = [0] + pick_boundaries(score) + [n]
    segs = []
    seg_energy = [float(f.energy[a:b].mean()) for a, b in zip(bounds, bounds[1:])]
    hi = np.percentile(seg_energy, 70) if seg_energy else 1
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        e = seg_energy[i]
        k = kick[a:b].mean()
        if k < 0.4:
            label = "break"
        elif e >= hi and e >= 0.5:
            label = "chorus"
        else:
            label = "verse"
        segs.append((a, b, label))
    if len(segs) > 1:
        if segs[0][2] != "chorus" or seg_energy[0] < hi:
            segs[0] = (segs[0][0], segs[0][1], "intro")
        if segs[-1][2] != "chorus" or seg_energy[-1] < hi:
            segs[-1] = (segs[-1][0], segs[-1][1], "outro")
    return segs


def builtin(f: BarFeatures, bpm: Optional[float], beats: np.ndarray, bars: np.ndarray, duration: float) -> RawStructure:
    segs = builtin_segments(f)
    ends = list(bars[1:]) + [duration]
    return RawStructure(
        BUILTIN_VERSION, bpm, [float(b) for b in beats], [float(b) for b in bars],
        [{"start": float(bars[a]) if a else 0.0, "end": float(ends[b - 1]), "label": lab} for a, b, lab in segs],
    )


# ------------------------------------------------------------------ allin1
class Allin1Error(RuntimeError):
    pass


def allin1_available(python: Optional[str]) -> tuple[bool, str]:
    if not python or not Path(python).is_file():
        return False, "allin1 Python not set (Settings → Phrase analysis)"
    r = subprocess.run([python, "-c", "import allin1, natten, madmom; print('ok')"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or "ok" not in r.stdout:
        return False, (r.stderr.strip().splitlines() or ["allin1 import failed"])[-1]
    return True, "allin1 ready"


def run_allin1(path: str, python: str, timeout: float = 1800) -> RawStructure:
    """Analyze one file in the external allin1 environment."""
    with tempfile.TemporaryDirectory(prefix="hsb-allin1-") as work:
        out = os.path.join(work, "result.json")
        try:
            r = subprocess.run([python, str(WORKER), path, out, work], capture_output=True, text=True,
                               timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise Allin1Error(f"allin1 timed out after {timeout:.0f} s") from e
        if r.returncode != 0 or not os.path.isfile(out):
            lines = [x for x in r.stderr.strip().splitlines() if "Warning" not in x and "@custom" not in x]
            raise Allin1Error(lines[-1] if lines else f"allin1 failed (exit {r.returncode})")
        with open(out, encoding="utf-8") as fh:
            return RawStructure.from_dict(json.load(fh))
