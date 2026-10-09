import struct

import numpy as np
import pytest

from harmonic_set_builder.analysis import grid, labels, pipeline, structure, transitions
from harmonic_set_builder.analysis.energy import BarFeatures
from harmonic_set_builder.analysis.labels import BREAKDOWN, BUILD, DROP, GROOVE, INTRO, OUTRO, Section
from harmonic_set_builder.analysis.store import AnalysisStore, TrackAnalysis, file_signature

from . import edm


# ---------------------------------------------------------------- grids
def _varint(v: int) -> bytes:
    v &= (1 << 64) - 1
    out = b""
    while True:
        b = v & 0x7F
        v >>= 7
        out += bytes([b | (0x80 if v else 0)])
        if not v:
            return out


def test_decode_real_beatgrid_blob():
    # Copied from a real Mixxx 2.4 library: 115.38 BPM, first beat at frame 12648.
    blob = bytes.fromhex("0A09099B649A9F51D85C40120308E862")
    g = grid.decode_mixxx_beats(blob, "BeatGrid-2.0", 44100)
    assert g.bpm == pytest.approx(115.3799819) and g.first_beat == pytest.approx(12648 / 44100)


def test_decode_negative_first_beat_and_beatmap():
    bpm = b"\x0a\x09\x09" + struct.pack("<d", 126.0)
    neg = b"\x12" + bytes([1 + len(_varint(-4410))]) + b"\x08" + _varint(-4410)
    g = grid.decode_mixxx_beats(bpm + neg, "BeatGrid-2.0", 44100)
    assert g.first_beat == pytest.approx(-0.1)
    beats = b"".join(b"\x0a" + bytes([1 + len(_varint(f))]) + b"\x08" + _varint(f) for f in range(0, 44100 * 10, 22050))
    m = grid.decode_mixxx_beats(beats, "BeatMap-1.0", 44100)
    assert m.beats[:3] == (0.0, 0.5, 1.0)
    assert grid.decode_mixxx_beats(b"\xff\xff", "BeatGrid-2.0", 44100) is None
    assert grid.decode_mixxx_beats(None, "BeatGrid-2.0", 44100) is None


def test_beat_times_cover_the_track_from_a_negative_first_beat():
    b = grid.beat_times(10.0, 120.0, -0.2)
    assert b[0] == pytest.approx(0.3) and b[-1] < 10.0 and np.allclose(np.diff(b), 0.5)


def test_downbeat_phase_from_hints_and_novelty():
    beats = np.arange(64) * 0.5
    assert grid.downbeat_phase([], hints=beats[2::4][:8] + 0.01, beats=beats) == 2
    nov = np.zeros(64)
    nov[1::4] = 1.0
    assert grid.downbeat_phase(nov) == 1


# ---------------------------------------------------------------- labels
def _features(energy, low=None):
    n = len(energy)
    e = np.asarray(energy, dtype=float)
    lo = np.asarray(low if low is not None else e, dtype=float)
    z = np.zeros(n)
    return BarFeatures(np.arange(n) * 2.0, np.arange(1, n + 1) * 2.0, e, lo, z, z, z, e, np.zeros((n, 25)),
                       lo * 30 - 40)


def test_snap_prefers_phrase_grid():
    bars = np.arange(64) * 2.0
    assert labels.snap_bar(2 * 15.2, bars) == 16  # one bar off the phrase: snap to it
    assert labels.snap_bar(2 * 13.0, bars) == 13  # three bars off: keep the downbeat


def test_allin1_labels_map_to_dj_labels():
    e = [0.2] * 16 + [0.9] * 16 + [0.2] * 16 + [0.9] * 16 + [0.4] * 16
    f = _features(e, low=[0.8] * 16 + [1] * 16 + [0.0] * 16 + [1] * 16 + [0.8] * 16)
    raw = structure.RawStructure("allin1==1.1.0", segments=[
        {"start": 0, "end": 1, "label": "start"}, {"start": 1, "end": 32, "label": "intro"},
        {"start": 32.3, "end": 64, "label": "chorus"}, {"start": 64, "end": 96, "label": "bridge"},
        {"start": 95.8, "end": 128, "label": "chorus"}, {"start": 128, "end": 158, "label": "outro"},
        {"start": 158, "end": 160, "label": "end"},
    ])
    secs = labels.to_sections(raw, f, 160.0)
    assert [(s.label, s.start_bar, s.end_bar) for s in secs] == [
        (INTRO, 0, 16), (DROP, 16, 32), (BREAKDOWN, 32, 48), (DROP, 48, 64), (OUTRO, 64, 80)]
    assert [s.name for s in secs] == ["Intro", "Drop 1", "Breakdown", "Drop 2", "Outro"]
    assert secs[0].start_sec == 0.0 and secs[-1].end_sec == 160.0
    assert labels.summary(secs) == "I16 D16 Br16 D16 O16"


