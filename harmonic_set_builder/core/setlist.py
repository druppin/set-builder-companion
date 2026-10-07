"""Set model: real tracks, transitional entries, the "To be added" pool and the
route state machine (pool routes and key-mistake fix routes).

Layout invariants while a route is open:

* pool route: ``[..., anchor, slot*, target placeholder]`` (block at the end)
* fix route:  ``[..., anchor, slot*, B, ...]`` (block right before B)

The anchor is always the last real track before the block. Only the first slot
is active. Filling = inserting a real track right after the anchor, making it the
new anchor and recomputing the route. A fitting track and a non-fitting one take
the same path; the recompute decides how many slots remain.
"""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

from . import route as route_mod
from .energy import EnergyPoint, series_from_moves
from .ranking import Relation, relate
from .settings import Settings
from .track import Track

REAL, SLOT, TARGET = "track", "slot", "target"
POOL_ROUTE, FIX_ROUTE = "pool", "fix"


def new_uid() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class TrackRef:
    """Survives a library refresh: Mixxx id plus path, artist and title."""

    track_id: int
    location: str
    artist: str
    title: str

    @classmethod
    def of(cls, t: Track) -> "TrackRef":
        return cls(t.id, t.location, t.artist, t.title)

    def to_dict(self) -> dict:
        return {"id": self.track_id, "location": self.location, "artist": self.artist, "title": self.title}

    @classmethod
    def from_dict(cls, d: dict) -> "TrackRef":
        return cls(int(d.get("id", -1)), d.get("location", ""), d.get("artist", ""), d.get("title", ""))


@dataclass
class Entry:
    kind: str
    ref: Optional[TrackRef] = None  # REAL and TARGET
    slot: Optional[route_mod.Slot] = None  # SLOT
    uid: str = field(default_factory=new_uid)
    from_pool: bool = False  # came from To be added: goes back there if removed

    @property
    def is_real(self) -> bool:
        return self.kind == REAL

    def to_dict(self) -> dict:
        d = {"uid": self.uid, "kind": self.kind}
        if self.from_pool:
            d["from_pool"] = True
        if self.ref:
            d["ref"] = self.ref.to_dict()
        if self.slot:
            d["slot"] = self.slot.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Entry":
        return cls(
            d["kind"],
            TrackRef.from_dict(d["ref"]) if d.get("ref") else None,
            route_mod.Slot.from_dict(d["slot"]) if d.get("slot") else None,
            d.get("uid") or new_uid(),
            bool(d.get("from_pool")),
        )


@dataclass
class PoolItem:
    ref: TrackRef
    uid: str = field(default_factory=new_uid)


@dataclass
class RouteState:
    kind: str  # POOL_ROUTE | FIX_ROUTE
    target_uid: str  # pool item uid (pool route) or B's entry uid (fix route)
    anchor_uid: str
    status: str = "ok"  # "ok" | "unreachable"
    message: str = ""
    from_library: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "RouteState":
        return cls(**d)


@dataclass
class Context:
    """What the set needs from the outside world to compute routes."""

    settings: Settings
    resolve: Callable[[TrackRef], Optional[Track]]
    focused: Sequence[Track] = ()
    library: Sequence[Track] = ()
    by_id: Callable[[int], Optional[Track]] = lambda _id: None


@dataclass
class Outcome:
    """Result of a route operation, for the status bar."""

    message: str = ""
    completed: bool = False
    fitted: Optional[bool] = None


