"""Application state: library snapshot, current set, undo stack, autosave and
the row data every table and the graph are built from."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QUndoCommand, QUndoStack

from ..core import duration as dur
from ..core import route as route_mod
from ..core.camelot import format_key
from ..core.ranking import rank_key, relate
from ..core.setlist import FIX_ROUTE, SLOT, TARGET, Context, Outcome, SetModel
from ..core.track import Track
from ..data import export, mixxx_db
from ..data.config import Config
from ..data.mixxx_db import Collection, Library
from ..data.store import SetStore
from . import columns as C
from .graph import GraphPoint

AUTOSAVE_MS = 500


class SnapshotCommand(QUndoCommand):
    """Undo by restoring whole-set snapshots: sets are small, and this covers
    every mutation (add, move, delete, fill, routes, pool) the same way."""

    def __init__(self, ctrl: "Controller", before: dict, after: dict, text: str):
        super().__init__(text)
        self.ctrl, self.before, self.after = ctrl, before, after
        self._skip_first = True

    def redo(self):
        if self._skip_first:
            self._skip_first = False
            return
        self.ctrl._restore(self.after)

    def undo(self):
        self.ctrl._restore(self.before)


class Controller(QObject):
    libraryChanged = Signal()
    setChanged = Signal()
    viewChanged = Signal()
    setsListChanged = Signal()
    message = Signal(str)

    def __init__(self, config: Config, data_dir: Path, parent=None):
        super().__init__(parent)
        self.config = config
        self.settings = config.settings
        self.data_dir = Path(data_dir)
        self.store = SetStore(self.data_dir)
        self.library = Library()
        self.db_path: Optional[Path] = None
        self.model = SetModel("My first set")
        self.undo = QUndoStack(self)
        self.focus: Optional[Collection] = None
        self.slot_view = False
        self.fix_mode = False
        self.hide_in_set = False
        self.selected_uid: Optional[str] = None
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(AUTOSAVE_MS)
        self._autosave.timeout.connect(self.save_now)

    # ------------------------------------------------------------ library
    def refresh_library(self, override: Optional[str] = None) -> bool:
        path = mixxx_db.locate(override or self.config.get("db_path"))
        if path is None:
            self.message.emit("Mixxx library not found. Choose mixxxdb.sqlite from File → Choose Mixxx library…")
            return False
        try:
            snap = mixxx_db.snapshot(path, self.data_dir / "snapshot")
            self.library = mixxx_db.load(snap)
        except Exception as e:  # sqlite errors, permissions: report, keep the old snapshot
            self.message.emit(f"Could not read the Mixxx library: {e}")
            return False
        self.db_path = path
        if override:
            self.config.set("db_path", str(path))
        if self.focus:
            pool = self.library.crates if self.focus.kind == "crate" else self.library.playlists
            self.focus = next((c for c in pool if c.id == self.focus.id), None)
        self.libraryChanged.emit()
        self.setChanged.emit()
        missing = [e for e in self.model.real_entries() if self.library.resolve(e.ref) is None]
        if missing:
            self.message.emit(f"{len(missing)} track(s) in this set can no longer be found in the library.")
        return True

    def ctx(self) -> Context:
        return Context(
            self.settings,
            resolve=self.library.resolve,
            focused=self.focused_tracks(),
            library=self.library.all_tracks,
            by_id=self.library.get,
        )

    def focused_tracks(self) -> list[Track]:
        if self.focus is None:
            return self.library.all_tracks
        return self.library.collection_tracks(self.focus)

    def set_focus(self, c: Optional[Collection]) -> None:
        self.focus = c
        self.viewChanged.emit()

    # --------------------------------------------------------------- sets
    def load_initial(self) -> None:
        sets = self.store.list_sets()
        last = self.config.get("last_set")
        ids = [s[0] for s in sets]
        if last in ids:
            self.open_set(last)
        elif ids:
            self.open_set(ids[0])
        else:
            self.save_now()
            self.setsListChanged.emit()

    def open_set(self, set_id: str) -> None:
        if self._autosave.isActive():
            self.save_now()
        r = self.store.load(set_id)
        if r.model is None:
            self.message.emit("That set could not be read.")
            return
        self.model = r.model
        if r.recovered_from:
            self.message.emit("The newest autosave of this set was corrupt; reopened the previous version.")
        self.undo.clear()
        self.slot_view = False
        self.selected_uid = None
        self.config.set("last_set", self.model.id)
        self.setChanged.emit()
        self.viewChanged.emit()
        self.setsListChanged.emit()

    def _adopt(self, model: SetModel) -> None:
        if self._autosave.isActive():
            self.save_now()
        self.model = model
        self.undo.clear()
        self.slot_view = False
        self.save_now()
        self.config.set("last_set", model.id)
        self.setChanged.emit()
        self.viewChanged.emit()
        self.setsListChanged.emit()

    def new_set(self, name: str) -> None:
        self._adopt(SetModel(name))

    def duplicate_set(self, name: str) -> None:
        self._adopt(self.model.copy(name))

    def rename_set(self, name: str) -> None:
        self.mutate("Rename set", lambda c: setattr(self.model, "name", name))
        self.setsListChanged.emit()

    def delete_set(self) -> None:
        self.store.delete(self.model.id)
        sets = self.store.list_sets()
        if sets:
            self.open_set(sets[0][0])
        else:
            self._adopt(SetModel("My first set"))

    def import_collection(self, c: Collection) -> None:
        self._adopt(SetModel.from_tracks(c.name, self.library.collection_tracks(c)))
        self.message.emit(f"Opened Mixxx {c.kind} “{c.name}” as a new set ({len(c.track_ids)} tracks).")

    def import_m3u(self, path: Path) -> list[str]:
        found, missing = export.match_paths(export.read_m3u(path), self.library.by_location)
        self._adopt(SetModel.from_tracks(Path(path).stem, found))
        return missing

    def save_now(self) -> None:
        self._autosave.stop()
        self.store.save(self.model)
        self.config.set("last_set", self.model.id)

    # ---------------------------------------------------------- mutations
    def mutate(self, text: str, fn: Callable[[Context], object]):
        before = self.model.to_dict()
        out = fn(self.ctx())
        after = self.model.to_dict()
        if after != before:
            self.undo.push(SnapshotCommand(self, before, after, text))
            self._changed()
        if isinstance(out, Outcome) and out.message:
            self.message.emit(out.message)
        return out

    def _restore(self, d: dict) -> None:
        self.model = SetModel.from_dict(d)
        self._changed()

    def _changed(self) -> None:
        if self.model.route is None or self.model.active_slot_uid() is None:
            self.slot_view = False
        self.setChanged.emit()
        self._autosave.start()

    def tracks(self, ids) -> list[Track]:
        return [t for t in (self.library.get(i) for i in ids) if t]

    def append_tracks(self, ids) -> None:
        tracks = self.tracks(ids)
        self.mutate("Add tracks", lambda c: self.model.insert_tracks(tracks, None, c))

    def drop_on_setlist(self, payload: dict, row: int) -> None:
        src = payload.get("from")
        if src == "set":
            self.mutate("Move tracks", lambda c: self.model.move_entries(payload["uids"], row, c))
            return
        tracks = self.tracks(payload.get("ids", []))
        if not tracks:
            return

        # Tracks from To be added leave it as they enter the set (SetModel._take).
        self.mutate("Fill transition" if self._in_zone(row) else "Add tracks",
                    lambda c: self.model.insert_tracks(tracks, row, c))

    def _in_zone(self, row: int) -> bool:
        z = self.model.route_zone()
        return bool(z and z[0] <= row <= z[1])

    def drop_on_pool(self, payload: dict) -> None:
        tracks = self.tracks(payload.get("ids", []))
        if tracks:
            self.mutate("Add to To be added", lambda c: self.model.add_to_pool(tracks))

    def add_to_pool(self, ids) -> None:
        tracks = self.tracks(ids)
        self.mutate("Add to To be added", lambda c: self.model.add_to_pool(tracks))

    def remove_entries(self, uids) -> None:
        self.mutate("Remove tracks", lambda c: self.model.remove_entries(uids, c))

    def remove_from_pool(self, uid: str) -> None:
        self.mutate("Remove from To be added", lambda c: self.model.remove_from_pool(uid, c))

    def open_pool_route(self, uid: str) -> Outcome:
        return self.mutate("Show route", lambda c: self.model.open_pool_route(uid, c))

    def place_as_opener(self, uid: str) -> None:
        self.mutate("Place opening track", lambda c: self.model.place_as_opener(uid, c))

    def placements(self, pool_uid: str) -> list[route_mod.Placement]:
        p = self.model.pool_item(pool_uid)
        t = self.library.resolve(p.ref) if p else None
        if t is None:
            return []
        reals = [self.library.resolve(e.ref) for e in self.model.real_entries()]
        if any(r is None for r in reals):
            reals = [r or Track(-1) for r in reals]
        return route_mod.suggest_placements(reals, t, self.settings)

    def insert_pool_item(self, pool_uid: str, gap: int) -> None:
        self.mutate("Insert from To be added", lambda c: self.model.insert_pool_item(pool_uid, gap, c))

    def open_fix_route(self, b_uid: str) -> None:
        self.mutate("Fix key mistake", lambda c: self.model.open_fix_route(b_uid, c))

    def close_route(self) -> None:
        self.mutate("Stop showing route", lambda c: self.model.close_route())

    def fill_active(self, ids) -> None:
        tracks = self.tracks(ids)
        if self.model.route and tracks:
            self.mutate("Fill transition", lambda c: self.model.fill(tracks, c))

    def toggle_slot_view(self) -> None:
        self.slot_view = (not self.slot_view) and self.model.active_slot_uid() is not None
        self.viewChanged.emit()

    def set_fix_mode(self, on: bool) -> None:
        self.fix_mode = on
        if not on and self.model.route and self.model.route.kind == FIX_ROUTE:
            self.close_route()
        self.setChanged.emit()

    def set_hide_in_set(self, on: bool) -> None:
        self.hide_in_set = on
        self.viewChanged.emit()

    def select(self, uid: Optional[str]) -> None:
        if uid != self.selected_uid:
            self.selected_uid = uid
            self.viewChanged.emit()

    def settings_changed(self) -> None:
        self.config.save()
        self.libraryChanged.emit()
        self.setChanged.emit()

    # ------------------------------------------------------------- export
    def real_tracks(self) -> list[Track]:
        return [t for t in (self.library.resolve(e.ref) for e in self.model.real_entries()) if t]

    def export(self, path: Path, as_text: bool) -> None:
        tracks = self.real_tracks()
        if as_text:
            rels = [None] + [relate(a, b, self.settings) for a, b in zip(tracks, tracks[1:])]
            d = dur.estimate([dur.Item(t.duration, t.bpm) for t in tracks], self.settings.mix_overlap_bars)
            text = export.tracklist_text(tracks, rels, self.settings.key_notation)
            text += f"\nEstimated length ≈ {dur.fmt(d.mixed)} ({self.settings.mix_overlap_bars}-bar mixes)\n"
            Path(path).write_text(text, encoding="utf-8")
        else:
            export.write_m3u8(Path(path), tracks)
        self.message.emit(f"Exported {len(tracks)} tracks to {path}")

    def duration_summary(self) -> tuple[str, str]:
        """(label, tooltip) for the estimated set length."""
        bars = self.settings.mix_overlap_bars
        real_items, all_items = [], []
        for e in self.model.entries:
            if e.kind == SLOT:
                cands = [t for t in (self.library.get(i) for i in e.slot.candidates) if t and t.duration]
                durs = sorted(t.duration for t in cands)
                item = dur.Item(durs[len(durs) // 2] if durs else None, (e.slot.bpm_min + e.slot.bpm_max) / 2)
            else:
                t = self.library.resolve(e.ref)
                item = dur.Item(t.duration if t else None, t.bpm if t else None)
                if e.is_real:
                    real_items.append(item)
            all_items.append(item)
        if not real_items:
            return "Empty set", ""
        d = dur.estimate(real_items, bars)
        n = d.tracks
        text = f"{n} track{'s' if n != 1 else ''} · ≈ {dur.fmt(d.mixed)} mixed"
        if bars:
            text += f" ({dur.fmt(d.back_to_back)} back to back)"
        if d.unknown:
            text += f" · {d.unknown} without a length"
        if len(all_items) > len(real_items):
            text += f" · with route ≈ {dur.fmt(dur.estimate(all_items, bars).mixed)}"
        tip = (f"Track lengths added up, minus a {bars}-bar overlap per transition at the incoming "
               "track's tempo (change it in Settings → Mixing)." if bars else "Track lengths added up.")
        if len(all_items) > len(real_items):
            tip += "\nWith route: unfilled transitions count as a typical candidate's length."
        return text, tip

    # ----------------------------------------------------------------- rows
    def anchor_track(self) -> Optional[Track]:
        r = self.model.route
        e = self.model.entry(r.anchor_uid) if r else None
        return self.library.resolve(e.ref) if e and e.ref else None

    def reference_track(self) -> Optional[Track]:
        """Selected setlist row, else the last real track (slot view: the anchor)."""
        if self.slot_view:
            return self.anchor_track()
        e = self.model.entry(self.selected_uid) if self.selected_uid else None
        if e and e.is_real:
            t = self.library.resolve(e.ref)
            if t:
                return t
        last = self.model.last_real()
        return self.library.resolve(last.ref) if last else None

    def _row_for(self, t: Track, ref: Optional[Track], in_set: set, pool: set) -> C.Row:
        rel = relate(ref, t, self.settings) if ref and t.mixable and ref.mixable else None
        return C.Row(
            track=t, rel=rel, in_set=t.id in in_set, want=t.id in pool,
            rank=rank_key(rel, t.rating) if ref else (), haystack=C.make_haystack(t),
        )

    def track_rows(self) -> tuple[list[C.Row], str]:
        """Rows for the track table and the slot-view banner ("" in normal view)."""
        in_set, pool = self.model.set_track_ids(), self.model.pool_track_ids()
        ref = self.reference_track()
        if self.slot_view:
            uid = self.model.active_slot_uid()
            e = self.model.entry(uid)
            if e is not None:
                rows = []
                for tid in e.slot.candidates:
                    t = self.library.get(tid)
                    if t:
                        row = self._row_for(t, ref, in_set, pool)
                        row.pinned = t.id in pool
                        rows.append(row)
                rows.sort(key=lambda r: not r.pinned)  # stable: fitting pool tracks first
                n, total = self.model.slot_position(uid)
                fmt = lambda k: format_key(k, self.settings.key_notation)  # noqa: E731
                lib = " · from library" if e.slot.from_library else ""
                banner = (f"Transition {n} of {total} — {e.slot.key_text(fmt)}, {e.slot.bpm_text()} BPM{lib}"
                          "   (click the transition again to go back)")
                return rows, banner
        rows = [self._row_for(t, ref, in_set, pool) for t in self.focused_tracks()]
        if ref is not None:
            rows.sort(key=lambda r: r.rank)
        return rows, ""

    def set_rows(self) -> tuple[list[C.Row], list[GraphPoint]]:
        ctx = self.ctx()
        infos = self.model.analyze(ctx)
        energy = self.model.energy(infos, self.settings)
        pool = self.model.pool_track_ids()
        active = self.model.active_slot_uid()
        n = self.settings.key_notation
        rows, pts = [], []
        pos = 0
        for i, (info, ep) in enumerate(zip(infos, energy)):
            e = info.entry
            slot_moves = ""
            if e.kind == SLOT:
                prev = infos[i - 1].rep if i > 0 else None
                if prev and prev.key:
                    names = []
                    for k in e.slot.keys:
                        mv = relate(prev, Track(-1, key=k, bpm=prev.bpm), self.settings).move
                        if mv and not mv.is_clash and mv.name not in names:
                            names.append(mv.name)
                    slot_moves = " / ".join(names)
            else:
                pos += 1 if e.is_real else 0
            row = C.Row(
                track=info.track, rel=info.rel, entry=e, pos=pos if e.is_real else None,
                in_set=e.is_real, want=bool(info.track and info.track.id in pool) or e.kind == TARGET,
                clash=info.clash, duplicate=info.duplicate, missing=info.missing,
                slot_active=e.uid == active, slot_moves=slot_moves,
                fixable=self.fix_mode and info.clash and not (
                    self.model.route and self.model.route.kind == FIX_ROUTE and self.model.route.target_uid == e.uid),
            )
            rows.append(row)
            if e.kind == SLOT:
                label, kind = e.slot.key_text(lambda k: format_key(k, n)), "slot"
                tip = f"Transition: {label}, {e.slot.bpm_text()} BPM"
            else:
                t = info.track
                label = format_key(t.key, n) if t else "?"
                kind = "target" if e.kind == TARGET else "track"
                who = f"{t.artist} – {t.title}" if t else f"{e.ref.artist} – {e.ref.title} (missing)"
                tip = who
                if info.rel and info.rel.move:
                    mv = info.rel.move
                    tip += f"\n{mv.name}: {mv.label}"
                if info.rel and info.rel.bpm:
                    tip += f"\nΔbpm {info.rel.bpm.delta:+.1f} ({info.rel.bpm.band})"
            tip += f"\nEnergy {ep.value:g}"
            pts.append(GraphPoint(i, ep.value, label, tip, kind, info.clash))
        return rows, pts

    def pool_rows(self) -> list[C.Row]:
        ref = self.reference_track()
        in_set = self.model.set_track_ids()
        rows = []
        for p in self.model.pool:
            t = self.library.resolve(p.ref)
            if t is None:
                rows.append(C.Row(entry=None, missing=True, want=True, extra={"uid": p.uid, "ref": p.ref}))
                continue
            row = self._row_for(t, ref, in_set, set())
            row.want = True
            row.extra = {"uid": p.uid}
            rows.append(row)
        return rows
