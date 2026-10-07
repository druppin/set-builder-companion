"""Shortest harmonic routes between two tracks, slot candidate sets and
placement suggestions for a single track.

An edge a -> b exists when the transition is in key (Smooth, or Energy move if
allowed) and the BPM Δ is Safe (Caution only when the setting allows it).
"Fastest" = fewest hops; ties go to the smallest summed cost
(Smooth 1.0, Energy move 1.5, Caution BPM +0.5).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from . import bpm as bpm_mod
from .camelot import CLASH, ENERGY, SMOOTH, TIER_ORDER, Key, classify, transpose
from .ranking import relate
from .settings import Settings
from .track import Track

TIER_COST = {SMOOTH: 1.0, ENERGY: 1.5}
CAUTION_COST = 0.5


@dataclass
class Slot:
    candidates: list[int]  # track ids, best (cheapest path) first
    keys: list[Key]
    bpm_min: float
    bpm_max: float
    from_library: bool = False

    def key_text(self, fmt) -> str:
        return " / ".join(fmt(k) for k in self.keys)

    def bpm_text(self) -> str:
        lo, hi = round(self.bpm_min), round(self.bpm_max)
        return f"{lo}" if lo == hi else f"{lo}–{hi}"

    def to_dict(self) -> dict:
        return {
            "candidates": self.candidates,
            "keys": [str(k) for k in self.keys],
            "bpm_min": self.bpm_min,
            "bpm_max": self.bpm_max,
            "from_library": self.from_library,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Slot":
        keys = [Key(int(k[:-1]), k[-1]) for k in d.get("keys", [])]
        return cls(list(d["candidates"]), keys, d["bpm_min"], d["bpm_max"], d.get("from_library", False))


@dataclass
class RouteResult:
    hops: int  # edges from anchor to target; slots = hops - 1
    slots: list[Slot] = field(default_factory=list)
    from_library: bool = False


class _Edges:
    """Fast edge test with a cached key-pair classification."""

    def __init__(self, s: Settings):
        self.s = s
        self.moves = s.moves()
        self.cache: dict[tuple, Optional[str]] = {}

    def cost(self, a: Track, b: Track) -> Optional[float]:
        s = self.s
        d = bpm_mod.compare(a.bpm, b.bpm, s.bpm)
        if d is None or d.band == bpm_mod.DANGER:
            return None
        if d.band == bpm_mod.CAUTION and not s.route_allow_caution:
            return None
        k2 = b.key
        if not s.keylock and k2 is not None:
            k2 = transpose(k2, bpm_mod.keylock_shift(a.bpm, b.bpm, d.factor))
        pair = (a.key, k2)
        if pair not in self.cache:
            mv = classify(a.key, k2, self.moves, s.energy_moves_in_key)
            self.cache[pair] = mv.tier if mv else None
        tier = self.cache[pair]
        if tier is None or tier == CLASH:
            return None
        return TIER_COST[tier] + (CAUTION_COST if d.band == bpm_mod.CAUTION else 0.0)


def find_route(anchor: Track, target: Track, candidates: Iterable[Track], s: Settings) -> Optional[RouteResult]:
    """Minimum-hop route anchor -> target through ``candidates``.
    Returns None when unreachable within ``s.route_max_hops``."""
    if not (anchor.mixable and target.mixable):
        return None
    edges = _Edges(s)
    if edges.cost(anchor, target) is not None:
        return RouteResult(1)
    seen = {anchor.id, target.id}
    nodes: list[Track] = []
    for t in candidates:
        if t.mixable and t.id not in seen:
            seen.add(t.id)
            nodes.append(t)
    max_mid = s.route_max_hops - 1  # max intermediate tracks

    # Backward BFS: dist_t[i] = hops from node i to the target.
    dist_t: dict[int, int] = {}
    frontier = [i for i, n in enumerate(nodes) if edges.cost(n, target) is not None]
    for i in frontier:
        dist_t[i] = 1
    depth = 1
    while frontier and depth < max_mid:
        depth += 1
        nxt = []
        for i, n in enumerate(nodes):
            if i in dist_t:
                continue
            if any(edges.cost(n, nodes[j]) is not None for j in frontier):
                dist_t[i] = depth
                nxt.append(i)
        frontier = nxt

    # Forward BFS from the anchor. The first layer touching a node that reaches
    # the target fixes L (dist_t is a true shortest distance); keep expanding to
    # depth L-1 so every slot layer has its dist_a labels.
    dist_a: dict[int, int] = {}
    frontier = [i for i, n in enumerate(nodes) if edges.cost(anchor, n) is not None]
    for i in frontier:
        dist_a[i] = 1
    best_L = None
    depth = 1
    while frontier:
        if best_L is None:
            reach = [dist_a[i] + dist_t[i] for i in frontier if i in dist_t]
            if reach:
                best_L = min(reach)
        if depth >= (best_L - 1 if best_L else max_mid):
            break
        depth += 1
        nxt = []
        for i, n in enumerate(nodes):
            if i in dist_a:
                continue
            if any(edges.cost(nodes[j], n) is not None for j in frontier):
                dist_a[i] = depth
                nxt.append(i)
        frontier = nxt
    if best_L is None or best_L > s.route_max_hops:
        return None

    layers = [
        [i for i in dist_a if dist_a[i] == k and dist_t.get(i) == best_L - k]
        for k in range(1, best_L)
    ]
    # Cheapest cost from the anchor to each on-path node, and from it to the target.
    ca: dict[int, float] = {i: edges.cost(anchor, nodes[i]) for i in layers[0]}
    for k in range(1, len(layers)):
        for i in layers[k]:
            ca[i] = min(
                ca[j] + c for j in layers[k - 1]
                if (c := edges.cost(nodes[j], nodes[i])) is not None
            )
    ct: dict[int, float] = {i: edges.cost(nodes[i], target) for i in layers[-1]}
    for k in range(len(layers) - 2, -1, -1):
        for i in layers[k]:
            ct[i] = min(
                ct[j] + c for j in layers[k + 1]
                if (c := edges.cost(nodes[i], nodes[j])) is not None
            )
    slots = []
    for layer in layers:
        order = sorted(layer, key=lambda i: (ca[i] + ct[i], -(nodes[i].rating or 0), nodes[i].id))
        tracks = [nodes[i] for i in order]
        keys = sorted({t.key for t in tracks}, key=lambda k: (k.number, k.mode))
        slots.append(Slot([t.id for t in tracks], keys, min(t.bpm for t in tracks), max(t.bpm for t in tracks)))
    return RouteResult(best_L, slots)


def find_route_with_fallback(
    anchor: Track, target: Track, focused: Sequence[Track], library: Sequence[Track], s: Settings
) -> Optional[RouteResult]:
    r = find_route(anchor, target, focused, s)
    if r is None and s.library_fallback and len(library) > len(focused):
        r = find_route(anchor, target, library, s)
        if r is not None:
            r.from_library = True
            for slot in r.slots:
                slot.from_library = True
    return r


def suggest_relaxation(anchor: Track, target: Track, tracks: Sequence[Track], s: Settings) -> Optional[str]:
    """Which single rule, if relaxed, would make the target reachable."""
    if not anchor.mixable:
        return "The anchor track has no key or BPM."
    if not target.mixable:
        return "The target track has no key or BPM."
    options = [
        ("route_allow_caution", True, "Allow Caution BPM steps in routes"),
        ("energy_moves_in_key", True, "Count Energy moves (+2 / semitone) as in key"),
        ("route_max_hops", 16, "Raise the route hop cap"),
    ]
    for attr, value, text in options:
        if getattr(s, attr) == value:
            continue
        if find_route(anchor, target, tracks, dataclasses.replace(s, **{attr: value})):
            return text
    if not s.bpm.half_double:
        relaxed = dataclasses.replace(s, bpm=dataclasses.replace(s.bpm, half_double=True))
        if find_route(anchor, target, tracks, relaxed):
            return "Enable half/double-time matching"
    wider = dataclasses.replace(s, bpm=dataclasses.replace(s.bpm, safe=s.bpm.caution))
    if find_route(anchor, target, tracks, wider):
        return f"Widen the Safe BPM band to {s.bpm.caution:g}"
    return None


def fits_slot(track: Track, slot: Slot) -> bool:
    return track.id in slot.candidates


@dataclass(frozen=True)
class Placement:
    gap: int  # insert before real-track index ``gap`` (0 = start, n = end)
    worst_tier: str
    worst_band: Optional[str]


def suggest_placements(real_tracks: Sequence[Track], track: Track, s: Settings) -> list[Placement]:
    """Every gap where inserting ``track`` keeps both neighboring transitions non-clash."""
    if not track.has_key:
        return []
    out = []
    for gap in range(len(real_tracks) + 1):
        rels = []
        if gap > 0:
            rels.append(relate(real_tracks[gap - 1], track, s))
        if gap < len(real_tracks):
            rels.append(relate(track, real_tracks[gap], s))
        if any(r.is_clash for r in rels):
            continue
        tiers = [r.move.tier for r in rels if r.move] or [SMOOTH]
        bands = [r.bpm.band for r in rels if r.bpm]
        worst_tier = max(tiers, key=TIER_ORDER.__getitem__)
        worst_band = max(bands, key=bpm_mod.BAND_ORDER.__getitem__) if bands else None
        out.append(Placement(gap, worst_tier, worst_band))
    out.sort(key=lambda p: (TIER_ORDER[p.worst_tier], bpm_mod.BAND_ORDER.get(p.worst_band, 3), p.gap))
    return out
