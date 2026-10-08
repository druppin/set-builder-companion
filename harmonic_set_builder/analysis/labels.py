"""Map raw segments to DJ sections and clean them up (spec §3.5).

allin1 is trained mostly on pop/band music, so its labels map loosely onto dance
music; the energy and kick checks below do most of the work. Thresholds are
module constants so they can be tuned after the validation run.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np

from .energy import BarFeatures, build_signs, detect_build, kick_present
from .structure import RawStructure

INTRO, BUILD, DROP, BREAKDOWN, GROOVE, OUTRO = "Intro", "Build", "Drop", "Breakdown", "Groove", "Outro"
DJ_LABELS = (INTRO, BUILD, DROP, BREAKDOWN, GROOVE, OUTRO)
MODEL, DERIVED, MANUAL = "model", "derived", "manual"
# Bump when the labeling rules change: older results get re-labeled from the cached
# model output (fast; allin1 isn't re-run).
LABELS_VERSION = 2
LABELS_TAG = f"+labels{LABELS_VERSION}"

DROP_ENERGY = 0.55  # chorus at or above this mean energy is a Drop
BODY_DROP_ENERGY = 0.7  # verse/inst/solo this energetic with the kick in is a Drop too
BREAK_ENERGY = 0.5  # break/bridge below this (or without kick) is a Breakdown
EDGE_ENERGY = 0.6  # Groove before the first / after the last Drop below this joins Intro / Outro
PHRASE = 8  # bars; boundaries within SNAP_BARS of a multiple snap onto it
SNAP_BARS = 1
MIN_BARS = 4  # shorter sections (except builds) merge into a neighbour


@dataclass
class Section:
    label: str
    number: int
    start_bar: int
    end_bar: int
    start_sec: float
    end_sec: float
    mean_energy: float
    source: str = MODEL
    repeated: bool = False  # label occurs more than once in the track

    @property
    def name(self) -> str:
        return f"{self.label} {self.number}" if self.repeated else self.label

    @property
    def bars(self) -> int:
        return self.end_bar - self.start_bar

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Section":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def nearest_bar(t: float, bars: np.ndarray) -> int:
    return int(np.argmin(np.abs(bars - t)))


def phrase_offset(boundaries: list[int]) -> int:
    """Bar (0–7) on which the track's 8-bar phrases start, judged by where its
    sections change. Often 0, but a pickup bar before the first phrase makes it 1.
    Needs more votes than bar 0 to move off it."""
    pts = [b for b in boundaries if b > 0]
    votes = [sum(1 for b in pts if (b - o) % PHRASE == 0) for o in range(PHRASE)]
    best = max(range(PHRASE), key=lambda o: votes[o])
    return best if votes[best] >= 2 and votes[best] > votes[0] else 0


def snap_bar(t: float, bars: np.ndarray, offset: int = 0) -> int:
    """Nearest downbeat, then onto the track's 8-bar phrase grid if that's within a bar."""
    b = nearest_bar(t, bars)
    p = int(round((b - offset) / PHRASE)) * PHRASE + offset
    return p if abs(p - b) <= SNAP_BARS and 0 <= p < len(bars) else b


def _segments(raw: RawStructure) -> list[dict]:
    """Segments with allin1's 'start'/'end' silence merged into its neighbour."""
    segs = sorted(raw.segments, key=lambda s: s["start"])
    out = []
    for i, s in enumerate(segs):
        if s["label"] == "start" and i + 1 < len(segs):
            continue  # the next segment starts at 0 instead
        if s["label"] == "end" and out:
            continue  # absorbed by the previous segment
        out.append(s)
    return out


def raw_phrase_offset(raw: RawStructure, bars: np.ndarray) -> int:
    return phrase_offset([nearest_bar(s["start"], bars) for s in _segments(raw)[1:]])


