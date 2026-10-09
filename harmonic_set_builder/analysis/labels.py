"""Map raw segments to DJ sections and clean them up (spec §3.5).

allin1 is trained mostly on pop/band music, so its labels map loosely onto dance
music; the energy and kick checks below do most of the work. Thresholds are
module constants so they can be tuned after the validation run.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Optional

import numpy as np

from .energy import BarFeatures, build_signs, detect_build, kick_present
from .structure import RawStructure

INTRO, BUILD, DROP, BREAKDOWN, GROOVE, OUTRO = "Intro", "Build", "Drop", "Breakdown", "Groove", "Outro"
DJ_LABELS = (INTRO, BUILD, DROP, BREAKDOWN, GROOVE, OUTRO)
MODEL, DERIVED, MANUAL = "model", "derived", "manual"
# Bump when the labeling rules change: older results get re-labeled from the cached
# model output (fast; allin1 isn't re-run).
LABELS_VERSION = 4
LABELS_TAG = f"+labels{LABELS_VERSION}"

DROP_ENERGY = 0.55  # chorus at or above this mean energy is a Drop
BODY_DROP_ENERGY = 0.7  # verse/inst/solo this energetic with the kick in is a Drop too
# ...and either way only within DROP_MARGIN of the track's loudest section: a 0.8 groove
# isn't a drop in a track whose drops sit at 0.95.
DROP_MARGIN = 0.12
SILENT = 0.15  # a stretch this quiet is a breakdown, not (the start of) a build
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
    part: int = 1  # a phrase change inside the section (e.g. a 32-bar drop's second 16) starts part 2

    @property
    def name(self) -> str:
        base = f"{self.label} {self.number}" if self.repeated else self.label
        return f"{base} {chr(96 + self.part)}" if self.part > 1 else base

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


def _raw_bounds(raw: RawStructure, bars: np.ndarray, offset: int) -> list[tuple[int, str]]:
    """(start bar, raw label) per segment, snapped to the track's phrase grid."""
    out: list[tuple[int, str]] = []
    for s in _segments(raw):
        b = 0 if not out else snap_bar(s["start"], bars, offset)
        if out and b <= out[-1][0]:
            continue
        out.append((b, s["label"]))
    return out


def _map(label: str, e: float, kick: float, top: float = 0.0) -> str:
    """``top``: the track's loudest section energy (drops are judged relative to it)."""
    drop_level = max(DROP_ENERGY, top - DROP_MARGIN)
    body_level = max(BODY_DROP_ENERGY, top - DROP_MARGIN)
    if label == "intro":
        return INTRO
    if label == "outro":
        return OUTRO
    if label == "chorus":
        return DROP if e >= drop_level else GROOVE
    if label in ("break", "bridge"):
        return BREAKDOWN if e < BREAK_ENERGY or kick < 0.5 else GROOVE
    # verse / inst / solo (and start/end): energy and the kick decide
    if e >= body_level and kick >= 0.7:
        return DROP
    if kick < 0.4 and e < BREAK_ENERGY:
        return BREAKDOWN
    return GROOVE