def test_quiet_groove_at_the_edges_becomes_intro_and_outro():
    e = [0.3] * 8 + [0.4] * 8 + [0.9] * 16 + [0.4] * 8
    raw = structure.RawStructure("x", segments=[
        {"start": 0, "label": "verse"}, {"start": 16, "label": "verse"}, {"start": 32, "label": "chorus"},
        {"start": 64, "label": "verse"}])
    secs = labels.to_sections(raw, _features(e, low=[1] * 40), 80.0)
    assert [s.label for s in labels.whole(secs)] == [INTRO, DROP, OUTRO]
    assert [(s.name, s.start_bar) for s in secs][:2] == [("Intro", 0), ("Intro b", 8)]  # allin1's cut kept


def test_mapping_rules():
    assert labels._map("chorus", 0.4, 1) == GROOVE
    assert labels._map("break", 0.8, 0.2) == BREAKDOWN
    assert labels._map("bridge", 0.8, 1) == GROOVE
    assert labels._map("inst", 0.9, 1) == DROP
    assert labels._map("verse", 0.5, 1) == GROOVE


# ------------------------------------------------- full pipeline (builtin)
@pytest.fixture(scope="module")
def edm_file(tmp_path_factory):
    p = tmp_path_factory.mktemp("audio") / "edm.wav"
    edm.write_wav(p, edm.render(lead_in=0.3))
    return p


def test_builtin_finds_the_synthetic_structure(edm_file):
    job = pipeline.Job(str(edm_file), track_id=7, bpm=edm.BPM, grid={"bpm": edm.BPM, "first_beat": 0.3, "beats": None})
    a = TrackAnalysis.from_dict(pipeline.analyze(job)["analysis"])
    got = [(s.label, s.start_bar, s.end_bar) for s in a.sections]
    want = edm.expected_bounds()
    assert [g[0] for g in got] == [w[0] for w in want]
    assert [g[1] for g in got] == [w[1] for w in want]
    assert a.grid == "mixxx" and a.first_downbeat == pytest.approx(0.3, abs=0.01)
    assert {s.source for s in a.sections if s.label == BUILD} == {"derived"}
    drops = [s for s in a.sections if s.label == DROP]
    assert all(d.mean_energy > 0.8 for d in drops)
    assert len(a.bars) >= 96 and all(0 <= b["energy"] <= 1 for b in a.bars)


def test_builtin_without_a_mixxx_grid_still_finds_sections(edm_file):
    a = TrackAnalysis.from_dict(pipeline.analyze(pipeline.Job(str(edm_file), bpm=edm.BPM))["analysis"])
    assert a.grid == "detected"
    assert [s.label for s in a.sections] == [w[0] for w in edm.expected_bounds()]


# Real allin1 1.1.0 output for tests/edm.py (2026-10-08): good boundaries, pop-style labels.
ALLIN1_ON_EDM = [(0.0, "start"), (0.3, "intro"), (16.2, "intro"), (29.8, "intro"), (45.3, "intro"), (60.0, "intro"),
                 (75.0, "intro"), (105.3, "inst"), (120.3, "inst"), (135.5, "inst"), (150.7, "inst"), (180.3, "end")]


