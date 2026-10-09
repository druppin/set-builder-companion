"""What each analyzer predicted for one track, for side-by-side viewing (the Phrases
view's "Show:" picker). Works from what's stored: the saved analysis (for its
per-bar features) and each model's cached raw output, so no audio is needed."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .benchmark import cluster_candidates
from .energy import from_bars
from .labels import Section, to_sections
from .store import TrackAnalysis
from .structure import RawStructure

SAVED = "saved"
MODEL_NAMES = {"allin1": "allin1 pop model", "raveform": "allin1 Raveform v1 (EDM)", "builtin": "Built-in"}
CUEDETR = "cuedetr"


@dataclass
class MethodView:
    key: str
    title: str
    sections: list[Section] = field(default_factory=list)
    points: list[float] = field(default_factory=list)  # cue positions (CUE-DETR)
    note: str = ""


def available(raw_backends: set[str], has_analysis: bool) -> list[tuple[str, str]]:
    """(key, title) of everything that can be shown for a track."""
    out = [(SAVED, "Saved analysis")] if has_analysis else []
    for b, name in MODEL_NAMES.items():
        if b in raw_backends:
            if has_analysis:
                out.append((f"{b}:rules", f"{name} + my rules"))
            out.append((f"{b}:raw", f"{name}, raw output"))
    if CUEDETR in raw_backends:
        out.append((CUEDETR, "CUE-DETR cue points"))
    return out


def _raw_sections(raw: dict, a: Optional[TrackAnalysis]) -> list[Section]:
    """The model's own segments and labels, unprocessed."""
    times = np.asarray(a.bar_times, dtype=float) if a and a.bars else None
    energy = np.asarray([b["energy"] for b in a.bars], dtype=float) if a and a.bars else None
    out = []
    segs = [s for s in raw.get("segments", []) if s["end"] - s["start"] >= 1.0]  # drop lead-in/out slivers
    for i, s in enumerate(segs):
        sb = int(np.argmin(np.abs(times - s["start"]))) if times is not None else 0
        eb = int(np.argmin(np.abs(times - s["end"]))) if times is not None else 0
        e = float(energy[sb:max(eb, sb + 1)].mean()) if energy is not None else 0.0
        probs = s.get("probs")
        label = s["label"]
        if probs:  # unnamed labels (Raveform): show how sure the model was
            label = f"{label} ({100 * max(probs):.0f}%)"
        out.append(Section(label, i + 1, sb, max(eb, sb), float(s["start"]), float(s["end"]), round(e, 3), "model"))
    return out


def build(key: str, a: Optional[TrackAnalysis], raw: Optional[dict]) -> MethodView:
    if key == SAVED:
        return MethodView(SAVED, "Saved analysis", list(a.sections) if a else [])
    if key == CUEDETR:
        pts = cluster_candidates(raw.get("candidates", [])) if raw else []
        return MethodView(key, "CUE-DETR cue points", points=pts,
                          note="Cue positions only (clustered candidates); CUE-DETR doesn't label sections.")
    backend, kind = key.split(":")
    title = f"{MODEL_NAMES.get(backend, backend)}" + (" + my rules" if kind == "rules" else ", raw output")
    if raw is None:
        return MethodView(key, title, note="No saved output for this model.")
    if kind == "raw":
        note = "The model's own labels (r0–r10 are Raveform's unnamed EDM labels)." if backend == "raveform" else \
            "The model's own segments and labels."
        return MethodView(key, title, _raw_sections(raw, a), note=note)
    if a is None or not a.bars:
        return MethodView(key, title, note="Needs a saved analysis of this track for its bar features.")
    secs = to_sections(RawStructure.from_dict(raw), from_bars(a.bars, a.duration), a.duration)
    return MethodView(key, title, secs, note="Labeled by the app's rules, snapped to Mixxx's beat grid.")
