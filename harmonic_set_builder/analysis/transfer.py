"""Move analysis results between machines (e.g. analyzed on a desktop GPU, used on
the laptop): one gzipped JSON file with each track's analysis and every cached
model output.

Tracks are matched by path (the desktop script makes the laptop's paths resolve
there; ``path_map`` rewrites prefixes otherwise). File timestamps aren't comparable
across machines and file systems, so results are imported keyed by file size and
take on this machine's file signature the first time the file is seen.
"""
from __future__ import annotations

import gzip
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .store import AnalysisStore, TrackAnalysis, size_hash

FORMAT = 1


def export(store: AnalysisStore, out: Path, paths: Optional[Iterable[str]] = None) -> int:
    """Write every analyzed track (or just ``paths``) whose file is reachable. Returns the count."""
    wanted = list(paths) if paths is not None else list(store.index())
    tracks = []
    for p in wanted:
        a = store.get(p)
        if a is None:
            continue
        try:
            size = os.stat(p).st_size
        except OSError:
            continue
        raw = {}
        for backend in store.raw_backends(p):
            data = store.raw_get(p, backend, a.file_hash)
            if data is not None:
                raw[backend] = data
        tracks.append({"path": p, "size": size, "analysis": a.to_dict(), "raw": raw})
    payload = {"format": FORMAT, "exported_at": datetime.now().isoformat(timespec="seconds"), "tracks": tracks}
    out = Path(out)
    tmp = out.with_name(out.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, out)
    return len(tracks)


@dataclass
class ImportResult:
    imported: int = 0
    kept_newer: int = 0  # this machine had a more recent analysis
    size_mismatch: list[str] = field(default_factory=list)  # file differs from the one analyzed


def _map(path: str, path_map: dict[str, str]) -> str:
    for src, dst in path_map.items():
        if path.startswith(src):
            return dst + path[len(src):]
    return path


def load(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != FORMAT:
        raise ValueError(f"unsupported analysis file (format {data.get('format')!r})")
    return data


def import_file(store: AnalysisStore, path: Path, path_map: Optional[dict[str, str]] = None,
                keep_newer: bool = True) -> ImportResult:
    data = load(path)
    res = ImportResult()
    for t in data["tracks"]:
        p = _map(t["path"], path_map or {})
        size = int(t["size"])
        try:
            local = os.stat(p).st_size
        except OSError:
            local = None  # drive not mounted: checked when the file is next seen
        if local is not None and local != size:
            res.size_mismatch.append(p)
            continue
        a = TrackAnalysis.from_dict(t["analysis"])
        a.track_path = p
        mine = store.get(p)
        if keep_newer and mine is not None and mine.analyzed_at > a.analyzed_at and \
                mine.analyzer.split("+")[0] == a.analyzer.split("+")[0]:
            res.kept_newer += 1
            continue
        a.file_hash = size_hash(size)
        store.save(a)
        for backend, raw in (t.get("raw") or {}).items():
            store.raw_put(p, backend, a.file_hash, raw)
        res.imported += 1
    return res
