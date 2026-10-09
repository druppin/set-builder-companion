"""Locate Mixxx's library, snapshot it read-only, and query only the snapshot.

Hard rule: Mixxx's own database is opened with ``mode=ro`` exactly once per
snapshot, copied with SQLite's backup API, and closed immediately.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..core.camelot import from_key_id, parse_key
from ..core.setlist import TrackRef
from ..core.track import Track

DB_NAME = "mixxxdb.sqlite"

# Playlists.hidden values observed in Mixxx 2.4/2.5 (PlaylistDAO::HiddenType).
PLAYLIST_NORMAL, PLAYLIST_AUTODJ, PLAYLIST_HISTORY, PLAYLIST_PLACEHOLDER = 0, 1, 2, -1


def candidate_paths() -> list[Path]:
    home = Path.home()
    out: list[Path] = []
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        if local:
            out.append(Path(local) / "Mixxx" / DB_NAME)
    elif sys.platform == "darwin":
        out.append(home / "Library/Containers/org.mixxx.mixxx/Data/Library/Application Support/Mixxx" / DB_NAME)
        out.append(home / "Library/Application Support/Mixxx" / DB_NAME)
    else:
        out.append(home / ".mixxx" / DB_NAME)
        xdg = os.environ.get("XDG_DATA_HOME")
        out.append((Path(xdg) if xdg else home / ".local/share") / "Mixxx" / DB_NAME)
    return out


def locate(override: Optional[str] = None) -> Optional[Path]:
    if override:
        p = Path(override).expanduser()
        return p if p.is_file() else None
    return next((p for p in candidate_paths() if p.is_file()), None)


def snapshot(src: Path, dst_dir: Path) -> Path:
    """Copy ``src`` into ``dst_dir`` with the backup API in one short operation."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "library-snapshot.sqlite"
    tmp = dst.with_suffix(".tmp")
    if tmp.exists():
        tmp.unlink()
    uri = Path(src).resolve().as_uri() + "?mode=ro"
    source = sqlite3.connect(uri, uri=True, timeout=2.0)
    try:
        target = sqlite3.connect(tmp)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    os.replace(tmp, dst)
    return dst


@dataclass
class Collection:
    """A crate or playlist."""

    kind: str  # "crate" | "playlist"
    id: int
    name: str
    track_ids: list[int] = field(default_factory=list)
    hidden: int = 0


class Library:
    """In-memory view of a snapshot."""

    def __init__(self) -> None:
        self.tracks: dict[int, Track] = {}
        self.crates: list[Collection] = []
        self.playlists: list[Collection] = []
        self.snapshot_time: Optional[datetime] = None
        self.source_path: Optional[Path] = None
        self._by_location: dict[str, int] = {}
        self._by_name: dict[tuple[str, str], int] = {}

    @property
    def all_tracks(self) -> list[Track]:
        return list(self.tracks.values())

    def get(self, track_id: int) -> Optional[Track]:
        return self.tracks.get(track_id)

    def by_location(self, path: str) -> Optional[Track]:
        tid = self._by_location.get(os.path.normcase(os.path.normpath(path)))
        return self.tracks.get(tid) if tid is not None else None

    def resolve(self, ref: TrackRef) -> Optional[Track]:
        """Id first (if its path still matches), then path, then artist + title."""
        t = self.tracks.get(ref.track_id)
        if t and (not ref.location or os.path.normcase(t.location) == os.path.normcase(ref.location)):
            return t
        if ref.location:
            t = self.by_location(ref.location)
            if t:
                return t
        tid = self._by_name.get((ref.artist.casefold(), ref.title.casefold()))
        return self.tracks.get(tid) if tid is not None else None

    def collection_tracks(self, c: Collection) -> list[Track]:
        return [self.tracks[i] for i in c.track_ids if i in self.tracks]

    def _index(self) -> None:
        self._by_location = {os.path.normcase(os.path.normpath(t.location)): t.id for t in self.tracks.values()}
        self._by_name = {(t.artist.casefold(), t.title.casefold()): t.id for t in self.tracks.values()}


def _s(v) -> str:
    return "" if v is None else str(v)


