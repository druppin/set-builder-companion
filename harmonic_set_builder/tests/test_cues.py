import hashlib
import sqlite3

import pytest

from harmonic_set_builder.analysis.labels import BREAKDOWN, BUILD, DROP, INTRO, OUTRO, Section
from harmonic_set_builder.data import mixxx_cues as mc
from harmonic_set_builder.data.mixxx_cues import ExistingCue

SR = 44100
CUES_SCHEMA = """
CREATE TABLE library (id INTEGER PRIMARY KEY, samplerate INTEGER);
CREATE TABLE cues (id INTEGER PRIMARY KEY AUTOINCREMENT, track_id INTEGER NOT NULL REFERENCES "library_old"(id),
  type INTEGER DEFAULT 0 NOT NULL, position INTEGER DEFAULT -1 NOT NULL, length INTEGER DEFAULT 0 NOT NULL,
  hotcue INTEGER DEFAULT -1 NOT NULL, label TEXT DEFAULT '' NOT NULL, color INTEGER DEFAULT 4294901760 NOT NULL);
"""


def pos(sec):
    return sec * SR * 2


def secs(*spec):
    out, counts = [], {}
    for label, a, b in spec:
        counts[label] = counts.get(label, 0) + 1
        out.append(Section(label, counts[label], a, b, a * 2.0, b * 2.0, 0.5))
    for s in out:
        s.repeated = sum(1 for x in out if x.label == s.label) > 1
    return out


SECTIONS = secs((INTRO, 0, 16), (BUILD, 16, 24), (DROP, 24, 56), (BREAKDOWN, 56, 72), (BUILD, 72, 80),
                (DROP, 80, 112), (OUTRO, 112, 128))


def plan(existing=(), own=frozenset(), **kw):
    return mc.plan_track(1, "/m/a.mp3", "A – Track", SR, SECTIONS, list(existing), set(own), **kw)


def ops(p, kind=None):
    return [o for o in p.ops if kind is None or o.kind == kind]


def test_fresh_track_gets_hot_cues_and_markers():
    p = plan()
    hot = ops(p, "hotcue")
    assert [(o.hotcue, o.label) for o in hot] == [
        (0, "◆ Intro"), (1, "◆ Build 1"), (2, "◆ Drop 1"), (3, "◆ Breakdown"), (4, "◆ Build 2"), (5, "◆ Drop 2"),
        (6, "◆ Outro")]
    assert hot[2].position == pos(48.0) and hot[2].color == mc.COLORS[DROP]
    intro = ops(p, "intro")[0]
    assert (intro.action, intro.type, intro.position, intro.length) == ("insert", mc.INTRO_CUE, 0, pos(32.0))
    outro = ops(p, "outro")[0]
    assert (outro.position, outro.length) == (pos(224.0), pos(32.0))


def test_never_touches_user_hot_cues_and_respects_the_cap():
    user = [ExistingCue(100 + s, mc.HOTCUE, pos(s * 10 + 1), 0, s, "", 0xFF8000) for s in range(4)]
    p = plan(user, max_hotcues=8)
    hot = ops(p, "hotcue")
    assert all(o.action == "insert" for o in hot)
    assert [o.hotcue for o in hot] == [4, 5, 6, 7]
    # Priority keeps the drops, the breakdown and the outro.
    assert [o.label for o in hot] == ["◆ Drop 1", "◆ Breakdown", "◆ Drop 2", "◆ Outro"]
    assert sum("no free hot cue slot" in s for s in p.skipped) == 3


def test_existing_hot_cue_at_a_section_is_not_duplicated():
    user = [ExistingCue(9, mc.HOTCUE, pos(48.1), 0, 0, "my drop", 0)]
    p = plan(user)
    assert "◆ Drop 1" not in [o.label for o in ops(p, "hotcue")]
    assert any("already have hot cue 1 there" in s for s in p.skipped)


def test_own_cues_only_replaced_when_asked():
    mine = [ExistingCue(50, mc.HOTCUE, pos(48.0), 0, 2, "◆ Drop 1", 0), ExistingCue(51, mc.HOTCUE, pos(1), 0, 3, "x", 0)]
    p = plan(mine, own={51})
    assert not ops(p, "hotcue") and "earlier export" in p.skipped[0]
    p = plan(mine, own={51}, replace_own=True)
    assert {o.cue_id for o in p.ops if o.action == "delete"} == {50, 51}
    assert [o.hotcue for o in ops(p, "hotcue") if o.action == "insert"][:2] == [0, 1]


def test_markers_are_completed_never_moved():
    auto_intro = ExistingCue(7, mc.INTRO_CUE, pos(0.3), 0, -1, "", 0)  # Mixxx: start only
    auto_outro = ExistingCue(8, mc.OUTRO_CUE, -1, pos(250.0), -1, "", 0)  # Mixxx: end only
    p = plan([auto_intro, auto_outro])
    i, o = ops(p, "intro")[0], ops(p, "outro")[0]
    assert (i.action, i.cue_id, i.position, i.length) == ("update", 7, pos(0.3), pos(32.0) - pos(0.3))
    assert (o.action, o.cue_id, o.position, o.position + o.length) == ("update", 8, pos(224.0), pos(250.0))
    full = ExistingCue(7, mc.INTRO_CUE, pos(1.0), pos(20.0), -1, "", 0)
    p = plan([full])
    assert not ops(p, "intro") and any("complete intro marker" in s for s in p.skipped)
    p = plan([auto_intro], complete_markers=False)
    assert not ops(p, "intro")