def test_allin1_output_on_dance_music_maps_to_the_real_sections(edm_file):
    segs = [{"start": t, "end": (ALLIN1_ON_EDM[i + 1][0] if i + 1 < len(ALLIN1_ON_EDM) else 182.0), "label": lab}
            for i, (t, lab) in enumerate(ALLIN1_ON_EDM)]
    raw = {"analyzer": "allin1==1.1.0", "bpm": 128, "beats": [], "downbeats": [], "segments": segs}
    job = pipeline.Job(str(edm_file), bpm=edm.BPM, grid={"bpm": edm.BPM, "first_beat": 0.3, "beats": None},
                       backend="allin1", raw=raw)
    a = TrackAnalysis.from_dict(pipeline.analyze(job)["analysis"])
    got = [(s.label, s.start_bar) for s in labels.whole(a.sections)]
    assert got == [(lab, start) for lab, start, _ in edm.expected_bounds()]


def test_cached_raw_relabels_without_rerunning_the_model(edm_file, monkeypatch):
    first_run = pipeline.analyze(pipeline.Job(str(edm_file), bpm=edm.BPM, backend="allin1", raw={
        "analyzer": "allin1==1.1.0", "beats": [], "downbeats": [],
        "segments": [{"start": 0, "end": 30, "label": "intro"}, {"start": 30, "end": 180, "label": "chorus"}]}))
    assert first_run["analysis"]["analyzer"] == "allin1==1.1.0" + labels.LABELS_TAG

    def boom(*a, **k):
        raise AssertionError("must not run allin1")

    monkeypatch.setattr(structure, "run_allin1", boom)
    again = pipeline.analyze(pipeline.Job(str(edm_file), bpm=edm.BPM, backend="allin1", raw=first_run["raw"]))
    assert again["analysis"]["sections"] == first_run["analysis"]["sections"]


# ------------------------------------------------------------------ store
def _analysis(path, secs, n_bars=64, bar_sec=2.0, **kw):
    a = TrackAnalysis(path, "1:2", "builtin==1", duration=n_bars * bar_sec, bpm=120.0, **kw)
    a.sections = [Section(l, 1, s, e, s * bar_sec, e * bar_sec, 0.5) for l, s, e in secs]
    a.bars = [{"bar": i, "start_sec": i * bar_sec, "energy": 0.5, "rms": 0.1, "low": 0.2, "high": 0.3,
               "centroid": 0.4, "onsets": 0.5} for i in range(n_bars)]
    return a


def test_store_round_trip_index_and_raw_cache(tmp_path):
    st = AnalysisStore(tmp_path / "analysis.sqlite")
    a = _analysis("/m/a.mp3", [(INTRO, 0, 16), (DROP, 16, 48), (OUTRO, 48, 64)], mixxx_track_id=3)
    st.save(a)
    st.save(a)  # replaces, no duplicates
    b = st.get("/m/a.mp3")
    assert [s.label for s in b.sections] == [INTRO, DROP, OUTRO] and len(b.bars) == 64
    assert b.bars[5]["centroid"] == 0.4 and b.mixxx_track_id == 3
    assert st.index()["/m/a.mp3"].summary == "I16 D32 O16"
    st.raw_put("/m/a.mp3", "allin1", "1:2", {"x": 1})
    assert st.raw_get("/m/a.mp3", "allin1", "1:2") == {"x": 1}
    assert st.raw_get("/m/a.mp3", "allin1", "changed") is None
    st.record_cues(3, "/m/a.mp3", [(10, "hotcue"), (11, "intro")])
    assert st.own_cue_ids(3) == {10, 11}
    st.forget_cues(3, [10])
    assert st.own_cue_ids(3) == {11}


def test_needs_analysis_on_file_change(tmp_path):
    f = tmp_path / "t.mp3"
    f.write_bytes(b"x")
    st = AnalysisStore(tmp_path / "a.sqlite")
    assert st.needs_analysis(str(f), "builtin")
    a = _analysis(str(f), [(DROP, 0, 64)])
    a.file_hash, a.analyzer = file_signature(str(f)), "builtin==1" + labels.LABELS_TAG
    st.save(a)
    assert not st.needs_analysis(str(f), "builtin")
    assert st.needs_analysis(str(f), "allin1")  # different backend
    assert st.needs_analysis(str(f), "builtin", force=True)
    f.write_bytes(b"xy")
    assert st.needs_analysis(str(f), "builtin")
    f.unlink()
    assert not st.needs_analysis(str(f), "builtin")  # unreachable: keep what we have