def _raw_bounds(raw: RawStructure, bars: np.ndarray) -> list[tuple[int, str]]:
    """(start bar, raw label) per segment, snapped to the track's phrase grid."""
    offset = raw_phrase_offset(raw, bars)
    out: list[tuple[int, str]] = []
    for s in _segments(raw):
        b = 0 if not out else snap_bar(s["start"], bars, offset)
        if out and b <= out[-1][0]:
            continue
        out.append((b, s["label"]))
    return out


def _map(label: str, e: float, kick: float) -> str:
    if label == "intro":
        return INTRO
    if label == "outro":
        return OUTRO
    if label == "chorus":
        return DROP if e >= DROP_ENERGY else GROOVE
    if label in ("break", "bridge"):
        return BREAKDOWN if e < BREAK_ENERGY or kick < 0.5 else GROOVE
    # verse / inst / solo (and start/end): energy and the kick decide
    if e >= BODY_DROP_ENERGY and kick >= 0.7:
        return DROP
    if kick < 0.4 and e < BREAK_ENERGY:
        return BREAKDOWN
    return GROOVE


def _edges_only(raw_labels: list[str], energy: list[float], kick: list[float]) -> list[str]:
    """allin1 often calls half a dance track "intro" (or "outro"). Only a leading run
    counts as intro, and it ends at the first drop-level segment; likewise outro
    only counts after the last one. Elsewhere those labels are treated as "inst"."""
    out = list(raw_labels)
    body = [_map("inst", e, k) for e, k in zip(energy, kick)]
    first_drop = next((i for i, b in enumerate(body) if b == DROP), len(out))
    last_drop = max((i for i, b in enumerate(body) if b == DROP), default=-1)
    for i, lab in enumerate(out):
        if lab == "intro" and (i >= first_drop or any(x not in ("intro", "start") for x in out[:i])):
            out[i] = "inst"
        elif lab == "outro" and (i <= last_drop or any(x not in ("outro", "end") for x in out[i + 1:])):
            out[i] = "inst"
    return out