def test_no_samplerate_or_sections_skips():
    assert mc.plan_track(1, "/a", "t", 0, SECTIONS, [], set()).ops == []
    assert mc.plan_track(1, "/a", "t", SR, [], [], set()).skipped == ["not analyzed"]


# --------------------------------------------------------- database writes
@pytest.fixture
def db(tmp_path):
    p = tmp_path / "mixxxdb.sqlite"
    c = sqlite3.connect(p)
    c.executescript(CUES_SCHEMA)
    c.execute("INSERT INTO library VALUES (1, 44100)")
    c.execute("INSERT INTO cues (track_id, type, position, length, hotcue, label, color) VALUES (1, 1, ?, 0, 0, '', 16744448)",
              (pos(10.0),))
    c.execute("INSERT INTO cues (track_id, type, position, length, hotcue) VALUES (1, 6, ?, 0, -1)", (pos(0.3),))
    c.commit()
    c.close()
    return p


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_dry_run_plan_touches_nothing(db):
    before = digest(db)
    plans = mc.plan(db, [(1, "/a", "A", SR, SECTIONS)], {})
    assert ops(plans[0], "hotcue")[0].hotcue == 1  # slot 0 is the user's
    assert digest(db) == before
    assert not list(db.parent.glob("*.hsb-backup-*"))


def test_apply_backs_up_writes_in_one_transaction(db, monkeypatch):
    monkeypatch.setattr(mc, "mixxx_running", lambda: False)
    dump = lambda p: list(sqlite3.connect(p).iterdump())  # noqa: E731
    before = dump(db)
    plans = mc.plan(db, [(1, "/a", "A", SR, SECTIONS)], {})
    bak, inserted, deleted = mc.apply(db, plans)
    assert bak.name.startswith("mixxxdb.sqlite.hsb-backup-") and dump(bak) == before
    c = sqlite3.connect(db)
    rows = c.execute("SELECT type, position, length, hotcue, label FROM cues ORDER BY id").fetchall()
    assert rows[0] == (1, pos(10.0), 0, 0, "")  # the user's hot cue is untouched
    assert rows[1][:2] == (6, pos(0.3)) and rows[1][2] == pytest.approx(pos(32.0) - pos(0.3))
    assert len([r for r in rows if r[0] == 1]) == 8
    assert {k for _, _, k in inserted} == {"hotcue", "outro"} and not deleted


def test_apply_refuses_while_mixxx_runs(db, monkeypatch):
    monkeypatch.setattr(mc, "mixxx_running", lambda: True)
    before = digest(db)
    with pytest.raises(mc.CueExportError, match="Mixxx is running"):
        mc.apply(db, mc.plan(db, [(1, "/a", "A", SR, SECTIONS)], {}))
    assert digest(db) == before


def test_apply_rolls_back_when_a_slot_was_taken(db, monkeypatch):
    monkeypatch.setattr(mc, "mixxx_running", lambda: False)
    plans = mc.plan(db, [(1, "/a", "A", SR, SECTIONS)], {})
    c = sqlite3.connect(db)
    c.execute("INSERT INTO cues (track_id, type, position, hotcue) VALUES (1, 1, 5, 4)")  # user adds hot cue 5
    c.commit()
    count = c.execute("SELECT COUNT(*) FROM cues").fetchone()[0]
    c.close()
    with pytest.raises(mc.CueExportError, match="no longer empty"):
        mc.apply(db, plans)
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM cues").fetchone()[0] == count  # nothing half-written


def test_refuses_unexpected_schema(tmp_path):
    p = tmp_path / "odd.sqlite"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE cues (id INTEGER, track_id INTEGER, pos INTEGER)")
    c.commit()
    c.close()
    with pytest.raises(mc.CueExportError, match="doesn't look as expected"):
        mc.read_existing(p, [1])


def test_mixxx_running_detects_this_process_name(monkeypatch, tmp_path):
    proc = tmp_path / "proc"
    (proc / "123").mkdir(parents=True)
    (proc / "123" / "comm").write_text("mixxx\n")
    real_path = mc.Path
    monkeypatch.setattr(mc, "Path", lambda p: real_path(str(p).replace("/proc", str(proc))))
    if mc.sys.platform.startswith("linux"):
        assert mc.mixxx_running()
        (proc / "123" / "comm").write_text("bash\n")
        assert not mc.mixxx_running()


def test_parts_get_hot_cues_after_whole_sections():
    with_parts = secs((INTRO, 0, 16), (DROP, 16, 32), (DROP, 32, 48), (BREAKDOWN, 48, 64), (DROP, 64, 96), (OUTRO, 96, 112))
    with_parts[2].number, with_parts[2].part, with_parts[2].repeated = 1, 2, True  # Drop 1 b
    with_parts[4].number = 2
    p = mc.plan_track(1, "/a", "A", SR, with_parts, [], set(), max_hotcues=4)
    assert [o.label for o in ops(p, "hotcue")] == ["◆ Drop 1", "◆ Breakdown", "◆ Drop 2", "◆ Outro"]
    p = mc.plan_track(1, "/a", "A", SR, with_parts, [], set(), max_hotcues=8)
    assert "◆ Drop 1 b" in [o.label for o in ops(p, "hotcue")]