def _edges_only(raw_labels: list[str], energy: list[float], kick: list[float], top: float = 0.0) -> list[str]:
    """allin1 often calls half a dance track "intro" (or "outro"). Only a leading run
    counts as intro, and it ends at the first drop-level segment; likewise outro
    only counts after the last one. Elsewhere those labels are treated as "inst"."""
    out = list(raw_labels)
    body = [_map("inst", e, k, top) for e, k in zip(energy, kick)]
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
    offset = raw_phrase_offset(raw, bars)
    bounds = _raw_bounds(raw, bars, offset) or [(0, "inst")]
    spans = [(a, (bounds[i + 1][0] if i + 1 < len(bounds) else n), lab) for i, (a, lab) in enumerate(bounds)]
    spans = [(a, b, lab) for a, b, lab in spans if b > a]
    seg_e = [float(f.energy[a:b].mean()) for a, b, _ in spans]
    seg_k = [float(kick[a:b].mean()) for a, b, _ in spans]
    # Second-loudest section: one peak section (a final drop) mustn't push real drops down.
    loud = sorted((e for (a, b, _), e in zip(spans, seg_e) if b - a >= MIN_BARS), reverse=True)
    top = loud[1] if len(loud) > 1 else (loud[0] if loud else 0.0)
    raw_labels = _edges_only([lab for _, _, lab in spans], seg_e, seg_k, top)
    labels = [_map(lab, e, k, top) for lab, e, k in zip(raw_labels, seg_e, seg_k)]
    sources = [source] * len(spans)

    # A big jump from a quieter section (breakdown, build, intro, quiet groove) with the kick
    # back in is a drop, even if it opens sparse and so averages below the loudest sections.
    for i in range(1, len(spans)):
        a, b, _ = spans[i]
        loud = float(np.percentile(f.energy[a:b], 75))  # a drop may open with a few sparse bars
        if labels[i] == GROOVE and labels[i - 1] != DROP and loud >= seg_e[i - 1] + 0.4 and seg_k[i] >= 0.6:
            labels[i] = DROP

    # Fake drops: the drop hits on the phrase line, pauses for a bar or two, then really
    # drops. The model marks the late hit; the drop starts on the phrase line.
    for i in range(1, len(spans)):
        a, b, lab = spans[i]
        k = (a - offset) % PHRASE
        g = a - k
        if labels[i] == DROP and k in (1, 2) and g - spans[i - 1][0] >= MIN_BARS and \
                f.energy[g] >= 0.75 * seg_e[i] and min(f.energy[g + 1:a]) < 0.6 * seg_e[i]:
            pa, _, plab = spans[i - 1]
            spans[i - 1], spans[i] = (pa, g, plab), (g, b, lab)
            seg_e[i - 1], seg_e[i] = float(f.energy[pa:g].mean()), float(f.energy[g:b].mean())

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
        if b - a > 8 and f.energy[a:a + (b - a) // 2].mean() < SILENT:
            continue  # breakdown, then (maybe) a build: found below, not the whole span
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
    # Merging same-label neighbours above keeps the labels clean, but the boundaries the
    # model found inside them (a long drop changing at its 16-bar phrase) are exactly where
    # DJs cue. Keep them as parts of the section.
    cuts = sorted({a for a, _, _ in spans})

    ends = list(bars[1:]) + [duration]
    counts: dict[str, int] = {}
    total = {lab: sum(1 for m in merged if m[2] == lab) for lab in DJ_LABELS}
    out = []
    for a, b, lab, src in merged:
        counts[lab] = counts.get(lab, 0) + 1
        for part, (pa, pb) in enumerate(_split(a, b, cuts if lab != BUILD else []), 1):
            out.append(Section(
                lab, counts[lab], pa, pb, 0.0 if pa == 0 else float(bars[pa]), float(ends[pb - 1]),
                round(float(f.energy[pa:pb].mean()), 4), src, total.get(lab, 0) > 1, part,
            ))
    return out


def _split(a: int, b: int, cuts: list[int]) -> list[tuple[int, int]]:
    """[a, b) split at the cuts inside it, never leaving a piece under MIN_BARS."""
    pieces, start = [], a
    for c in cuts:
        if start + MIN_BARS <= c <= b - MIN_BARS:
            pieces.append((start, c))
            start = c
    return pieces + [(start, b)]


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


def whole(sections: list[Section]) -> list[Section]:
    """Sections with their parts joined back together (one entry per Drop 1, Drop 2, …)."""
    out: list[Section] = []
    for s in sections:
        if out and s.part > 1 and out[-1].label == s.label and out[-1].number == s.number:
            p = out[-1]
            n_p, n_s = p.bars, s.bars
            energy = round((p.mean_energy * n_p + s.mean_energy * n_s) / max(n_p + n_s, 1), 4)
            out[-1] = replace(p, end_bar=s.end_bar, end_sec=s.end_sec, mean_energy=energy)
        else:
            out.append(replace(s, part=1))
    return out


def summary(sections: list[Section]) -> str:
    """Compact strip like 'I16 B8 D32 Br16 B8 D32 O16' (parts joined)."""
    short = {INTRO: "I", BUILD: "B", DROP: "D", BREAKDOWN: "Br", GROOVE: "G", OUTRO: "O"}
    return " ".join(f"{short.get(s.label, s.label[:1])}{s.bars}" for s in whole(sections))


def first(sections: list[Section], label: str) -> Optional[Section]:
    """The first whole section with this label (all its parts)."""
    return next((s for s in whole(sections) if s.label == label), None)


def last(sections: list[Section], label: str) -> Optional[Section]:
    return next((s for s in reversed(whole(sections)) if s.label == label), None)
