import hashlib
import json
import os
import sqlite3

import pytest

from harmonic_set_builder.core.setlist import SetModel
from harmonic_set_builder.data import export, mixxx_db
from harmonic_set_builder.data.store import KEEP, SetStore, atomic_write_json

from .helpers import Lib, tr

SCHEMA = """
CREATE TABLE track_locations (id INTEGER PRIMARY KEY AUTOINCREMENT, location varchar(512) UNIQUE,
  filename varchar(512), directory varchar(512), filesize INTEGER, fs_deleted INTEGER, needs_verification INTEGER);
CREATE TABLE library (id INTEGER PRIMARY KEY AUTOINCREMENT, artist varchar(64), title varchar(64),
  album varchar(64), year varchar(16), genre varchar(64), tracknumber varchar(3),
  location INTEGER REFERENCES track_locations(location), comment varchar(256), duration FLOAT,
  bitrate INTEGER, bpm FLOAT, datetime_added DEFAULT CURRENT_TIMESTAMP, mixxx_deleted INTEGER,
  filetype varchar(8) DEFAULT '?', timesplayed INTEGER DEFAULT 0, rating INTEGER DEFAULT 0,
  key varchar(8) DEFAULT '', composer varchar(64) DEFAULT '', key_id INTEGER DEFAULT 0,
  grouping TEXT DEFAULT '', album_artist TEXT DEFAULT '', color INTEGER, last_played_at DATETIME DEFAULT NULL);
CREATE TABLE Playlists (id INTEGER PRIMARY KEY, name varchar(48), position INTEGER,
  hidden INTEGER DEFAULT 0 NOT NULL, date_created datetime, date_modified datetime, locked INTEGER DEFAULT 0);
CREATE TABLE PlaylistTracks (id INTEGER PRIMARY KEY, playlist_id INTEGER, track_id INTEGER, position INTEGER, pl_datetime_added);
CREATE TABLE crates (id INTEGER PRIMARY KEY AUTOINCREMENT, name varchar(48) UNIQUE NOT NULL,
  count INTEGER DEFAULT 0, show INTEGER DEFAULT 1, locked INTEGER DEFAULT 0, autodj_source INTEGER DEFAULT 0);
CREATE TABLE crate_tracks (crate_id INTEGER NOT NULL, track_id INTEGER NOT NULL, UNIQUE (crate_id, track_id));
"""


