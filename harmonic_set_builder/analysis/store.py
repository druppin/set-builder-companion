"""Analysis results in the set builder's own SQLite database (spec §4).

Lives in the app data directory, never in Mixxx's. Also caches each backend's raw
output (so labels can be re-tuned without re-running allin1) and remembers which
Mixxx cues this tool exported (so it only ever replaces its own).
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from .labels import LABELS_TAG, Section, summary

SCHEMA = """
CREATE TABLE IF NOT EXISTS track_analysis (
  track_path      TEXT PRIMARY KEY,
  mixxx_track_id  INTEGER,
  file_hash       TEXT NOT NULL,
  analyzer        TEXT NOT NULL,
  analyzed_at     TEXT NOT NULL,
  bpm             REAL,
  first_downbeat  REAL,
  duration        REAL,
  grid            TEXT,
  phrase_offset   INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sections (
  track_path  TEXT REFERENCES track_analysis(track_path),
  idx         INTEGER,
  label       TEXT,
  number      INTEGER,
  start_sec   REAL,
  end_sec     REAL,
  start_bar   INTEGER,
  end_bar     INTEGER,
  mean_energy REAL,
  source      TEXT,
  repeated    INTEGER DEFAULT 0,
  part        INTEGER DEFAULT 1,
  PRIMARY KEY (track_path, idx)
);
CREATE TABLE IF NOT EXISTS bar_energy (
  track_path TEXT REFERENCES track_analysis(track_path),
  bar        INTEGER,
  start_sec  REAL,
  energy     REAL,
  rms REAL, low REAL, high REAL, centroid REAL, onsets REAL, low_db REAL,
  PRIMARY KEY (track_path, bar)
);
CREATE TABLE IF NOT EXISTS raw_cache (
  track_path TEXT,
  backend    TEXT,
  file_hash  TEXT NOT NULL,
  data       TEXT NOT NULL,
  PRIMARY KEY (track_path, backend)
);
CREATE TABLE IF NOT EXISTS exported_cues (
  mixxx_track_id INTEGER,
  cue_id         INTEGER,
  kind           TEXT,
  track_path     TEXT,
  exported_at    TEXT,
  PRIMARY KEY (mixxx_track_id, cue_id)
);
"""

BAR_FIELDS = ("energy", "rms", "low", "high", "centroid", "onsets", "low_db")  # low_db: kick/bass level


def file_signature(path: str) -> str:
    """mtime + size: cheap, and changes whenever the file is re-tagged or replaced."""
    st = os.stat(path)
    return f"{st.st_mtime_ns}:{st.st_size}"


SIZE_PREFIX = "size:"  # imported results: the other machine's mtime isn't comparable, the size is


def size_hash(size: int) -> str:
    return f"{SIZE_PREFIX}{size}"


def hash_matches(stored: str, signature: str) -> bool:
    if stored == signature:
        return True
    return stored.startswith(SIZE_PREFIX) and stored[len(SIZE_PREFIX):] == signature.rsplit(":", 1)[-1]


@dataclass
class TrackAnalysis:
    track_path: str
    file_hash: str
    analyzer: str
    analyzed_at: str = ""
    mixxx_track_id: Optional[int] = None
    bpm: Optional[float] = None
    first_downbeat: Optional[float] = None
    duration: float = 0.0
    grid: str = ""  # where the beat grid came from: mixxx | allin1 | detected
    phrase_offset: int = 0  # bar (0-7) where the 8-bar phrases start; 1 = one pickup bar
    sections: list[Section] = field(default_factory=list)
    bars: list[dict] = field(default_factory=list)  # {"bar", "start_sec", energy, rms, low, ...}

    @property
    def bar_times(self) -> list[float]:
        return [b["start_sec"] for b in self.bars]

    def bar_time(self, bar: int) -> float:
        if 0 <= bar < len(self.bars):
            return self.bars[bar]["start_sec"]
        if bar >= len(self.bars) and self.bars:
            return self.duration
        return 0.0

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("track_path", "file_hash", "analyzer", "analyzed_at", "mixxx_track_id",
                                             "bpm", "first_downbeat", "duration", "grid", "phrase_offset", "bars")}
        d["sections"] = [s.to_dict() for s in self.sections]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TrackAnalysis":
        a = cls(**{k: d[k] for k in ("track_path", "file_hash", "analyzer") if k in d})
        for k in ("analyzed_at", "mixxx_track_id", "bpm", "first_downbeat", "duration", "grid", "phrase_offset", "bars"):
            if k in d:
                setattr(a, k, d[k])
        a.sections = [Section.from_dict(s) for s in d.get("sections") or []]
        return a


@dataclass
class IndexRow:
    file_hash: str
    analyzer: str
    analyzed_at: str
    summary: str


class AnalysisStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(track_analysis)")}
        if "phrase_offset" not in cols:  # databases from before phrase offsets
            self.conn.execute("ALTER TABLE track_analysis ADD COLUMN phrase_offset INTEGER DEFAULT 0")
        if "low_db" not in {r[1] for r in self.conn.execute("PRAGMA table_info(bar_energy)")}:
            self.conn.execute("ALTER TABLE bar_energy ADD COLUMN low_db REAL")
        if "part" not in {r[1] for r in self.conn.execute("PRAGMA table_info(sections)")}:
            self.conn.execute("ALTER TABLE sections ADD COLUMN part INTEGER DEFAULT 1")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ analyses
    def save(self, a: TrackAnalysis) -> None:
        a.analyzed_at = a.analyzed_at or datetime.now().isoformat(timespec="seconds")
        with self.conn:
            self._delete(a.track_path)
            self.conn.execute(
                "INSERT INTO track_analysis (track_path, mixxx_track_id, file_hash, analyzer, analyzed_at, bpm, "
                "first_downbeat, duration, grid, phrase_offset) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (a.track_path, a.mixxx_track_id, a.file_hash, a.analyzer, a.analyzed_at, a.bpm, a.first_downbeat,
                 a.duration, a.grid, a.phrase_offset),
            )
            self.conn.executemany(
                "INSERT INTO sections (track_path, idx, label, number, start_sec, end_sec, start_bar, end_bar, "
                "mean_energy, source, repeated, part) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(a.track_path, i, s.label, s.number, s.start_sec, s.end_sec, s.start_bar, s.end_bar, s.mean_energy,
                  s.source, int(s.repeated), s.part) for i, s in enumerate(a.sections)],
            )
            self.conn.executemany(
                "INSERT INTO bar_energy (track_path, bar, start_sec, " + ", ".join(BAR_FIELDS) + ") VALUES ("
                + ",".join("?" * (3 + len(BAR_FIELDS))) + ")",
                [(a.track_path, b["bar"], b["start_sec"], *(b.get(k) for k in BAR_FIELDS)) for b in a.bars],
            )

    def _delete(self, path: str) -> None:
        for t in ("sections", "bar_energy", "track_analysis"):
            self.conn.execute(f"DELETE FROM {t} WHERE track_path = ?", (path,))

    def delete(self, path: str) -> None:
        with self.conn:
            self._delete(path)

    def get(self, path: str) -> Optional[TrackAnalysis]:
        r = self.conn.execute(
            "SELECT track_path, file_hash, analyzer, analyzed_at, mixxx_track_id, bpm, first_downbeat, duration, grid, "
            "phrase_offset FROM track_analysis WHERE track_path = ?", (path,)).fetchone()
        if not r:
            return None
        a = TrackAnalysis(*r[:3], analyzed_at=r[3], mixxx_track_id=r[4], bpm=r[5], first_downbeat=r[6],
                          duration=r[7] or 0.0, grid=r[8] or "", phrase_offset=r[9] or 0)
        a.sections = [
            Section(label=x[0], number=x[1], start_sec=x[2], end_sec=x[3], start_bar=x[4], end_bar=x[5],
                    mean_energy=x[6], source=x[7], repeated=bool(x[8]), part=x[9] or 1)
            for x in self.conn.execute(
                "SELECT label, number, start_sec, end_sec, start_bar, end_bar, mean_energy, source, repeated, part "
                "FROM sections WHERE track_path = ? ORDER BY idx", (path,))
        ]
        a.bars = [
            {"bar": x[0], "start_sec": x[1], **dict(zip(BAR_FIELDS, x[2:]))}
            for x in self.conn.execute(
                "SELECT bar, start_sec, " + ", ".join(BAR_FIELDS) + " FROM bar_energy WHERE track_path = ? ORDER BY bar",
                (path,))
        ]
        return a

    def index(self) -> dict[str, IndexRow]:
        """Every analyzed path with a one-line section summary (for big tables)."""
        secs: dict[str, list[Section]] = {}
        for p, label, num, sb, eb, part in self.conn.execute(
                "SELECT track_path, label, number, start_bar, end_bar, part FROM sections ORDER BY track_path, idx"):
            secs.setdefault(p, []).append(Section(label, num, sb, eb, 0, 0, 0, part=part or 1))
        return {
            p: IndexRow(h, an, at, summary(secs.get(p, [])))
            for p, h, an, at in self.conn.execute("SELECT track_path, file_hash, analyzer, analyzed_at FROM track_analysis")
        }

    def needs_analysis(self, path: str, backend: str, force: bool = False) -> bool:
        if force:
            return True
        return self.status(path, backend) != "current"

    def backend_of(self, path: str) -> Optional[str]:
        """'builtin' or 'allin1': which analyzer made the stored result."""
        r = self.conn.execute("SELECT analyzer FROM track_analysis WHERE track_path = ?", (path,)).fetchone()
        return r[0].split("==")[0] if r else None

    def status(self, path: str, backend: Optional[str] = None) -> str:
        """missing | changed (file or backend differs) | outdated (older labeling rules) | current.
        An unreachable file (drive not mounted) keeps its result: never "changed"."""
        r = self.conn.execute("SELECT file_hash, analyzer FROM track_analysis WHERE track_path = ?", (path,)).fetchone()
        if not r:
            return "missing"
        try:
            sig = file_signature(path)
            if r[0] != sig and hash_matches(r[0], sig):
                self._adopt(path, sig)
            changed = not hash_matches(r[0], sig) or (backend is not None and not r[1].startswith(backend))
        except OSError:
            changed = False
        if changed:
            return "changed"
        return "current" if r[1].endswith(LABELS_TAG) else "outdated"

    # ----------------------------------------------------------- raw cache
    def raw_get(self, path: str, backend: str, file_hash: str) -> Optional[dict]:
        r = self.conn.execute("SELECT file_hash, data FROM raw_cache WHERE track_path = ? AND backend = ?",
                              (path, backend)).fetchone()
        return json.loads(r[1]) if r and hash_matches(r[0], file_hash) else None

    def raw_backends(self, path: str) -> dict[str, str]:
        """{backend: file hash} of every cached raw output for this file."""
        return dict(self.conn.execute("SELECT backend, file_hash FROM raw_cache WHERE track_path = ?", (path,)))

    def _adopt(self, path: str, signature: str) -> None:
        """An imported (size-keyed) result met its file here: key it by this machine's signature."""
        with self.conn:
            self.conn.execute("UPDATE track_analysis SET file_hash = ? WHERE track_path = ? AND file_hash LIKE 'size:%'",
                              (signature, path))
            self.conn.execute("UPDATE raw_cache SET file_hash = ? WHERE track_path = ? AND file_hash LIKE 'size:%'",
                              (signature, path))

    def raw_put(self, path: str, backend: str, file_hash: str, data: dict) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO raw_cache VALUES (?,?,?,?)",
                              (path, backend, file_hash, json.dumps(data)))

    # ------------------------------------------------------- exported cues
    def own_cue_ids(self, track_id: int) -> set[int]:
        return {r[0] for r in self.conn.execute("SELECT cue_id FROM exported_cues WHERE mixxx_track_id = ?", (track_id,))}

    def record_cues(self, track_id: int, path: str, cues: Iterable[tuple[int, str]]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO exported_cues VALUES (?,?,?,?,?)",
                                  [(track_id, cid, kind, path, now) for cid, kind in cues])

    def forget_cues(self, track_id: int, cue_ids: Iterable[int]) -> None:
        with self.conn:
            self.conn.executemany("DELETE FROM exported_cues WHERE mixxx_track_id = ? AND cue_id = ?",
                                  [(track_id, c) for c in cue_ids])
