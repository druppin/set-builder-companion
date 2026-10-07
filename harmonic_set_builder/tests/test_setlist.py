import pytest

from harmonic_set_builder.core.settings import Settings
from harmonic_set_builder.core.setlist import FIX_ROUTE, POOL_ROUTE, REAL, SLOT, TARGET, SetModel

from .helpers import Lib, tr

STRICT = Settings(energy_moves_in_key=False)

A, T, N9, N10, N2, N7 = tr(1, "8A"), tr(2, "11A"), tr(3, "9A"), tr(4, "10A"), tr(6, "2A"), tr(7, "7A")
LIB = Lib([A, T, N9, N10, N2, N7])


def kinds(m):
    return [e.kind for e in m.entries]


def ids(m):
    return [e.ref.track_id if e.ref else None for e in m.entries]


@pytest.fixture
def ctx():
    return LIB.ctx(STRICT)


@pytest.fixture
def routed(ctx):
    """Set [A] with T in the pool and a pool route open: A, slot(9A), slot(10A), T."""
    m = SetModel("t")
    m.insert_tracks([A], None, ctx)
    m.add_to_pool([T])
    m.open_pool_route(m.pool[0].uid, ctx)
    return m


def test_open_pool_route_layout(routed):
    assert kinds(routed) == [REAL, SLOT, SLOT, TARGET]
    assert routed.route.kind == POOL_ROUTE
    assert routed.active_slot_uid() == routed.entries[1].uid
    assert routed.slot_position(routed.entries[2].uid) == (2, 2)
    assert routed.has_unfilled()


def test_fill_fitting_tracks_completes_route(routed, ctx):
    out = routed.fill([N9], ctx)
    assert out.fitted and kinds(routed) == [REAL, REAL, SLOT, TARGET]
    out = routed.fill([N10], ctx)
    assert out.completed
    assert ids(routed) == [1, 3, 4, 2] and routed.route is None and routed.pool == []


def test_drop_in_zone_fills(routed, ctx):
    routed.insert_tracks([N9], len(routed.entries), ctx)  # dropped at the end
    assert ids(routed)[:2] == [1, 3] and routed.route is not None


def test_non_fitting_drop_becomes_anchor_and_route_recomputes(routed, ctx):
    out = routed.fill([N7], ctx)  # 7A: away from target
    assert out.fitted is False
    assert ids(routed)[:2] == [1, 7]
    assert routed.route.anchor_uid == routed.entries[1].uid
    assert kinds(routed) == [REAL, REAL, SLOT, SLOT, SLOT, TARGET]  # 7A->8A->9A->10A->11A
    assert routed.analyze(ctx)[1].clash is False  # 8A -> 7A is fine


def test_insert_before_anchor_keeps_route(routed, ctx):
    slots_before = [e.slot.candidates for e in routed.entries if e.kind == SLOT]
    routed.insert_tracks([N2], 0, ctx)
    assert ids(routed)[0] == 6
    assert [e.slot.candidates for e in routed.entries if e.kind == SLOT] == slots_before


def test_deleting_anchor_recomputes_from_new_last(routed, ctx):
    routed.insert_tracks([N9], 0, ctx)  # [9A, A, slots..., T]
    routed.remove_entries([routed.entries[1].uid], ctx)  # delete anchor A
    assert ids(routed)[0] == 3
    assert routed.route.anchor_uid == routed.entries[0].uid
    assert kinds(routed) == [REAL, SLOT, TARGET]


def test_removing_target_from_pool_closes_route(routed, ctx):
    routed.remove_from_pool(routed.pool[0].uid, ctx)
    assert routed.route is None and kinds(routed) == [REAL]


def test_close_route_keeps_filled(routed, ctx):
    routed.fill([N9], ctx)
    routed.close_route()
    assert ids(routed) == [1, 3] and routed.pool[0].ref.track_id == 2


def test_pool_track_used_to_fill_leaves_pool(routed, ctx):
    routed.add_to_pool([N9])
    routed.fill([N9], ctx)
    assert [p.ref.track_id for p in routed.pool] == [2]


def test_filling_with_target_closes(routed, ctx):
    out = routed.fill([T], ctx)
    assert out.completed and ids(routed) == [1, 2] and routed.pool == []


def test_duplicate_fill_is_flagged(ctx):
    m = SetModel()
    m.insert_tracks([N9, A], None, ctx)  # [9A, 8A]
    m.add_to_pool([T])
    m.open_pool_route(m.pool[0].uid, ctx)
    m.fill([N9], ctx)  # copy of an existing set track
    rows = m.analyze(ctx)
    assert rows[0].duplicate and rows[2].duplicate and not rows[1].duplicate


