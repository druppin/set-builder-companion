"""Batch plumbing shared by the CLI and the Phrases view: build jobs from library
tracks, run them in worker processes, store the results."""
from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Iterable, Optional

from ..core.track import Track
from .pipeline import Job, worker_init
from .pipeline import analyze as pipeline_analyze  # noqa: F401 - picklable entry point for workers
from .store import AnalysisStore, TrackAnalysis, file_signature
from .structure import ALLIN1

AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".aiff", ".aif", ".ogg", ".opus", ".wma"}


def default_workers(backend: str) -> int:
    if backend == ALLIN1:
        return 1  # allin1 already uses every core and a lot of memory
    return max(1, min(4, (os.cpu_count() or 2) // 3))


def executor(workers: int) -> ProcessPoolExecutor:
    # spawn: never fork a process that has Qt (or a SQLite connection) loaded
    return ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"), initializer=worker_init)


def make_jobs(tracks: Iterable[Track], grids: dict[int, dict], store: AnalysisStore, backend: str,
              allin1_python: Optional[str] = None, force: bool = False, relabel: bool = False
              ) -> tuple[list[Job], list[tuple[Track, str]]]:
    """Jobs for tracks that need (re-)analysis, plus (track, reason) for those skipped.

    * new or changed file: analyze with ``backend``
    * older labeling rules, or ``relabel``: re-label from the cached output of the
      backend that made the result (seconds; allin1 isn't re-run, and an allin1
      result isn't replaced just because Built-in is selected)
    * ``force``: analyze from scratch with ``backend``
    """
    jobs, skipped = [], []
    for t in tracks:
        if not t.location or not os.path.isfile(t.location):
            skipped.append((t, "file not found (drive not mounted?)"))
            continue
        status = store.status(t.location)
        use, raw = backend, None
        if not force:
            if status == "current" and not relabel:
                skipped.append((t, "already analyzed"))
                continue
            if status in ("current", "outdated"):
                use = store.backend_of(t.location) or backend
            raw = store.raw_get(t.location, use, file_signature(t.location))
            if raw is None and use == ALLIN1 and status in ("current", "outdated"):
                skipped.append((t, "no cached allin1 output to re-label (re-analyze from scratch instead)"))
                continue
        jobs.append(Job(t.location, t.id, t.bpm, grids.get(t.id), use, allin1_python, raw))
    return jobs, skipped


def store_result(store: AnalysisStore, result: dict) -> TrackAnalysis:
    a = TrackAnalysis.from_dict(result["analysis"])
    store.save(a)
    store.raw_put(a.track_path, result["backend"], a.file_hash, result["raw"])
    return a