# ------------------------------------------------------------ transitions
def test_intro_over_outro_and_drop_on_breakdown():
    a = _analysis("/a", [(INTRO, 0, 16), (DROP, 16, 48), (BREAKDOWN, 48, 64), (DROP, 64, 96), (OUTRO, 96, 128)], 128)
    b = _analysis("/b", [(INTRO, 0, 32), (DROP, 32, 64), (OUTRO, 64, 80)], 80)
    tips, flags = transitions.suggest(a, b)
    io = tips[0]
    assert io.kind == "intro_over_outro" and io.a_bar == 96 and io.a_sec == 192.0
    assert "Start B's 32-bar intro at A's outro (bar 97)" in io.text
    dob = next(t for t in tips if t.kind == "drop_on_breakdown")
    assert dob.a_bar == 64 - 32 and "bar 65" in dob.text
    assert flags == []


def test_phrase_compatibility_flags():
    a = _analysis("/a", [(INTRO, 0, 16), (DROP, 16, 52), (OUTRO, 52, 64)])
    b = _analysis("/b", [(INTRO, 0, 4), (DROP, 4, 64)])
    tips, flags = transitions.suggest(a, b)
    text = " ".join(f.text for f in flags)
    assert "shorter than A's outro" in text and "off the 8-bar phrase grid" in text and "not a multiple of 8" in text
    assert tips[0].a_bar == 60  # B's 4-bar intro ends with A's outro
    _, flags = transitions.suggest(_analysis("/a", [(DROP, 0, 64)]), _analysis("/b", [(DROP, 0, 64)]))
    assert any("no intro" in f.text for f in flags) and any("no outro" in f.text for f in flags)


def test_set_flow_overlaps_at_the_mix_point():
    a = _analysis("/a", [(INTRO, 0, 16), (DROP, 16, 48), (OUTRO, 48, 64)])
    b = _analysis("/b", [(INTRO, 0, 16), (DROP, 16, 64)])
    flow = transitions.set_flow([a, None, b])
    assert [f.index for f in flow] == [0, 2]
    # a's mix point: no next analysis directly after it (None) -> default overlap
    assert flow[0].mix_in == pytest.approx((64 - 16) * 2.0)
    flow = transitions.set_flow([a, b])
    assert flow[1].offset == pytest.approx(48 * 2.0) and flow[1].times[0] == pytest.approx(96.0)


# ------------------------------------------- real tracks (bar features only)
import json  # noqa: E402
from pathlib import Path  # noqa: E402

REAL = json.loads((Path(__file__).parent / "fixtures" / "real_tracks.json").read_text())


def _real(name):
    """Stored per-bar features plus allin1's raw output for a track from the library
    (2026-10-08). No audio: kick presence comes from the normalized low band."""
    d = REAL[name]
    b = {k: np.asarray(v, dtype=float) for k, v in d["bars"].items()}
    n = len(b["energy"])
    f = BarFeatures(b["start_sec"], np.append(b["start_sec"][1:], d["duration"]), b["rms"], b["low"], b["high"],
                    b["centroid"], b["onsets"], b["energy"], np.zeros((n, 25)), b["low"] * 30 - 40)
    raw = structure.RawStructure("allin1==1.1.0", segments=d["allin1_segments"])
    return labels.to_sections(raw, f, d["duration"]), raw, f


def test_phrase_grid_offset_from_a_pickup_bar():
    # AC Slater – Bass Face: phrases start one bar after Mixxx's first downbeat.
    secs, raw, f = _real("bass_face")
    assert labels.raw_phrase_offset(raw, f.starts) == 1
    drops = [s for s in labels.whole(secs) if s.label == DROP]
    assert [round(s.start_sec, 1) for s in drops] == [32.4, 122.4]  # on allin1's boundaries, not a bar early
    # allin1's phrase changes inside the drops are kept as parts, where DJs put cues
    parts = [round(s.start_sec, 1) for s in secs if s.label == DROP and s.part > 1]
    assert parts == [47.4, 62.4, 137.4, 152.4, 167.4]
    assert all((s.start_bar - 1) % 8 == 0 for s in secs[1:])