def load(snapshot_path: Path) -> Library:
    """Read tracks, crates and playlists from a snapshot copy."""
    lib = Library()
    conn = sqlite3.connect(snapshot_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(library)")}
        want = [
            "id", "artist", "title", "album", "album_artist", "genre", "composer", "grouping", "year",
            "tracknumber", "duration", "bpm", "key", "key_id", "rating", "timesplayed", "last_played_at",
            "comment", "datetime_added", "bitrate", "filetype", "color", "coverart_type", "coverart_location",
            "samplerate",
        ]
        select = ", ".join(f"l.{c}" if c in cols else f"NULL AS {c}" for c in want)
        sql = (
            f"SELECT {select}, tl.location AS path FROM library l "
            "JOIN track_locations tl ON tl.id = l.location "
            "WHERE COALESCE(l.mixxx_deleted, 0) = 0 AND COALESCE(tl.fs_deleted, 0) = 0"
        )
        for r in conn.execute(sql):
            key = from_key_id(r["key_id"]) or parse_key(r["key"])
            bpm = r["bpm"] if r["bpm"] and r["bpm"] > 0 else None
            lib.tracks[r["id"]] = Track(
                id=r["id"], artist=_s(r["artist"]), title=_s(r["title"]), album=_s(r["album"]),
                album_artist=_s(r["album_artist"]), genre=_s(r["genre"]), composer=_s(r["composer"]),
                grouping=_s(r["grouping"]), year=_s(r["year"]), tracknumber=_s(r["tracknumber"]),
                duration=float(r["duration"] or 0), bpm=bpm, key=key, key_text=_s(r["key"]),
                rating=int(r["rating"] or 0), timesplayed=int(r["timesplayed"] or 0),
                last_played_at=_s(r["last_played_at"]), comment=_s(r["comment"]),
                datetime_added=_s(r["datetime_added"]), bitrate=int(r["bitrate"] or 0),
                filetype=_s(r["filetype"]), color=r["color"], location=_s(r["path"]),
                cover_type=int(r["coverart_type"] or 0), cover_location=_s(r["coverart_location"]),
                samplerate=int(r["samplerate"] or 0),
            )
        # Crates have no order of their own: use Mixxx's default library sort (artist, title).
        for r in conn.execute("SELECT id, name FROM crates ORDER BY name COLLATE NOCASE"):
            ids = [
                x[0] for x in conn.execute(
                    "SELECT ct.track_id FROM crate_tracks ct JOIN library l ON l.id = ct.track_id "
                    "WHERE ct.crate_id = ? ORDER BY l.artist COLLATE NOCASE, l.title COLLATE NOCASE",
                    (r["id"],),
                )
            ]
            lib.crates.append(Collection("crate", r["id"], r["name"], [i for i in ids if i in lib.tracks]))
        for r in conn.execute("SELECT id, name, hidden FROM Playlists ORDER BY position, id"):
            ids = [
                x[0] for x in conn.execute(
                    "SELECT track_id FROM PlaylistTracks WHERE playlist_id = ? ORDER BY position", (r["id"],)
                )
            ]
            lib.playlists.append(
                Collection("playlist", r["id"], r["name"], [i for i in ids if i in lib.tracks], int(r["hidden"] or 0))
            )
    finally:
        conn.close()
    lib._index()
    lib.snapshot_time = datetime.now()
    return lib


def load_grids(snapshot_path: Path, track_ids) -> dict[int, dict]:
    """Mixxx beat grids (decoded, in seconds) for these tracks, from the snapshot."""
    from ..analysis.grid import decode_mixxx_beats

    ids = list(track_ids)
    out: dict[int, dict] = {}
    conn = sqlite3.connect(snapshot_path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(library)")}
        if not {"beats", "beats_version", "samplerate"} <= cols:
            return out
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ("SELECT id, beats, beats_version, samplerate FROM library WHERE id IN (" + ",".join("?" * len(chunk)) + ")")
            for tid, blob, ver, sr in conn.execute(q, chunk):
                g = decode_mixxx_beats(blob, ver, int(sr or 0))
                if g:
                    out[tid] = g.to_dict()
    finally:
        conn.close()
    return out


def load_cues(snapshot_path: Path, track_ids) -> dict[int, list[dict]]:
    """Cues of these tracks from the snapshot, in seconds: {"type", "start", "length", "hotcue", "label"}
    (type 1 = hot cue, 6 = intro, 7 = outro; see data/mixxx_cues.py)."""
    ids = list(track_ids)
    out: dict[int, list[dict]] = {i: [] for i in ids}
    conn = sqlite3.connect(snapshot_path)
    try:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ("SELECT c.track_id, c.type, c.position, c.length, c.hotcue, c.label, l.samplerate, c.color FROM cues c "
                 "JOIN library l ON l.id = c.track_id WHERE c.track_id IN (" + ",".join("?" * len(chunk)) + ")")
            for tid, ctype, pos, length, hot, label, sr, color in conn.execute(q, chunk):
                if not sr:
                    continue
                k = 2.0 * sr  # positions are stereo samples
                out[tid].append({"type": int(ctype), "start": pos / k if pos >= 0 else None,
                                 "length": (length or 0) / k, "hotcue": int(hot), "label": label or "",
                                 "color": int(color) if color is not None else None})
    finally:
        conn.close()
    return out


def visible_playlists(lib: Library, show_history: bool, show_autodj: bool) -> list[Collection]:
    out = []
    for p in lib.playlists:
        if p.hidden == PLAYLIST_NORMAL:
            out.append(p)
        elif p.hidden == PLAYLIST_AUTODJ and show_autodj:
            out.append(p)
        elif p.hidden == PLAYLIST_HISTORY and show_history:
            out.append(p)
    return out