def test_moving_set_row_onto_slot_moves_it(ctx):
    m = SetModel()
    m.insert_tracks([N9, A], None, ctx)
    m.add_to_pool([T])
    m.open_pool_route(m.pool[0].uid, ctx)
    nine = m.entries[0].uid
    m.move_entries([nine], len(m.entries), ctx)
    assert ids(m)[:2] == [1, 3] and m.entries[1].uid == nine


def test_direct_target_completes_immediately(ctx):
    m = SetModel()
    m.insert_tracks([A], None, ctx)
    m.add_to_pool([N9])
    out = m.open_pool_route(m.pool[0].uid, ctx)
    assert out.completed and ids(m) == [1, 3] and m.pool == []


def test_empty_set_has_no_anchor(ctx):
    m = SetModel()
    m.add_to_pool([T])
    out = m.open_pool_route(m.pool[0].uid, ctx)
    assert m.route is None and "empty" in out.message
    m.place_as_opener(m.pool[0].uid, ctx)
    assert ids(m) == [2] and m.pool == []


def test_unreachable_route_reports(ctx):
    lib = Lib([A, T])
    c = lib.ctx(STRICT)
    m = SetModel()
    m.insert_tracks([A], None, c)
    m.add_to_pool([T])
    out = m.open_pool_route(m.pool[0].uid, c)
    assert m.route.status == "unreachable" and "Unreachable" in out.message
    assert kinds(m) == [REAL, TARGET]


def test_focused_source_fallback_to_library():
    c = LIB.ctx(STRICT, focused=[A, T])
    m = SetModel()
    m.insert_tracks([A], None, c)
    m.add_to_pool([T])
    m.open_pool_route(m.pool[0].uid, c)
    assert m.route.from_library and all(e.slot.from_library for e in m.entries if e.kind == SLOT)


# ----------------------------------------------------------- fix routes
@pytest.fixture
def clashing(ctx):
    m = SetModel()
    m.insert_tracks([A, T, N10], None, ctx)  # 8A -> 11A clash, 11A -> 10A fine
    return m


def test_clash_detection(clashing, ctx):
    rows = clashing.analyze(ctx)
    assert [r.clash for r in rows] == [False, True, False]


def test_fix_route_bridges_and_clears_red(clashing, ctx):
    b = clashing.entries[1].uid
    clashing.open_fix_route(b, ctx)
    assert clashing.route.kind == FIX_ROUTE
    assert kinds(clashing) == [REAL, SLOT, SLOT, REAL, REAL]
    assert clashing.analyze(ctx)[3].clash  # B stays red until bridged
    clashing.fill([N9], ctx)
    out = clashing.fill([N10], ctx)
    assert out.completed and clashing.route is None
    assert ids(clashing) == [1, 3, 4, 2, 4]
    assert not any(r.clash for r in clashing.analyze(ctx))


def test_fix_route_non_fit_recomputes_to_b(clashing, ctx):
    clashing.open_fix_route(clashing.entries[1].uid, ctx)
    clashing.fill([N7], ctx)
    assert clashing.route.anchor_uid == clashing.entries[1].uid
    assert clashing.entries[-2].ref.track_id == 2  # B still right after the block


def test_one_route_at_a_time(clashing, ctx):
    clashing.add_to_pool([N2])
    clashing.open_fix_route(clashing.entries[1].uid, ctx)
    clashing.open_pool_route(clashing.pool[0].uid, ctx)
    assert clashing.route.kind == POOL_ROUTE
    assert ids(clashing)[:3] == [1, 2, 4]  # fix slots gone


def test_fix_route_closes_when_b_deleted(clashing, ctx):
    b = clashing.entries[1].uid
    clashing.open_fix_route(b, ctx)
    clashing.remove_entries([b], ctx)
    assert clashing.route is None and kinds(clashing) == [REAL, REAL]


def test_snapshot_round_trip_preserves_route(routed, ctx):
    routed.fill([N9], ctx)
    copy = SetModel.from_dict(routed.to_dict())
    assert copy.to_dict() == routed.to_dict()
    copy.fill([N10], ctx)
    assert copy.route is None


def test_energy_graph_includes_slots(routed, ctx):
    rows = routed.analyze(ctx)
    pts = routed.energy(rows, ctx.settings)
    assert [p.value for p in pts] == [5, 6, 7, 8]


def test_missing_track_reported():
    c = LIB.ctx(STRICT)
    m = SetModel()
    m.insert_tracks([A, tr(99, "8A")], None, c)
    rows = m.analyze(c)
    assert rows[1].missing and not rows[0].missing
