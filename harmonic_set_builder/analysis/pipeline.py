"""Analyze one track end to end. Runs in a worker process (see ``ui/phrases.py``
and ``cli.py``); returns plain dicts so results cross process boundaries."""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np

from . import audio, grid as grid_mod, structure
from .energy import COMPONENTS, bar_features, beat_novelty, spectral
from .labels import LABELS_TAG, raw_phrase_offset, to_sections
from .store import TrackAnalysis, file_signature
from .structure import ALLIN1, BUILTIN, RawStructure


@dataclass
class Job:
    path: str
    track_id: Optional[int] = None
    bpm: Optional[float] = None  # from Mixxx
    grid: Optional[dict] = None  # MixxxGrid.to_dict()
    backend: str = BUILTIN
    allin1_python: Optional[str] = None
    raw: Optional[dict] = None  # cached raw output: relabel without re-running the model
    compare: tuple = ()  # also run these for comparison: "raveform", "cuedetr"
    extra_raw: Optional[dict] = None  # cached outputs of the comparison models
    cuedetr_python: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


LOW_PRIORITY = 10


def lower_priority() -> None:
    """Run at nice 10 (absolute: a process started from a raised-priority parent would
    otherwise stay above normal), so the desktop and any audio app stay smooth."""
    try:
        if os.getpriority(os.PRIO_PROCESS, 0) < LOW_PRIORITY:
            os.setpriority(os.PRIO_PROCESS, 0, LOW_PRIORITY)
    except (AttributeError, OSError):
        pass


def worker_init() -> None:
    """Process-pool initializer."""
    lower_priority()


def _detect_beats(sp, bpm: Optional[float], sr: int) -> np.ndarray:
    import librosa

    _, frames = librosa.beat.beat_track(onset_envelope=sp.onset_env, sr=sr, hop_length=512,
                                        start_bpm=bpm or 120.0, tightness=400 if bpm else 100)
    return sp.times[np.clip(frames, 0, len(sp.times) - 1)]


def analyze(job: Job) -> dict:
    """Returns {"analysis": TrackAnalysis dict, "raw": raw dict, "backend": str}."""
    sig = file_signature(job.path)
    y = audio.load(job.path)
    sr = audio.SR
    duration = len(y) / sr
    if duration < 10:
        raise ValueError("track is shorter than 10 seconds")
    sp = spectral(y, sr)
    del y

    raw: Optional[RawStructure] = RawStructure.from_dict(job.raw) if job.raw else None
    extra = dict(job.extra_raw or {})
    want_raveform = "raveform" in job.compare and "raveform" not in extra
    if (raw is None and job.backend == ALLIN1) or want_raveform:
        models = (["harmonix-all"] if raw is None and job.backend == ALLIN1 else []) + \
            (["raveform-fold3"] if want_raveform else [])
        got, _ = structure.run_allin1_models(job.path, job.allin1_python or "", models)
        if "harmonix-all" in got:
            raw = RawStructure.from_dict(got["harmonix-all"])
        if "raveform-fold3" in got:
            extra["raveform"] = got["raveform-fold3"]
    if "cuedetr" in job.compare and "cuedetr" not in extra:
        extra["cuedetr"] = structure.run_cuedetr(job.path, job.cuedetr_python or "")

    mgrid = grid_mod.MixxxGrid.from_dict(job.grid)
    beats = grid_mod.grid_beats(mgrid, duration, job.bpm)
    grid_src = "mixxx"
    if beats is None or len(beats) < 16:
        if raw and len(raw.beats) >= 16:
            beats, grid_src = np.asarray(raw.beats), "allin1"
        else:
            beats, grid_src = _detect_beats(sp, job.bpm, sr), "detected"
    beats = np.asarray(beats, dtype=float)
    if len(beats) < 8:
        raise ValueError("could not find a beat grid")

    if raw and raw.downbeats:
        phase = grid_mod.downbeat_phase([], hints=raw.downbeats, beats=beats)
    else:
        phase = grid_mod.downbeat_phase(beat_novelty(sp, beats, duration))
    bars = grid_mod.bar_starts(beats, phase)
    f = bar_features(sp, bars, duration)

    bpm = (mgrid.bpm if mgrid and mgrid.bpm else None) or job.bpm or float(60.0 / np.median(np.diff(beats)))
    if raw is None:
        raw = structure.builtin(f, bpm, beats, bars, duration)
    sections = to_sections(raw, f, duration)

    a = TrackAnalysis(
        track_path=job.path, file_hash=sig, analyzer=raw.analyzer + LABELS_TAG, mixxx_track_id=job.track_id,
        bpm=round(float(bpm), 3), first_downbeat=round(float(bars[0]), 4), duration=round(duration, 3),
        grid=grid_src, sections=sections, phrase_offset=raw_phrase_offset(raw, f.starts),
        bars=[{"bar": i, "start_sec": round(float(f.starts[i]), 4), "energy": round(float(f.energy[i]), 4),
               **{k: round(float(getattr(f, k)[i]), 4) for k in COMPONENTS},
               "low_db": round(float(f.low_raw[i]), 2)} for i in range(len(f))],
    )
    return {"analysis": a.to_dict(), "raw": raw.to_dict(), "backend": job.backend, "extra_raw": extra}