def test_builds_before_drops_on_real_tracks():
    secs, _, _ = _real("bass_face")
    names = [s.label for s in secs]
    assert names.count(BUILD) == 2  # Build 2 used to be missed
    b2 = [s for s in secs if s.label == BUILD][1]
    assert b2.end_bar == next(s for s in secs if s.name == "Drop 2").start_bar

    secs, _, _ = _real("do_it_to_it")
    # ACRAZE – Do It To It (Tiësto Remix) opens on a build, and its second build is 8 bars, not 4.
    assert secs[0].label == BUILD and secs[0].bars == 8
    b2 = [s for s in secs if s.label == BUILD][1]
    assert (round(b2.start_sec, 1), b2.bars) == (62.9, 8)


def test_phrase_offset_needs_evidence():
    assert labels.phrase_offset([9, 17, 41, 65]) == 1
    assert labels.phrase_offset([8, 17]) == 0  # a tie stays on the downbeat grid
    assert labels.phrase_offset([13]) == 0


def test_status_and_relabel_keep_the_original_backend(tmp_path):
    from harmonic_set_builder.analysis import batch
    from harmonic_set_builder.core.track import Track

    f = tmp_path / "t.mp3"
    f.write_bytes(b"x")
    st = AnalysisStore(tmp_path / "a.sqlite")
    a = _analysis(str(f), [(DROP, 0, 64)])
    a.file_hash, a.analyzer = file_signature(str(f)), "allin1==1.1.0"  # made by older labeling rules
    st.save(a)
    st.raw_put(str(f), "allin1", a.file_hash, {"analyzer": "allin1==1.1.0", "segments": []})
    assert st.status(str(f)) == "outdated" and st.backend_of(str(f)) == "allin1"
    jobs, _ = batch.make_jobs([Track(1, location=str(f))], {}, st, "builtin")
    assert len(jobs) == 1 and jobs[0].backend == "allin1" and jobs[0].raw is not None  # re-label, not re-run
    a.analyzer += labels.LABELS_TAG
    st.save(a)
    assert st.status(str(f)) == "current"
    jobs, skipped = batch.make_jobs([Track(1, location=str(f))], {}, st, "builtin")
    assert not jobs and skipped[0][1] == "already analyzed"
    jobs, _ = batch.make_jobs([Track(1, location=str(f))], {}, st, "builtin", force=True)
    assert jobs[0].backend == "builtin" and jobs[0].raw is None


# ------------------------------------------------------------- benchmark
def test_benchmark_match_and_score():
    from harmonic_set_builder.analysis import benchmark

    refs = [0.0, 30.0, 60.0, 90.0]  # the cue at 0:00 doesn't count
    m = benchmark.match(refs, [30.2, 61.0, 75.0], 0.5)
    assert (m.refs, m.preds, m.hits, m.used) == (3, 3, 1, 1)
    s = benchmark.score("x", [(refs, [30.2, 61.0, 75.0], 1.875)])
    assert round(s.recall_half_sec, 2) == 0.33 and round(s.recall_bar, 2) == 0.67
    assert s.median_error == pytest.approx(0.6)
    assert "Your cues found" in benchmark.table([s])


def test_cluster_cuedetr_candidates():
    from harmonic_set_builder.analysis import benchmark

    cands = [[30.2, 0.7], [30.3, 0.9], [30.35, 0.8], [61.0, 0.95], [90.0, 0.65], [90.1, 0.62], [90.2, 0.7]]
    assert [round(t, 1) for t in benchmark.cluster_candidates(cands)] == [30.3, 90.1]  # 61.0 is a lone window


def test_parts_split_only_on_model_cuts_and_join_for_summary():
    assert labels._split(0, 32, [8, 16, 30]) == [(0, 8), (8, 16), (16, 32)]  # 30 would leave 2 bars
    secs = [Section(DROP, 1, 0, 16, 0, 30, 0.9), Section(DROP, 1, 16, 32, 30, 60, 0.7, part=2),
            Section(OUTRO, 1, 32, 48, 60, 90, 0.3)]
    assert secs[1].name == "Drop b"
    w = labels.whole(secs)
    assert [(s.label, s.start_bar, s.end_bar) for s in w] == [(DROP, 0, 32), (OUTRO, 32, 48)]
    assert w[0].mean_energy == pytest.approx(0.8)
    assert labels.summary(secs) == "D32 O16"
    assert labels.first(secs, DROP).end_bar == 32