@pytest.fixture
def mixxx_db_file(tmp_path):
    p = tmp_path / "mixxx" / "mixxxdb.sqlite"
    p.parent.mkdir()
    c = sqlite3.connect(p)
    c.executescript(SCHEMA)
    rows = [
        # id, artist, title, bpm, key, key_id, mixxx_deleted, fs_deleted
        (1, "Zed", "One", 128.0, "Am", 22, 0, 0),
        (2, "Abe", "Two", 126.0, "", 0, 0, 0),  # no key_id: no text either
        (3, "Abe", "Three", 0, "8B", 0, 0, 0),  # text fallback, no BPM
        (4, "Gone", "Deleted", 128.0, "C", 1, 1, 0),
        (5, "Gone", "Missing file", 128.0, "C", 1, 0, 1),
    ]
    for tid, artist, title, bpm, key, kid, mdel, fdel in rows:
        c.execute("INSERT INTO track_locations (id, location, fs_deleted) VALUES (?,?,?)",
                  (tid * 10, f"/music/{artist}/{title}.mp3", fdel))
        c.execute("INSERT INTO library (id, artist, title, bpm, key, key_id, mixxx_deleted, location, duration,"
                  " comment) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (tid, artist, title, bpm, key, kid, mdel, tid * 10, 300.4, "Energy 6" if tid == 1 else ""))
    c.execute("INSERT INTO crates (id, name) VALUES (1, 'House')")
    c.executemany("INSERT INTO crate_tracks VALUES (1, ?)", [(1,), (2,), (4,)])
    c.executemany("INSERT INTO Playlists (id, name, position, hidden) VALUES (?,?,?,?)",
                  [(1, "Auto DJ", 0, 1), (2, "My set", 1, 0), (3, "2026-08-16", 2, 2)])
    c.executemany("INSERT INTO PlaylistTracks (playlist_id, track_id, position) VALUES (2,?,?)",
                  [(3, 1), (1, 2), (2, 3)])
    c.commit()
    c.close()
    return p


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_snapshot_leaves_original_byte_identical(mixxx_db_file, tmp_path):
    before, mtime = sha(mixxx_db_file), os.stat(mixxx_db_file).st_mtime_ns
    snap = mixxx_db.snapshot(mixxx_db_file, tmp_path / "snap")
    assert sha(mixxx_db_file) == before and os.stat(mixxx_db_file).st_mtime_ns == mtime
    assert sorted(os.listdir(mixxx_db_file.parent)) == ["mixxxdb.sqlite"]  # no journal/wal left behind
    lib = mixxx_db.load(snap)
    assert sha(mixxx_db_file) == before
    assert set(lib.tracks) == {1, 2, 3}


def test_library_contents(mixxx_db_file, tmp_path):
    lib = mixxx_db.load(mixxx_db.snapshot(mixxx_db_file, tmp_path / "snap"))
    t1, t2, t3 = lib.get(1), lib.get(2), lib.get(3)
    assert str(t1.key) == "8A" and t1.bpm == 128 and t1.energy_tag == 6
    assert t2.key is None and not t2.mixable
    assert str(t3.key) == "8B" and t3.bpm is None
    assert t1.location == "/music/Zed/One.mp3"
    crate = lib.crates[0]
    assert crate.name == "House" and crate.track_ids == [2, 1]  # deleted dropped; artist order
    pl = {p.name: p for p in lib.playlists}
    assert pl["My set"].track_ids == [3, 1, 2]  # position order
    vis = [p.name for p in mixxx_db.visible_playlists(lib, False, False)]
    assert vis == ["My set"]
    assert len(mixxx_db.visible_playlists(lib, True, True)) == 3


def test_resolve_survives_id_change(mixxx_db_file, tmp_path):
    from harmonic_set_builder.core.setlist import TrackRef

    lib = mixxx_db.load(mixxx_db.snapshot(mixxx_db_file, tmp_path / "snap"))
    assert lib.resolve(TrackRef(999, "/music/Zed/One.mp3", "", "")).id == 1
    assert lib.resolve(TrackRef(999, "/elsewhere.mp3", "zed", "ONE")).id == 1
    assert lib.resolve(TrackRef(1, "/music/Abe/Two.mp3", "Abe", "Two")).id == 2  # id reused for another file
    assert lib.resolve(TrackRef(999, "/x", "nobody", "nothing")) is None


def test_locate_override(mixxx_db_file):
    assert mixxx_db.locate(str(mixxx_db_file)) == mixxx_db_file
    assert mixxx_db.locate("/no/such/file") is None


# ------------------------------------------------------------------ store
def make_set(n=1):
    lib = Lib([tr(i, "8A") for i in range(1, n + 1)])
    m = SetModel("Friday")
    m.insert_tracks(list(lib.tracks.values()), None, lib.ctx())
    return m


def test_store_keeps_last_versions(tmp_path):
    store = SetStore(tmp_path)
    m = make_set()
    for _ in range(KEEP + 5):
        store.save(m)
    assert len(list((tmp_path / "sets" / m.id).glob("*.json"))) == KEEP
    assert store.load(m.id).model.to_dict() == m.to_dict()
    assert store.list_sets() == [(m.id, "Friday")]


def test_store_recovers_from_corrupt_newest(tmp_path):
    store = SetStore(tmp_path)
    m = make_set(1)
    store.save(m)
    m.name = "Newer"
    newest = store.save(m)
    newest.write_text("{ truncated")
    r = store.load(m.id)
    assert r.model.name == "Friday" and r.recovered_from == newest.name


def test_atomic_write_survives_crash(tmp_path, monkeypatch):
    path = tmp_path / "x.json"
    atomic_write_json(path, {"v": 1})

    def boom(*a, **k):
        raise OSError("power cut")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_json(path, {"v": 2})
    assert json.loads(path.read_text()) == {"v": 1}


# ------------------------------------------------------------------ export
def test_m3u8_round_trip(tmp_path):
    tracks = [tr(1, "8A", duration=300.4), tr(2, "9A", duration=0)]
    p = tmp_path / "set.m3u8"
    export.write_m3u8(p, tracks)
    text = p.read_text(encoding="utf-8")
    assert text.splitlines() == [
        "#EXTM3U", "#EXTINF:300,Artist1 - T1 8A", "/music/1.mp3", "#EXTINF:0,Artist2 - T2 9A", "/music/2.mp3",
    ]
    lookup = {t.location: t for t in tracks}.get
    found, missing = export.match_paths(export.read_m3u(p) + ["/nope.mp3"], lookup)
    assert [t.id for t in found] == [1, 2] and missing == ["/nope.mp3"]


def test_tracklist_text():
    from harmonic_set_builder.core.ranking import relate
    from harmonic_set_builder.core.settings import Settings

    a, b = tr(1, "8A"), tr(2, "9A")
    text = export.tracklist_text([a, b], [None, relate(a, b, Settings())])
    assert text.splitlines()[1] == " 2. Artist2 – T2 9A  [9A, 128.0 BPM]  +1"


def test_load_cues_in_seconds(tmp_path):
    p = tmp_path / "snap.sqlite"
    c = sqlite3.connect(p)
    c.executescript("CREATE TABLE library (id INTEGER PRIMARY KEY, samplerate INTEGER);"
                    "CREATE TABLE cues (id INTEGER PRIMARY KEY, track_id INTEGER, type INTEGER, position REAL,"
                    " length REAL, hotcue INTEGER, label TEXT, color INTEGER);"
                    "INSERT INTO library VALUES (1, 44100);"
                    "INSERT INTO cues VALUES (1, 1, 1, 88200, 0, 0, 'drop', 0);"
                    "INSERT INTO cues VALUES (2, 1, 7, -1, 441000, -1, '', 0);")
    c.commit()
    c.close()
    cues = mixxx_db.load_cues(p, [1, 2])
    assert cues[2] == []
    hot, outro = sorted(cues[1], key=lambda x: x["type"])
    assert (hot["type"], hot["start"], hot["hotcue"], hot["label"]) == (1, 1.0, 0, "drop")
    assert outro["start"] is None and outro["length"] == 5.0
