from harmonic_set_builder.core.route import find_route, find_route_with_fallback, suggest_placements, suggest_relaxation
from harmonic_set_builder.core.settings import Settings

from .helpers import tr

STRICT = Settings(energy_moves_in_key=False)

A = tr(1, "8A")
T = tr(2, "11A")  # 8A -> 11A is off-key
N9 = tr(3, "9A")
N10 = tr(4, "10A")
N10_FAST = tr(5, "10A", bpm=140)  # danger BPM: never an edge
N2 = tr(6, "2A")
LIB = [A, T, N9, N10, N10_FAST, N2]


def test_direct_is_one_hop():
    assert find_route(A, N9, LIB, Settings()).hops == 1


def test_shortest_with_energy_moves():
    r = find_route(A, T, LIB, Settings())
    assert r.hops == 2 and len(r.slots) == 1
    assert set(r.slots[0].candidates) == {3, 4}  # 9A (+1 then +2) or 10A (+2 then +1)
    assert [str(k) for k in r.slots[0].keys] == ["9A", "10A"]


def test_strict_route_needs_two_slots():
    r = find_route(A, T, LIB, STRICT)
    assert r.hops == 3
    assert [s.candidates for s in r.slots] == [[3], [4]]
    assert r.slots[1].bpm_text() == "128"


def test_every_slot_candidate_is_on_a_shortest_path():
    lib = LIB + [tr(7, "9B"), tr(8, "10B"), tr(9, "7A")]
    r = find_route(A, T, lib, STRICT)
    assert r.hops == 3
    assert 9 not in r.slots[0].candidates  # 7A moves away from the target


def test_tie_break_prefers_smooth():
    # 8A -> 8B (smooth) -> ... vs 8A -> 10A (energy): both reach 10B in 2 hops.
    tgt = tr(20, "10B")
    lib = [A, tgt, tr(21, "9B"), tr(22, "10A"), tr(23, "9A")]
    r = find_route(A, tgt, lib, Settings())
    assert r.hops == 2
    # 9A (+1 smooth, then diagonal smooth) costs 2.0; 10A (+2 energy, then rel. major) 2.5
    assert r.slots[0].candidates[0] in (21, 23)
    assert r.slots[0].candidates[-1] == 22


def test_unreachable_and_relaxation():
    lib = [A, T, tr(3, "9A", bpm=133), tr(4, "10A", bpm=133)]  # caution steps only
    assert find_route(A, T, lib, STRICT) is None
    assert "Caution" in suggest_relaxation(A, T, lib, STRICT)


def test_hop_cap():
    s = Settings(energy_moves_in_key=False, route_max_hops=2)
    assert find_route(A, T, LIB, s) is None


def test_fallback_to_library():
    r = find_route_with_fallback(A, T, [A, T], LIB, Settings())
    assert r and r.from_library and r.slots[0].from_library


def test_unknown_key_tracks_excluded():
    lib = [A, T, tr(3, None), tr(4, "10A", bpm=None)]
    assert find_route(A, T, lib, Settings()) is None


def test_half_time_edge():
    dnb = tr(30, "8A", bpm=174)
    half = tr(31, "9A", bpm=87)
    assert find_route(dnb, half, [dnb, half], Settings()).hops == 1


def test_placements():
    setl = [tr(1, "8A"), tr(2, "10A"), tr(3, "3B")]
    cand = tr(9, "9A")
    gaps = suggest_placements(setl, cand, Settings())
    # 9A -> 3B and 3B -> 9A are tritones, so gaps 2 and 3 are excluded.
    assert {g.gap for g in gaps} == {0, 1}
    assert all(g.worst_tier == "smooth" for g in gaps)


def test_placement_ranking_puts_energy_moves_last():
    setl = [tr(1, "8A"), tr(2, "8A")]
    gaps = suggest_placements(setl, tr(9, "10A"), Settings())
    assert [g.worst_tier for g in gaps] == ["energy"] * 3
    gaps = suggest_placements([tr(1, "8A"), tr(2, "11A")], tr(9, "9A"), Settings())
    assert gaps[0].worst_tier == "smooth" and gaps[-1].worst_tier == "energy"
