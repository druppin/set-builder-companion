import random

import numpy as np
import pytest

from harmonic_set_builder.core import quiz, synth, theory
from harmonic_set_builder.core.camelot import ALL_KEYS, DEFAULT_MOVES, move_name, parse_key

from .helpers import tr

K = parse_key


def test_scales_and_names():
    assert theory.scale_names(K("8B")) == ["C", "D", "E", "F", "G", "A", "B"]
    assert theory.scale_names(K("8A")) == ["A", "B", "C", "D", "E", "F", "G"]
    assert theory.key_name(K("4B")) == "Ab major"
    assert theory.key_name(K("11A")) == "F# minor"
    assert theory.triad(K("8A")) == [9, 0, 4]


@pytest.mark.parametrize("a,b,shared", [
    ("8A", "8A", 7), ("8A", "8B", 7), ("8A", "9A", 6), ("8A", "7A", 6), ("8A", "9B", 6),
    ("8B", "7A", 6), ("8A", "10A", 5), ("8B", "3B", 2), ("8B", "2B", 2),
])
def test_shared_notes_match_the_wheel(a, b, shared):
    assert theory.compare(K(a), K(b)).shared_count == shared


def test_every_move_has_an_explanation_with_concrete_notes():
    for k1 in ALL_KEYS:
        for k2 in ALL_KEYS:
            e = theory.explain(k1, k2)
            assert e.why and e.tip and str(k1) in e.title
    e = theory.explain(K("8B"), K("9B"))
    assert "6 of 7" in e.notes and "F→F#" in e.notes
    assert "Lift" in e.feel and "energy +1" in e.feel


def test_every_named_move_has_its_own_tip():
    for move in DEFAULT_MOVES:
        assert move in theory._TIPS and move in theory._WHY


def test_relative_keys_share_home_chord_notes():
    c = theory.compare(K("8A"), K("8B"))
    assert c.added == [] and c.removed == []
    assert sorted(c.shared_chord) == [0, 4]  # A minor and C major share C and E


def test_tonic_midi_and_scale_midi():
    assert synth.tonic_midi(K("8B")) == 60  # C4
    s = synth.scale_midi(K("8A"))
    assert s[0] % 12 == 9 and s[-1] - s[0] == 12 and len(s) == 8


def test_synth_renders_audio_and_wav():
    for kind in ("scale", "chord", "progression"):
        a = synth.key_reference(K("8A"), kind)
        assert a.dtype == np.float32 and len(a) > synth.SR and np.max(np.abs(a)) <= 0.81
    t = synth.transition(K("8A"), K("9A"))
    assert len(t) > 4 * synth.SR
    wav = synth.wav_bytes(synth.blend(K("8A"), K("3B")))
    assert wav[:4] == b"RIFF" and len(wav) > 44


def test_progression_chords_stay_in_key():
    for key in ALL_KEYS:
        pcs = set(theory.scale(key))
        for n in synth.progression_notes(key):
            assert n.midi % 12 in pcs


def test_dominant_frequency_of_scale_reference():
    a = synth.render([synth.Note(0, 1.0, 69, 0.3)])  # A4 alone
    spec = np.abs(np.fft.rfft(a))
    peak = np.argmax(spec) * synth.SR / len(a)
    assert abs(peak - 440) < 3


def test_target_for_every_quiz_move():
    rng = random.Random(1)
    for move in quiz.QUIZ_MOVES:
        starts = quiz.start_keys(move)
        assert len(starts) >= 12
        for k1 in starts:
            assert move_name(k1, quiz.target_for(k1, move, rng)) == move


@pytest.mark.parametrize("kind", list(quiz.KINDS))
def test_questions_have_the_right_answer(kind):
    rng = random.Random(7)
    cfg = quiz.QuizConfig(kinds=[kind])
    for _ in range(200):
        q = quiz.make_question(rng, cfg)
        assert 0 <= q.answer < len(q.options) and len(set(q.options)) == len(q.options)
        if kind == quiz.NAME_MOVE:
            assert q.options[q.answer] == move_name(q.k1, q.k2)
        if kind == quiz.FIND_KEY:
            assert q.options[q.answer] == str(q.k2)
            assert all(move_name(q.k1, K(o)) != q.move for i, o in enumerate(q.options) if i != q.answer)
        if kind == quiz.TIER:
            assert q.options[q.answer] == {"smooth": "Smooth", "energy": "Energy move", "clash": "Clash"}[
                DEFAULT_MOVES[q.move].tier]
        if kind == quiz.EAR:
            assert q.audio


def test_questions_from_library_tracks():
    tracks = [tr(i, k) for i, k in enumerate(["8A", "9A", "8B", "3A", "10A", "7A", "8A"], 1)]
    rng = random.Random(3)
    cfg = quiz.QuizConfig(kinds=[quiz.NAME_MOVE])
    with_tracks = [q for q in (quiz.make_question(rng, cfg, tracks) for _ in range(50)) if q.tracks[0]]
    assert with_tracks
    for q in with_tracks:
        a, b = q.tracks
        assert a.id != b.id and move_name(a.key, b.key) == q.move and a.artist in q.prompt


def test_stats_and_practice_weights():
    s = quiz.Stats()
    s.record("+1", True)
    s.record("+1", True)
    s.record("Tritone", False)
    assert (s.asked, s.correct, s.streak, s.best_streak) == (3, 2, 0, 2)
    w = s.practice_weights()
    assert w["Tritone"] == quiz.QUIZ_MOVES["Tritone"] * 3
    assert w["+1"] == quiz.QUIZ_MOVES["+1"]
    again = quiz.Stats.from_dict(s.to_dict())
    assert again.per_move == s.per_move and again.best_streak == 2
