"""Export detected sections to Mixxx as hot cues and intro/outro markers (spec §6).

This is the only code that writes to Mixxx's database, and only on explicit
request. Safety rules, all enforced here:

1. Refuse while Mixxx is running (it caches tracks and would overwrite edits).
2. Back up the database (timestamped copy next to it) before every write.
3. One transaction for everything; any error rolls it all back.
4. Never touch cues the user placed: hot cues only go into empty slots, and only
   cues this tool created (label prefix ``◆`` or ids recorded in the analysis
   store) are ever replaced. An existing intro/outro marker is only *completed*
   (its missing end / start filled in), never moved.
5. ``plan()`` is a dry run: it reads, computes and touches nothing.

Schema, verified against a real Mixxx 2.4 library (2026-10-08) and Mixxx's
``CueType`` enum: ``cues(id, track_id, type, position, length, hotcue, label, color)``;
type 1 = hot cue, 2 = main cue, 4 = loop, 6 = intro, 7 = outro, 8 = audible sound;
``position``/``length`` are in stereo samples (seconds × samplerate × 2);
``hotcue`` is the 0-based slot (-1 for non-hot cues); ``color`` is 0xRRGGBB.
An intro/outro with only its end set stores position -1 and the end in ``length``.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from ..analysis.labels import BREAKDOWN, BUILD, DROP, GROOVE, INTRO, OUTRO, Section, first, last

HOTCUE, MAIN, LOOP, INTRO_CUE, OUTRO_CUE, AUDIBLE = 1, 2, 4, 6, 7, 8
OWN_PREFIX = "◆ "
DEFAULT_MAX_HOTCUES = 8
MAX_HOTCUES = 36  # Mixxx 2.4+
SAME_SPOT_SEC = 0.25  # an existing hot cue this close already marks the section
REQUIRED_COLUMNS = {"id", "track_id", "type", "position", "length", "hotcue", "label", "color"}

# From Mixxx's default hot cue palette, so they look at home there.
COLORS = {
    INTRO: 0x32BE44,  # green
    BUILD: 0xF8D200,  # yellow
    DROP: 0xC50A08,  # red
    BREAKDOWN: 0x0044FF,  # blue
    GROOVE: 0xAF00CC,  # purple
    OUTRO: 0x42D4F4,  # celeste
}
# With more sections than free slots, keep these first.
PRIORITY = [DROP, BREAKDOWN, OUTRO, BUILD, INTRO, GROOVE]


class CueExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExistingCue:
    id: int
    type: int
    position: float
    length: float
    hotcue: int
    label: str
    color: int


@dataclass
class CueOp:
    action: str  # insert | update | delete
    kind: str  # hotcue | intro | outro
    type: int
    position: float = -1
    length: float = 0
    hotcue: int = -1
    label: str = ""
    color: int = 0
    cue_id: Optional[int] = None  # for update / delete
    note: str = ""


@dataclass
class TrackPlan:
    track_id: int
    path: str
    title: str
    ops: list[CueOp] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [f"{self.title}"]
        out += [f"   {op.action:6} {op.note}" for op in self.ops]
        out += [f"   skip   {s}" for s in self.skipped]
        return out


def is_own(c: ExistingCue, own_ids: set[int]) -> bool:
    return c.id in own_ids or c.label.startswith(OWN_PREFIX)


def _fmt(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:05.2f}"


def plan_track(track_id: int, path: str, title: str, samplerate: int, sections: Sequence[Section],
               existing: Sequence[ExistingCue], own_ids: set[int], max_hotcues: int = DEFAULT_MAX_HOTCUES,
               replace_own: bool = False, complete_markers: bool = True) -> TrackPlan:
    p = TrackPlan(track_id, path, title)
    if not samplerate:
        p.skipped.append("no sample rate in Mixxx's library (analyze the track in Mixxx first)")
        return p
    if not sections:
        p.skipped.append("not analyzed")
        return p
    to_pos = lambda sec: round(sec * samplerate * 2, 1)  # noqa: E731
    max_hotcues = max(1, min(max_hotcues, MAX_HOTCUES))

    # ---- hot cues
    hot = [c for c in existing if c.type == HOTCUE and c.hotcue >= 0]
    own_hot = [c for c in hot if is_own(c, own_ids)]
    if own_hot and not replace_own:
        p.skipped.append(f"{len(own_hot)} hot cue(s) from an earlier export (choose “replace my earlier cues”)")
    else:
        for c in own_hot:
            p.ops.append(CueOp("delete", "hotcue", HOTCUE, cue_id=c.id, hotcue=c.hotcue,
                               note=f"hot cue {c.hotcue + 1} “{c.label}” (from an earlier export)"))
        theirs = [c for c in hot if not is_own(c, own_ids)]
        used = {c.hotcue for c in theirs}
        free = [s for s in range(max_hotcues) if s not in used]
        wanted = []
        for s in sections:
            near = next((c for c in theirs if abs(c.position / (2 * samplerate) - s.start_sec) <= SAME_SPOT_SEC), None)
            if near:
                p.skipped.append(f"{s.name} at {_fmt(s.start_sec)}: you already have hot cue {near.hotcue + 1} there")
            else:
                wanted.append(s)
        if len(wanted) > len(free):
            # Whole sections first (by label priority), then the phrase changes inside them.
            ranked = sorted(wanted, key=lambda s: (s.part > 1, PRIORITY.index(s.label) if s.label in PRIORITY else 99,
                                                   s.start_sec))
            keep = ranked[:len(free)]
            for s in ranked[len(free):]:
                p.skipped.append(f"{s.name} at {_fmt(s.start_sec)}: no free hot cue slot (of {max_hotcues})")
            wanted = sorted(keep, key=lambda s: s.start_sec)
        for slot, s in zip(free, wanted):
            p.ops.append(CueOp("insert", "hotcue", HOTCUE, to_pos(s.start_sec), 0, slot, OWN_PREFIX + s.name,
                               COLORS.get(s.label, 0xF2F2FF),
                               note=f"hot cue {slot + 1} “{s.name}” at {_fmt(s.start_sec)}"))

    # ---- intro / outro markers
    for kind, ctype, sec in (("intro", INTRO_CUE, first(sections, INTRO)), ("outro", OUTRO_CUE, last(sections, OUTRO))):
        if sec is None:
            continue
        start, end = to_pos(sec.start_sec), to_pos(sec.end_sec)
        cur = next((c for c in existing if c.type == ctype), None)
        span = f"{_fmt(sec.start_sec)}–{_fmt(sec.end_sec)}"
        if cur is None:
            p.ops.append(CueOp("insert", kind, ctype, start, end - start, -1, OWN_PREFIX + kind.title(),
                               COLORS[sec.label], note=f"{kind} marker {span}"))
        elif is_own(cur, own_ids):
            if replace_own:
                p.ops.append(CueOp("update", kind, ctype, start, end - start, -1, OWN_PREFIX + kind.title(),
                                   COLORS[sec.label], cue_id=cur.id, note=f"{kind} marker {span} (from an earlier export)"))
            else:
                p.skipped.append(f"{kind} marker from an earlier export")
        elif not complete_markers:
            p.skipped.append(f"track already has an {kind} marker")
        elif ctype == INTRO_CUE and cur.position >= 0 and cur.length <= 0 and end > cur.position:
            p.ops.append(CueOp("update", kind, ctype, cur.position, end - cur.position, -1, cur.label, cur.color,
                               cue_id=cur.id, note=f"intro end at {_fmt(sec.end_sec)} (keeps your intro start)"))
        elif ctype == OUTRO_CUE and cur.position < 0 < cur.length and start < cur.length:
            p.ops.append(CueOp("update", kind, ctype, start, cur.length - start, -1, cur.label, cur.color,
                               cue_id=cur.id, note=f"outro start at {_fmt(sec.start_sec)} (keeps your outro end)"))
        else:
            p.skipped.append(f"track already has a complete {kind} marker")
    return p


# ------------------------------------------------------------- database
def mixxx_running() -> bool:
    """Is a Mixxx process running for this user?"""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq mixxx.exe", "/NH"], capture_output=True,
                                 text=True, timeout=10).stdout
            return "mixxx.exe" in out.lower()
        if sys.platform == "darwin":
            return subprocess.run(["pgrep", "-ix", "mixxx"], capture_output=True, timeout=10).returncode == 0
        for d in Path("/proc").iterdir():
            if d.name.isdigit():
                try:
                    if (d / "comm").read_text().strip().lower() == "mixxx":
                        return True
                except OSError:
                    continue
    except (OSError, subprocess.SubprocessError):
        return True  # can't tell: assume the worst
    return False


def read_existing(db_path: Path, track_ids: Iterable[int]) -> dict[int, list[ExistingCue]]:
    """Current cues of these tracks, read-only."""
    ids = list(track_ids)
    out: dict[int, list[ExistingCue]] = {i: [] for i in ids}
    if not ids:
        return out
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)
    try:
        _check_schema(conn)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = ("SELECT id, track_id, type, position, length, hotcue, label, color FROM cues WHERE track_id IN ("
                 + ",".join("?" * len(chunk)) + ")")
            for r in conn.execute(q, chunk):
                out[r[1]].append(ExistingCue(r[0], r[2], float(r[3]), float(r[4]), int(r[5]), r[6] or "", int(r[7] or 0)))
    finally:
        conn.close()
    return out


def _check_schema(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(cues)")}
    missing = REQUIRED_COLUMNS - cols
    if missing:
        raise CueExportError(f"Mixxx's cues table doesn't look as expected (missing {', '.join(sorted(missing))}); "
                             "not writing anything.")


def backup(db_path: Path) -> Path:
    """Timestamped copy next to the original, e.g. mixxxdb.sqlite.hsb-backup-20261008-142233."""
    db_path = Path(db_path)
    dst = db_path.with_name(f"{db_path.name}.hsb-backup-{datetime.now():%Y%m%d-%H%M%S}")
    src = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2.0)
    try:
        tgt = sqlite3.connect(dst)
        try:
            src.backup(tgt)
        finally:
            tgt.close()
    finally:
        src.close()
    return dst


def apply(db_path: Path, plans: Sequence[TrackPlan], *, check_running: bool = True
          ) -> tuple[Path, list[tuple[int, int, str]], list[tuple[int, int]]]:
    """Write the plans in one transaction. Returns (backup path, inserted (track, cue id, kind),
    deleted (track, cue id)). Raises CueExportError without writing if anything is off."""
    if check_running and mixxx_running():
        raise CueExportError("Mixxx is running. Close it first: Mixxx caches tracks and would overwrite the new cues.")
    ops = [(p, op) for p in plans for op in p.ops]
    if not ops:
        raise CueExportError("Nothing to write.")
    bak = backup(db_path)
    inserted: list[tuple[int, int, str]] = []
    deleted: list[tuple[int, int]] = []
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    try:
        _check_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for p, op in ops:
                if op.action == "delete":
                    conn.execute("DELETE FROM cues WHERE id = ? AND track_id = ?", (op.cue_id, p.track_id))
                    deleted.append((p.track_id, op.cue_id))
                elif op.action == "update":
                    cur = conn.execute("UPDATE cues SET position = ?, length = ?, label = ?, color = ? "
                                       "WHERE id = ? AND track_id = ? AND type = ?",
                                       (op.position, op.length, op.label, op.color, op.cue_id, p.track_id, op.type))
                    if cur.rowcount != 1:
                        raise CueExportError(f"cue {op.cue_id} of track {p.track_id} changed since it was read")
                else:
                    if op.kind == "hotcue" and conn.execute(
                            "SELECT 1 FROM cues WHERE track_id = ? AND type = ? AND hotcue = ?",
                            (p.track_id, HOTCUE, op.hotcue)).fetchone():
                        raise CueExportError(f"hot cue slot {op.hotcue + 1} of track {p.track_id} is no longer empty")
                    cur = conn.execute(
                        "INSERT INTO cues (track_id, type, position, length, hotcue, label, color) VALUES (?,?,?,?,?,?,?)",
                        (p.track_id, op.type, op.position, op.length, op.hotcue, op.label, op.color))
                    inserted.append((p.track_id, cur.lastrowid, op.kind))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    except sqlite3.Error as e:
        raise CueExportError(f"Mixxx's database refused the write ({e}); nothing was changed. Backup: {bak}") from e
    finally:
        conn.close()
    return bak, inserted, deleted


def plan(db_path: Path, items: Sequence[tuple[int, str, str, int, Sequence[Section]]], own_ids: dict[int, set[int]],
         **opts) -> list[TrackPlan]:
    """Dry run for many tracks. ``items``: (track id, path, title, samplerate, sections)."""
    existing = read_existing(db_path, [i[0] for i in items])
    return [plan_track(tid, path, title, sr, secs, existing.get(tid, []), own_ids.get(tid, set()), **opts)
            for tid, path, title, sr, secs in items]


def restore_hint(bak: Path) -> str:
    return f"To undo, close Mixxx and copy {bak.name} back over {bak.name.split('.hsb-backup-')[0]}."