def to_sections(raw: RawStructure, f: BarFeatures, duration: float, source: str = MODEL) -> list[Section]:
    n = len(f)
    if n == 0:
        return []
    bars = f.starts
    kick = kick_present(f).astype(float)
    bounds = _raw_bounds(raw, bars) or [(0, "inst")]
    spans = [(a, (bounds[i + 1][0] if i + 1 < len(bounds) else n), lab) for i, (a, lab) in enumerate(bounds)]
    spans = [(a, b, lab) for a, b, lab in spans if b > a]
    seg_e = [float(f.energy[a:b].mean()) for a, b, _ in spans]
    seg_k = [float(kick[a:b].mean()) for a, b, _ in spans]
    raw_labels = _edges_only([lab for _, _, lab in spans], seg_e, seg_k)
    labels = [_map(lab, e, k) for lab, e, k in zip(raw_labels, seg_e, seg_k)]
    sources = [source] * len(spans)

    # Groove at the edges, quieter than the body, is really intro/outro.
    drops = [i for i, lab in enumerate(labels) if lab in (DROP, BREAKDOWN)]
    if drops:
        for i in range(drops[0]):
            if labels[i] == GROOVE and f.energy[spans[i][0]:spans[i][1]].mean() < EDGE_ENERGY:
                labels[i] = INTRO
        last = max(i for i, lab in enumerate(labels) if lab == DROP) if DROP in labels else drops[-1]
        for i in range(len(labels) - 1, last, -1):
            quiet = f.energy[spans[i][0]:spans[i][1]].mean() < EDGE_ENERGY
            # whatever ends the track after its last drop is the outro, kick or not
            if quiet and (labels[i] == GROOVE or (labels[i] == BREAKDOWN and i == len(labels) - 1)):
                labels[i] = OUTRO
            else:
                break

    # A short, quieter section right before a drop that shows build signs is the build.
    # (Neither analyzer labels builds; the boundary in front of the drop is the clue.)
    for i in range(len(spans) - 1):
        a, b, _ = spans[i]
        if labels[i + 1] != DROP or labels[i] in (DROP, BUILD) or not 4 <= b - a <= 16:
            continue
        drop_e = float(f.energy[b:spans[i + 1][1]].mean())
        if seg_e[i] > drop_e - 0.25:
            continue
        signs = build_signs(f, a, b)
        rising = signs["onsets"] or signs["high"] or signs["centroid"]
        # Something must rise (a flat kickless stretch is a breakdown). At the very
        # start, a kick intro adding hats rises too, so there the kick/bass must also drop out.
        if rising and (i > 0 or signs["low_out"]):
            labels[i], sources[i] = BUILD, DERIVED

    # Merge neighbours with the same label, find builds, then merge again.
    runs = _merge([(a, b, labels[i], sources[i]) for i, (a, b, _) in enumerate(spans)])
    with_builds: list[tuple[int, int, str, str]] = []
    for i, (a, b, lab, src) in enumerate(runs):
        nxt = runs[i + 1][2] if i + 1 < len(runs) else None
        if nxt == DROP and lab not in (DROP, BUILD):
            s = detect_build(f, b, a)
            if s is not None and s <= a and lab == INTRO:
                s = None  # never relabel the whole intro: DJs mix on it
            if s is not None:
                if s > a:
                    with_builds.append((a, s, lab, src))
                with_builds.append((s, b, BUILD, DERIVED))
                continue
        with_builds.append((a, b, lab, src))
    merged = _absorb_short(_merge(with_builds))

    ends = list(bars[1:]) + [duration]
    counts: dict[str, int] = {}
    total = {lab: sum(1 for m in merged if m[2] == lab) for lab in DJ_LABELS}
    out = []
    for a, b, lab, src in merged:
        counts[lab] = counts.get(lab, 0) + 1
        out.append(Section(
            lab, counts[lab], a, b, 0.0 if a == 0 else float(bars[a]), float(ends[b - 1]),
            round(float(f.energy[a:b].mean()), 4), src, total.get(lab, 0) > 1,
        ))
    return out


def _merge(spans: list[tuple[int, int, str, str]]) -> list[tuple[int, int, str, str]]:
    out: list[list] = []
    for a, b, lab, src in spans:
        if out and out[-1][2] == lab:
            out[-1][1] = b
            if src != out[-1][3]:
                out[-1][3] = DERIVED if DERIVED in (src, out[-1][3]) else src
        else:
            out.append([a, b, lab, src])
    return [tuple(x) for x in out]


def _absorb_short(spans: list[tuple[int, int, str, str]]) -> list[tuple[int, int, str, str]]:
    """Sections under MIN_BARS (except builds) join their neighbour: a 3-bar "outro"
    of trailing silence isn't something to mix on."""
    out = list(spans)
    changed = True
    while changed and len(out) > 1:
        changed = False
        for i, (a, b, lab, src) in enumerate(out):
            if b - a < MIN_BARS and lab != BUILD:
                if i > 0:
                    pa, _, plab, psrc = out[i - 1]
                    out[i - 1] = (pa, b, plab, psrc)
                else:
                    _, nb, nlab, nsrc = out[1]
                    out[1] = (a, nb, nlab, nsrc)
                del out[i]
                changed = True
                break
    return _merge(out)


def summary(sections: list[Section]) -> str:
    """Compact strip like 'I16 B8 D32 Br16 B8 D32 O16'."""
    short = {INTRO: "I", BUILD: "B", DROP: "D", BREAKDOWN: "Br", GROOVE: "G", OUTRO: "O"}
    return " ".join(f"{short.get(s.label, s.label[:1])}{s.bars}" for s in sections)


def first(sections: list[Section], label: str) -> Optional[Section]:
    return next((s for s in sections if s.label == label), None)


def last(sections: list[Section], label: str) -> Optional[Section]:
    return next((s for s in reversed(sections) if s.label == label), None)
