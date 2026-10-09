"""``hsb``: phrase analysis from the command line (spec §7).

    hsb analyze [--path DIR | --mixxx-db PATH] [--force] [--relabel] [--limit N] [--backend builtin|allin1]
    hsb show TRACK
    hsb export-cues [--dry-run] [--tracks ...] [--replace-own] [--max-hotcues N]
    hsb validate --sample 20 [--out FILE.csv]
    hsb benchmark --sample 10 [--methods builtin,allin1,raveform,cuedetr]

Reads Mixxx's library through the same read-only snapshot as the app. Only
``export-cues`` without ``--dry-run`` writes to Mixxx, with every safety rule in
``data/mixxx_cues.py``.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from concurrent.futures import as_completed
from pathlib import Path
from typing import Optional

from .analysis import batch, benchmark, benchmark_report, labels, pipeline, structure, transfer
from .analysis.store import AnalysisStore, TrackAnalysis
from .analysis.structure import ALLIN1, BUILTIN, allin1_available
from .core.track import Track
from .data import mixxx_cues, mixxx_db
from .data.config import Config
from .data.paths import app_dirs, default_allin1_python, default_cuedetr_python


def _fmt(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:05.2f}"


class Env:
    def __init__(self, db_override: Optional[str] = None):
        self.config_dir, self.data_dir = app_dirs()
        self.config = Config(self.config_dir / "config.json")
        self.settings = self.config.settings
        self.store = AnalysisStore(self.data_dir / "analysis.sqlite")
        self.db_override = db_override
        self._lib: Optional[mixxx_db.Library] = None
        self.snapshot: Optional[Path] = None

    @property
    def db_path(self) -> Optional[Path]:
        return mixxx_db.locate(self.db_override or self.config.get("db_path"))

    @property
    def library(self) -> mixxx_db.Library:
        if self._lib is None:
            path = self.db_path
            if path is None:
                sys.exit("Mixxx library not found; pass --mixxx-db PATH.")
            self.snapshot = mixxx_db.snapshot(path, self.data_dir / "snapshot")
            self._lib = mixxx_db.load(self.snapshot)
        return self._lib

    def allin1_python(self) -> str:
        return self.settings.allin1_python or str(default_allin1_python(self.data_dir))

    def find(self, query: str) -> list[Track]:
        lib = self.library
        if os.path.exists(query):
            t = lib.by_location(os.path.abspath(query))
            return [t] if t else [Track(-1, title=Path(query).stem, location=os.path.abspath(query))]
        if query.isdigit() and int(query) in lib.tracks:
            return [lib.tracks[int(query)]]
        q = query.casefold()
        return [t for t in lib.all_tracks if q in f"{t.artist} - {t.title}".casefold()]


def _guard_mixxx(allow: bool) -> None:
    if mixxx_cues.mixxx_running() and not allow:
        sys.exit("Mixxx is running. Analysis is CPU-heavy and could cause audio dropouts; close Mixxx first "
                 "(or pass --allow-while-mixxx-runs).")


def run_analysis(env: Env, tracks: list[Track], backend: str, force: bool, relabel: bool, workers: int,
                 quiet: bool = False, compare: tuple = ()) -> dict[str, TrackAnalysis]:
    if backend == ALLIN1 and not relabel:
        ok, msg = allin1_available(env.allin1_python())
        if not ok:
            sys.exit(f"allin1 is not available: {msg}")
    grids = mixxx_db.load_grids(env.snapshot, [t.id for t in tracks]) if env.snapshot else {}
    jobs, skipped = batch.make_jobs(tracks, grids, env.store, backend, env.allin1_python(), force, relabel,
                                    compare, str(default_cuedetr_python(env.data_dir)))
    for t, why in skipped:
        if why != "already analyzed" and not quiet:
            print(f"skip  {t.display}: {why}")
    done: dict[str, TrackAnalysis] = {}
    if not jobs:
        return done
    workers = min(workers, len(jobs))
    print(f"Analyzing {len(jobs)} track(s) with {backend} using {workers} worker(s)…")
    with batch.executor(workers) as ex:
        futs = {ex.submit(batch.pipeline_analyze, j): j for j in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            j = futs[fut]
            try:
                a = batch.store_result(env.store, fut.result())
                done[a.track_path] = a
                print(f"[{i}/{len(jobs)}] {Path(j.path).name}: {labels.summary(a.sections)}")
            except Exception as e:  # noqa: BLE001 - report and carry on with the batch
                print(f"[{i}/{len(jobs)}] {Path(j.path).name}: FAILED ({e})")
    return done


def cmd_analyze(env: Env, a) -> int:
    _guard_mixxx(a.allow_while_mixxx_runs)
    if a.path:
        files = sorted(p for p in Path(a.path).rglob("*") if p.suffix.lower() in batch.AUDIO_EXTS)
        lib = env.library if env.db_path else None
        tracks = []
        for p in files:
            t = lib.by_location(str(p)) if lib else None
            tracks.append(t or Track(-1, title=p.stem, location=str(p)))
    else:
        tracks = sorted(env.library.all_tracks, key=lambda t: (t.artist.casefold(), t.title.casefold()))
    if a.limit:
        tracks = tracks[: a.limit]
    backend = a.backend or env.settings.analysis_backend
    workers = a.jobs or env.settings.analysis_workers or batch.default_workers(backend)
    compare = tuple(x for x in (a.compare or "").split(",") if x)
    run_analysis(env, tracks, backend, a.force, a.relabel, workers, compare=compare)
    return 0


def cmd_export_analysis(env: Env, a) -> int:
    n = transfer.export(env.store, Path(a.out))
    print(f"Exported {n} analyzed track(s) to {a.out}")
    return 0


def cmd_import_analysis(env: Env, a) -> int:
    path_map = dict(m.split("=", 1) for m in (a.map or []))
    r = transfer.import_file(env.store, Path(a.file), path_map)
    print(f"Imported {r.imported} track(s); kept {r.kept_newer} newer local result(s).")
    for p in r.size_mismatch[:20]:
        print(f"  skipped (file differs from the one analyzed): {p}")
    return 0


def _get_analysis(env: Env, t: Track) -> Optional[TrackAnalysis]:
    return env.store.get(t.location)


def cmd_show(env: Env, a) -> int:
    matches = env.find(a.track)
    if not matches:
        print("No track matches.")
        return 1
    for t in matches[:5]:
        an = _get_analysis(env, t)
        print(f"{t.display}  ({t.location})")
        if an is None:
            print("   not analyzed")
            continue
        print(f"   {an.analyzer}, grid from {an.grid}, {an.bpm} BPM, first downbeat {an.first_downbeat:.3f} s, "
              f"analyzed {an.analyzed_at}")
        for s in an.sections:
            print(f"   {s.name:12} {_fmt(s.start_sec):>9}  bar {s.start_bar + 1:>4}  {s.bars:>3} bars  "
                  f"energy {s.mean_energy:.2f}  [{s.source}]")
        blocks = " ▁▂▃▄▅▆▇█"
        curve = "".join(blocks[min(8, int(b["energy"] * 8 + 0.5))] for b in an.bars)
        print(f"   energy per bar: {curve}")
    if len(matches) > 5:
        print(f"… and {len(matches) - 5} more")
    return 0


def cmd_export_cues(env: Env, a) -> int:
    db = env.db_path
    if db is None:
        sys.exit("Mixxx library not found; pass --mixxx-db PATH.")
    lib = env.library
    if a.tracks:
        tracks = [t for q in a.tracks for t in env.find(q) if t.id >= 0]
    else:
        tracks = lib.all_tracks
    items = []
    for t in tracks:
        an = env.store.get(t.location)
        if an and an.sections:
            items.append((t.id, t.location, t.display, t.samplerate, an.sections))
    if not items:
        print("No analyzed tracks to export. Run `hsb analyze` first.")
        return 1
    own = {tid: env.store.own_cue_ids(tid) for tid, *_ in items}
    max_hot = a.max_hotcues or env.settings.max_hotcues
    plans = mixxx_cues.plan(db, items, own, max_hotcues=max_hot, replace_own=a.replace_own,
                            complete_markers=not a.no_complete_markers and env.settings.complete_markers)
    n_ops = sum(len(p.ops) for p in plans)
    for p in plans:
        if p.ops or a.verbose:
            print("\n".join(p.lines()))
    print(f"\n{n_ops} change(s) planned for {sum(1 for p in plans if p.ops)} track(s) in {db}")
    if a.dry_run or not n_ops:
        print("Dry run: nothing was written." if a.dry_run else "Nothing to write.")
        return 0
    try:
        bak, inserted, deleted = mixxx_cues.apply(db, plans)
    except mixxx_cues.CueExportError as e:
        sys.exit(str(e))
    for tid, cid, kind in inserted:
        env.store.record_cues(tid, next(p.path for p in plans if p.track_id == tid), [(cid, kind)])
    for tid, cid in deleted:
        env.store.forget_cues(tid, [cid])
    print(f"Written. Backup: {bak}\n{mixxx_cues.restore_hint(bak)}")
    return 0


def cmd_validate(env: Env, a) -> int:
    """Analyze a sample spread across genres and write a CSV to check by ear in Mixxx."""
    _guard_mixxx(a.allow_while_mixxx_runs)
    rng = random.Random(a.seed)
    by_genre: dict[str, list[Track]] = {}
    for t in env.library.all_tracks:
        if t.location and os.path.isfile(t.location):
            by_genre.setdefault((t.genre or "?").strip().casefold(), []).append(t)
    if not by_genre:
        sys.exit("No track files found (is the music drive mounted?).")
    for v in by_genre.values():
        rng.shuffle(v)
    sample: list[Track] = []
    genres = sorted(by_genre, key=lambda g: -len(by_genre[g]))
    while len(sample) < a.sample and any(by_genre[g] for g in genres):
        for g in genres:
            if by_genre[g] and len(sample) < a.sample:
                sample.append(by_genre[g].pop())
    backend = a.backend or env.settings.analysis_backend
    workers = a.jobs or env.settings.analysis_workers or batch.default_workers(backend)
    run_analysis(env, sample, backend, a.force, False, workers, quiet=True)
    out = Path(a.out or f"phrase-validation-{backend}.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["artist", "title", "genre", "section", "label", "start", "start_sec", "start_bar", "bars",
                    "mean_energy", "source", "analyzer", "correct? (y/n)", "notes"])
        for t in sample:
            an = env.store.get(t.location)
            for s in (an.sections if an else []):
                w.writerow([t.artist, t.title, t.genre, s.name, s.label, _fmt(s.start_sec), f"{s.start_sec:.2f}",
                            s.start_bar + 1, s.bars, f"{s.mean_energy:.2f}", s.source, an.analyzer, "", ""])
    print(f"Wrote {out} ({len(sample)} tracks). Check each section start by ear in Mixxx and fill in the last columns.")
    return 0


def _pick_with_cues(env: Env, n: int, min_cues: int, seed: int) -> list[Track]:
    """Up to ``n`` tracks with at least ``min_cues`` hand-placed hot cues, spread across genres."""
    lib = env.library
    cues = mixxx_db.load_cues(env.snapshot, list(lib.tracks))
    rng = random.Random(seed)
    by_genre: dict[str, list[Track]] = {}
    for t in lib.all_tracks:
        hot = [c for c in cues.get(t.id, []) if c["type"] == mixxx_cues.HOTCUE and c["start"] is not None]
        if len(hot) >= min_cues and t.location and os.path.isfile(t.location):
            by_genre.setdefault((t.genre or "?").strip().casefold(), []).append(t)
    for v in by_genre.values():
        rng.shuffle(v)
    out: list[Track] = []
    genres = sorted(by_genre, key=lambda g: -len(by_genre[g]))
    while len(out) < n and any(by_genre[g] for g in genres):
        for g in genres:
            if by_genre[g] and len(out) < n:
                out.append(by_genre[g].pop())
    return out


METHOD_NAMES = {
    "builtin": "Built-in (my rules)",
    "allin1:raw": "allin1 pop model, raw boundaries",
    "allin1": "allin1 pop model + my rules",
    "raveform:raw": "allin1 Raveform v1 (EDM), raw boundaries",
    "raveform": "allin1 Raveform v1 + my rules",
    "cuedetr": "CUE-DETR cue points (authors' threshold)",
    "cuedetr:clustered": "CUE-DETR, clustered candidates",
}


def cmd_benchmark(env: Env, a) -> int:
    """Run every method on tracks you've hand-cued in Mixxx and score them against your hot cues."""
    _guard_mixxx(a.allow_while_mixxx_runs)
    methods = a.methods.split(",")
    env.library  # noqa: B018 - takes the snapshot the cue and grid reads below use
    if a.tracks:
        tracks = [t for q in a.tracks for t in env.find(q) if t.id >= 0]
    else:
        tracks = _pick_with_cues(env, a.sample, a.min_cues, a.seed)
    if not tracks:
        sys.exit("No tracks with enough hot cues found (is the music drive mounted?).")
    cues = mixxx_db.load_cues(env.snapshot, [t.id for t in tracks])
    grids = mixxx_db.load_grids(env.snapshot, [t.id for t in tracks])
    a1_python, cd_python = env.allin1_python(), str(default_cuedetr_python(env.data_dir))
    report, items = [], {k: [] for k in METHOD_NAMES}
    for n, t in enumerate(tracks, 1):
        if not os.path.isfile(t.location):
            print(f"[{n}/{len(tracks)}] {t.display}: skipped, file not found (drive unplugged?)", flush=True)
            continue
        try:
            refs = sorted(c["start"] for c in cues[t.id] if c["type"] == mixxx_cues.HOTCUE and c["start"] is not None)
            bar = 240.0 / t.bpm if t.bpm else 2.0
            sig = batch.file_signature(t.location)
            print(f"[{n}/{len(tracks)}] {t.display}: {len(refs)} hot cues", flush=True)
            entry = {"track": t.display, "genre": t.genre, "bpm": t.bpm, "path": t.location, "hot_cues": refs,
                     "methods": {}, "timing": {}}
            job = lambda backend, raw=None: pipeline.Job(t.location, t.id, t.bpm, grids.get(t.id), backend, a1_python, raw)  # noqa: E731

            best = None  # saved as the track's analysis, so the run can be exported and imported
            if "builtin" in methods:
                res = pipeline.analyze(job("builtin"))
                env.store.raw_put(t.location, "builtin", sig, res["raw"])
                best = res
                secs = res["analysis"]["sections"]
                entry["methods"]["builtin"] = [(s["start_sec"], s["label"]) for s in secs]
            want = [m for m in ("allin1", "raveform") if m in methods]
            model_of = {"allin1": "harmonix-all", "raveform": "raveform-fold3"}
            raws = {m: env.store.raw_get(t.location, m, sig) for m in want}
            missing = [m for m in want if raws[m] is None]
            if missing:
                try:
                    got, timing = structure.run_allin1_models(t.location, a1_python, [model_of[m] for m in missing])
                    entry["timing"]["allin1"] = timing
                    print(f"     allin1 timing: {timing}", flush=True)
                    for m in missing:
                        raws[m] = got[model_of[m]]
                        env.store.raw_put(t.location, m, sig, raws[m])
                except structure.Allin1Error as e:
                    print(f"     allin1 failed: {e}")
            for m in want:
                if raws.get(m):
                    entry["methods"][f"{m}:raw"] = [(s["start"], s["label"], s.get("probs")) for s in raws[m]["segments"]]
                    res = pipeline.analyze(job("allin1", raws[m]))
                    if m == "allin1":
                        best = res  # the most accurate on the benchmark
                    entry["methods"][m] = [(s["start_sec"], s["label"]) for s in res["analysis"]["sections"]]
            if "cuedetr" in methods:
                raw = env.store.raw_get(t.location, "cuedetr", sig)
                if raw is None:
                    try:
                        raw = structure.run_cuedetr(t.location, cd_python)
                        env.store.raw_put(t.location, "cuedetr", sig, raw)
                    except structure.Allin1Error as e:
                        print(f"     CUE-DETR failed: {e}")
                if raw:
                    entry["methods"]["cuedetr"] = [(c, "cue") for c in raw["cues"]]
                    entry["methods"]["cuedetr:clustered"] = [(c, "cue") for c in benchmark.cluster_candidates(raw["candidates"])]
            if best is not None:
                batch.store_result(env.store, best)
            for k, preds in entry["methods"].items():
                items[k].append((refs, [p[0] for p in preds if p[0] > 0.01], bar))
            report.append(entry)
        except OSError as e:  # e.g. the drive went away mid-track: score what we have
            print(f"     skipped: {e}", flush=True)
    scores = [benchmark.score(METHOD_NAMES[k], v) for k, v in items.items() if v]
    text = benchmark.table(scores)
    print("\n" + text)
    out = Path(a.out or env.data_dir / "benchmark" / "latest.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {"scores": [s.__dict__ for s in scores], "tracks": report}
    out.write_text(json.dumps(data, indent=1), encoding="utf-8")
    page = out.with_suffix(".html")
    benchmark_report.write(page, data, METHOD_NAMES)
    print(f"\nDetails: {out}\nTimelines: {page}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hsb", description="Harmonic Set Builder phrase analysis.")
    p.add_argument("--mixxx-db", help="path to mixxxdb.sqlite")
    sub = p.add_subparsers(dest="cmd", required=True)

    an = sub.add_parser("analyze", help="detect sections and energy for tracks")
    src = an.add_mutually_exclusive_group()
    src.add_argument("--path", help="analyze audio files under this folder instead of the Mixxx library")
    an.add_argument("--force", action="store_true", help="re-analyze even if nothing changed")
    an.add_argument("--relabel", action="store_true", help="re-run labeling from cached model output only")
    an.add_argument("--limit", type=int, help="at most N tracks")
    an.add_argument("--backend", choices=[BUILTIN, ALLIN1])
    an.add_argument("--jobs", type=int, help="worker processes")
    an.add_argument("--allow-while-mixxx-runs", action="store_true")
    an.add_argument("--compare", help="also save these for comparison: raveform,cuedetr")

    sh = sub.add_parser("show", help="print a track's sections and energy")
    sh.add_argument("track", help="path, Mixxx track id, or part of 'Artist - Title'")

    ex = sub.add_parser("export-cues", help="write sections to Mixxx as hot cues and intro/outro markers")
    ex.add_argument("--dry-run", action="store_true", help="print the planned changes and touch nothing")
    ex.add_argument("--tracks", nargs="+", help="paths, ids or 'Artist - Title' parts (default: all analyzed)")
    ex.add_argument("--replace-own", action="store_true", help="replace cues from an earlier export")
    ex.add_argument("--max-hotcues", type=int, help="hot cue slots to use (default from settings: 8)")
    ex.add_argument("--no-complete-markers", action="store_true",
                    help="leave existing intro/outro markers completely alone")
    ex.add_argument("-v", "--verbose", action="store_true", help="also list tracks with nothing to change")

    va = sub.add_parser("validate", help="analyze a sample across genres and write a review CSV")
    va.add_argument("--sample", type=int, default=20)
    va.add_argument("--out")
    va.add_argument("--backend", choices=[BUILTIN, ALLIN1])
    va.add_argument("--jobs", type=int)
    va.add_argument("--seed", type=int, default=1)
    va.add_argument("--force", action="store_true")
    va.add_argument("--allow-while-mixxx-runs", action="store_true")

    xa = sub.add_parser("export-analysis", help="write all analysis results to a file (for another machine)")
    xa.add_argument("out", help="e.g. /mnt/e/hsb-analysis.json.gz")
    ia = sub.add_parser("import-analysis", help="load analysis results exported on another machine")
    ia.add_argument("file")
    ia.add_argument("--map", nargs="+", help="path prefix rewrites, FROM=TO")

    be = sub.add_parser("benchmark", help="score every analyzer against your hand-placed Mixxx hot cues")
    be.add_argument("--sample", type=int, default=10)
    be.add_argument("--tracks", nargs="+", help="paths, ids or 'Artist - Title' parts instead of a sample")
    be.add_argument("--min-cues", type=int, default=3, help="only tracks with at least this many hot cues")
    be.add_argument("--methods", default="builtin,allin1,raveform,cuedetr")
    be.add_argument("--seed", type=int, default=7)
    be.add_argument("--out")
    be.add_argument("--allow-while-mixxx-runs", action="store_true")

    a = p.parse_args(argv)
    env = Env(a.mixxx_db)
    try:
        return {"analyze": cmd_analyze, "show": cmd_show, "export-cues": cmd_export_cues,
                "validate": cmd_validate, "benchmark": cmd_benchmark,
                "export-analysis": cmd_export_analysis, "import-analysis": cmd_import_analysis}[a.cmd](env, a)
    finally:
        env.store.close()


if __name__ == "__main__":
    sys.exit(main())