class SetModel:
    def __init__(self, name: str = "New set", set_id: Optional[str] = None):
        self.id = set_id or new_uid()
        self.name = name
        self.entries: list[Entry] = []
        self.pool: list[PoolItem] = []
        self.route: Optional[RouteState] = None

    # ------------------------------------------------------------------ io
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "entries": [e.to_dict() for e in self.entries],
            "pool": [{"uid": p.uid, "ref": p.ref.to_dict()} for p in self.pool],
            "route": self.route.to_dict() if self.route else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SetModel":
        m = cls(d.get("name", "Set"), d.get("id"))
        m.entries = [Entry.from_dict(e) for e in d.get("entries", [])]
        m.pool = [PoolItem(TrackRef.from_dict(p["ref"]), p.get("uid") or new_uid()) for p in d.get("pool", [])]
        m.route = RouteState.from_dict(d["route"]) if d.get("route") else None
        return m

    @classmethod
    def from_tracks(cls, name: str, tracks: Iterable[Track]) -> "SetModel":
        m = cls(name)
        m.entries = [Entry(REAL, TrackRef.of(t)) for t in tracks]
        return m

    def copy(self, name: Optional[str] = None) -> "SetModel":
        m = SetModel.from_dict(self.to_dict())
        m.id = new_uid()
        if name:
            m.name = name
        return m

    # ------------------------------------------------------------ queries
    def index_of(self, uid: str) -> int:
        for i, e in enumerate(self.entries):
            if e.uid == uid:
                return i
        return -1

    def entry(self, uid: str) -> Optional[Entry]:
        i = self.index_of(uid)
        return self.entries[i] if i >= 0 else None

    def real_entries(self) -> list[Entry]:
        return [e for e in self.entries if e.is_real]

    def last_real(self) -> Optional[Entry]:
        for e in reversed(self.entries):
            if e.is_real:
                return e
        return None

    def pool_item(self, uid: str) -> Optional[PoolItem]:
        return next((p for p in self.pool if p.uid == uid), None)

    def pool_track_ids(self) -> set[int]:
        return {p.ref.track_id for p in self.pool}

    def set_track_ids(self) -> set[int]:
        return {e.ref.track_id for e in self.entries if e.is_real}

    def has_unfilled(self) -> bool:
        return any(e.kind != REAL for e in self.entries)

    def active_slot_uid(self) -> Optional[str]:
        return next((e.uid for e in self.entries if e.kind == SLOT), None)

    def slot_position(self, uid: str) -> tuple[int, int]:
        """(1-based index, total) of a slot within the open route."""
        slots = [e.uid for e in self.entries if e.kind == SLOT]
        return (slots.index(uid) + 1 if uid in slots else 0, len(slots))

    def route_zone(self) -> Optional[tuple[int, int]]:
        """Inclusive range of insertion indices that count as a drop onto the
        active transitional entry."""
        r = self.route
        if not r:
            return None
        a = self.index_of(r.anchor_uid)
        if a < 0:
            return None
        if r.kind == POOL_ROUTE:
            return (a + 1, len(self.entries))
        b = self.index_of(r.target_uid)
        return (a + 1, b) if b > a else None

    def route_target_track(self, ctx: Context) -> Optional[Track]:
        r = self.route
        if not r:
            return None
        if r.kind == POOL_ROUTE:
            p = self.pool_item(r.target_uid)
            return ctx.resolve(p.ref) if p else None
        e = self.entry(r.target_uid)
        return ctx.resolve(e.ref) if e and e.ref else None

    # ------------------------------------------------------ plain editing
    def insert_tracks(self, tracks: Sequence[Track], index: Optional[int], ctx: Context) -> Outcome:
        """Insert tracks at ``index`` (None = append). A drop inside the open
        route's block fills the active transitional entry."""
        if index is None:
            index = len(self.entries)
        zone = self.route_zone()
        if zone and zone[0] <= index <= zone[1]:
            return self.fill(tracks, ctx)
        for off, t in enumerate(tracks):
            self.entries.insert(index + off, self._take(TrackRef.of(t)))
        self._normalize(ctx)  # closes a pool route whose target was just placed
        return Outcome()

    def _take(self, ref: TrackRef) -> Entry:
        """A new set entry for ``ref``; a matching To be added track leaves the pool."""
        e = Entry(REAL, ref)
        hit = next((p for p in self.pool if p.ref.track_id == ref.track_id), None)
        if hit:
            self.pool.remove(hit)
            e.from_pool = True
        return e

    def move_entries(self, uids: Sequence[str], index: int, ctx: Context) -> Outcome:
        """Reorder real rows. Dropping them onto the route block moves them into it."""
        moving = [e for e in self.entries if e.uid in set(uids) and e.is_real]
        if not moving:
            return Outcome()
        zone = self.route_zone()
        if zone and zone[0] <= index <= zone[1]:
            anchor_moving = self.route and any(e.uid == self.route.anchor_uid for e in moving)
            target_moving = self.route and any(e.uid == self.route.target_uid for e in moving)
            if not anchor_moving and not target_moving:
                tracks = [ctx.resolve(e.ref) for e in moving]
                if all(tracks):
                    self.entries = [e for e in self.entries if e not in moving]
                    return self.fill(tracks, ctx, moving)
        before = sum(1 for e in self.entries[:index] if e in moving)
        rest = [e for e in self.entries if e not in moving]
        index -= before
        self.entries = rest[:index] + moving + rest[index:]
        self._normalize(ctx)
        return Outcome()

    def remove_entries(self, uids: Iterable[str], ctx: Context) -> None:
        uids = set(uids)
        removed = [e for e in self.entries if e.uid in uids and e.is_real]
        self.entries = [e for e in self.entries if e not in removed]
        # Tracks that came from To be added go back there, unless still in the set.
        still = self.set_track_ids() | self.pool_track_ids()
        for e in removed:
            if e.from_pool and e.ref.track_id not in still:
                self.pool.append(PoolItem(e.ref))
                still.add(e.ref.track_id)
        self._normalize(ctx)

    def add_to_pool(self, tracks: Sequence[Track]) -> None:
        have = self.pool_track_ids()
        for t in tracks:
            if t.id not in have:
                self.pool.append(PoolItem(TrackRef.of(t)))
                have.add(t.id)

    def remove_from_pool(self, uid: str, ctx: Context) -> None:
        self.pool = [p for p in self.pool if p.uid != uid]
        self._normalize(ctx)

    def insert_pool_item(self, pool_uid: str, real_gap: int, ctx: Context) -> None:
        """Place a pool track before the ``real_gap``-th real track; it leaves the pool."""
        p = self.pool_item(pool_uid)
        if not p:
            return
        reals = self.real_entries()
        if real_gap >= len(reals):
            last = reals[-1] if reals else None
            idx = self.index_of(last.uid) + 1 if last else 0
        else:
            idx = self.index_of(reals[real_gap].uid)
        if self.route and self.route.kind == POOL_ROUTE and self.route.target_uid == pool_uid:
            self.close_route()
        self.pool.remove(p)
        self.entries.insert(idx, Entry(REAL, p.ref, from_pool=True))
        self._normalize(ctx)

    # -------------------------------------------------------------- routes
    def close_route(self) -> None:
        self.entries = [e for e in self.entries if e.is_real]
        self.route = None

    def open_pool_route(self, pool_uid: str, ctx: Context) -> Outcome:
        self.close_route()
        p = self.pool_item(pool_uid)
        if not p:
            return Outcome("That track is no longer in To be added.")
        anchor = self.last_real()
        if anchor is None:
            return Outcome("The set is empty: there is no track to route from.")
        self.route = RouteState(POOL_ROUTE, pool_uid, anchor.uid)
        return self._recompute(ctx)

    def place_as_opener(self, pool_uid: str, ctx: Context) -> None:
        self.insert_pool_item(pool_uid, 0, ctx)

    def open_fix_route(self, b_uid: str, ctx: Context) -> Outcome:
        self.close_route()
        b = self.index_of(b_uid)
        a = next((e for e in reversed(self.entries[:b]) if e.is_real), None) if b > 0 else None
        if a is None:
            return Outcome("There is no track before this one to bridge from.")
        self.route = RouteState(FIX_ROUTE, b_uid, a.uid)
        return self._recompute(ctx)

    def fill(self, tracks: Sequence[Track], ctx: Context, moved: Optional[Sequence[Entry]] = None) -> Outcome:
        """Fill the active transitional entry with each track in turn. ``moved``
        are existing setlist rows being dragged onto the slot (kept, not copied)."""
        out = Outcome()
        for n, t in enumerate(tracks):
            if not self.route:  # completed by an earlier track: plain append
                self.entries.append(moved[n] if moved else self._take(TrackRef.of(t)))
                continue
            slot_entry = next((e for e in self.entries if e.kind == SLOT), None)
            fitted = bool(slot_entry and route_mod.fits_slot(t, slot_entry.slot))
            pool_hit = next((p for p in self.pool if p.ref.track_id == t.id), None)
            a = self.index_of(self.route.anchor_uid)
            new = moved[n] if moved else Entry(REAL, TrackRef.of(t))
            new.from_pool = new.from_pool or bool(pool_hit)
            if pool_hit and self.route.kind == POOL_ROUTE and self.route.target_uid == pool_hit.uid:
                # Dropped the target itself: the route is done, whatever the key.
                self._strip_route_entries()
                self.route = None
                self.pool.remove(pool_hit)
                self.entries.insert(a + 1, new)
                return Outcome("Target placed; route closed.", completed=True, fitted=fitted)
            if pool_hit:
                self.pool.remove(pool_hit)
            self.entries.insert(a + 1, new)
            self.route.anchor_uid = new.uid
            out = self._recompute(ctx)
            out.fitted = fitted
        return out

    def _strip_route_entries(self) -> list[Entry]:
        removed = [e for e in self.entries if not e.is_real]
        self.entries = [e for e in self.entries if e.is_real]
        return removed

    def _recompute(self, ctx: Context) -> Outcome:
        r = self.route
        self._strip_route_entries()
        anchor_e = self.entry(r.anchor_uid)
        anchor = ctx.resolve(anchor_e.ref) if anchor_e else None
        target = self.route_target_track(ctx)
        if anchor is None or target is None:
            self.close_route()
            return Outcome("Route closed: its anchor or target is missing from the library.")
        res = route_mod.find_route_with_fallback(anchor, target, ctx.focused, ctx.library, ctx.settings)
        a_idx = self.index_of(r.anchor_uid)
        if res is not None and res.hops == 1:
            return self._complete(ctx)
        r.status = "ok" if res else "unreachable"
        r.from_library = bool(res and res.from_library)
        if res is None:
            hint = route_mod.suggest_relaxation(anchor, target, ctx.library or ctx.focused, ctx.settings)
            r.message = "Unreachable with current rules." + (f" Try: {hint}." if hint else "")
        else:
            r.message = f"{len(res.slots)} transition{'s' if len(res.slots) != 1 else ''} to go" + (
                " (from library)" if res.from_library else ""
            )
        block = [Entry(SLOT, slot=s) for s in (res.slots if res else [])]
        if r.kind == POOL_ROUTE:
            block.append(Entry(TARGET, TrackRef.of(target)))
        self.entries[a_idx + 1:a_idx + 1] = block
        return Outcome(r.message)

    def _complete(self, ctx: Context) -> Outcome:
        r = self.route
        if r.kind == POOL_ROUTE:
            p = self.pool_item(r.target_uid)
            a_idx = self.index_of(r.anchor_uid)
            self.route = None
            self.pool.remove(p)
            self.entries.insert(a_idx + 1, Entry(REAL, p.ref, from_pool=True))
            return Outcome("Route complete: target added to the set.", completed=True)
        self.route = None
        return Outcome("Key mistake fixed.", completed=True)

    def _normalize(self, ctx: Context) -> None:
        """Re-establish the route layout after a generic edit (rule 10)."""
        r = self.route
        if not r:
            self.entries = [e for e in self.entries if e.is_real]
            return
        old_block = self._strip_route_entries()
        if r.kind == POOL_ROUTE:
            if not self.pool_item(r.target_uid):
                self.route = None
                return
            last = self.last_real()
            if last is None:
                self.route = None
                return
            if last.uid != r.anchor_uid:
                r.anchor_uid = last.uid
                self._recompute(ctx)
                return
            self.entries.extend(old_block)
            return
        b = self.index_of(r.target_uid)
        a = next((e for e in reversed(self.entries[:b]) if e.is_real), None) if b > 0 else None
        if b < 0 or a is None:
            self.route = None
            return
        if a.uid != r.anchor_uid:
            r.anchor_uid = a.uid
            self._recompute(ctx)
            return
        self.entries[b:b] = old_block

    # ------------------------------------------------------------ analysis
    def analyze(self, ctx: Context) -> list["RowInfo"]:
        """Per-entry relation, clash/duplicate flags and graph moves."""
        s = ctx.settings
        counts = Counter(e.ref.track_id for e in self.entries if e.is_real)
        rows: list[RowInfo] = []
        prev_real: Optional[Track] = None
        prev_any: Optional[Track] = None
        for e in self.entries:
            if e.kind == SLOT:
                rep = ctx.by_id(e.slot.candidates[0]) if e.slot.candidates else None
                track = None
            else:
                rep = track = ctx.resolve(e.ref)
            ref_for_rel = prev_real if e.is_real else prev_any
            rel = relate(ref_for_rel, rep, s) if (rep and ref_for_rel) else None
            graph_rel = relate(prev_any, rep, s) if (rep and prev_any) else None
            rows.append(
                RowInfo(
                    entry=e,
                    track=track,
                    rep=rep,
                    rel=rel,
                    clash=bool(e.is_real and rel and rel.is_clash),
                    duplicate=bool(e.is_real and counts[e.ref.track_id] > 1),
                    missing=bool(e.kind != SLOT and track is None),
                    graph_move=graph_rel.move if graph_rel else None,
                )
            )
            if rep is not None:
                prev_any = rep
                if e.is_real:
                    prev_real = rep
        return rows

    def energy(self, rows: Sequence["RowInfo"], s: Settings) -> list[EnergyPoint]:
        tags = [r.rep.energy_tag if r.rep else None for r in rows]
        unknown = [r.rep is None or r.rep.key is None for r in rows]
        return series_from_moves([r.graph_move for r in rows], s, tags, unknown)


@dataclass
class RowInfo:
    entry: Entry
    track: Optional[Track]  # resolved real/target track
    rep: Optional[Track]  # track used for maths (slot: best candidate)
    rel: Optional[Relation]
    clash: bool
    duplicate: bool
    missing: bool
    graph_move: object = None
