"""Why Camelot moves work: scales, shared notes and plain-language explanations.

A Camelot key is a 7-note scale. Two keys mix well when their scales share most
of their notes, because then the two tracks' melodies and basslines mostly agree.
Everything here is computed from the scales, so the numbers in the explanations
are always right for the keys involved.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .camelot import MAJOR, MINOR, Key, MoveDef, classify, move_name, pitch_class, wheel_distance

MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
MINOR_STEPS = (0, 2, 3, 5, 7, 8, 10)  # natural minor

_SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
# Major keys written with sharps (C counts: it has none). Minor keys follow their relative major.
_SHARP_MAJORS = {0, 7, 2, 9, 4, 11, 6}


def scale(key: Key) -> list[int]:
    """Pitch classes of the key's scale, starting on the tonic."""
    root = pitch_class(key)
    steps = MAJOR_STEPS if key.mode == MAJOR else MINOR_STEPS
    return [(root + s) % 12 for s in steps]


def triad(key: Key) -> list[int]:
    """Tonic chord: root, third, fifth."""
    s = scale(key)
    return [s[0], s[2], s[4]]


def uses_sharps(key: Key) -> bool:
    major_root = pitch_class(Key(key.number, MAJOR))
    return major_root in _SHARP_MAJORS


def note_name(pc: int, sharps: bool = True) -> str:
    return (_SHARPS if sharps else _FLATS)[pc % 12]


def key_name(key: Key) -> str:
    """'A minor', 'F# major' — spelled the way the key is usually written."""
    root = note_name(pitch_class(key), uses_sharps(key))
    return f"{root} {'minor' if key.mode == MINOR else 'major'}"


def short_name(key: Key) -> str:
    """'Am', 'Db', 'F#m' — spelled like ``key_name``."""
    root = note_name(pitch_class(key), uses_sharps(key))
    return root + ("m" if key.mode == MINOR else "")


def scale_names(key: Key) -> list[str]:
    sh = uses_sharps(key)
    return [note_name(pc, sh) for pc in scale(key)]


def semitones_between(k1: Key, k2: Key) -> int:
    """Shortest move of the tonic, -5..+6 semitones."""
    d = (pitch_class(k2) - pitch_class(k1)) % 12
    return d - 12 if d > 6 else d


@dataclass(frozen=True)
class Comparison:
    k1: Key
    k2: Key
    move: str
    shared: list[int]  # pitch classes in both scales
    removed: list[int]  # in k1 only
    added: list[int]  # in k2 only
    shared_chord: list[int]  # tonic-chord notes of k1 that are also in k2's tonic chord
    tonic_semitones: int

    @property
    def shared_count(self) -> int:
        return len(self.shared)

    def names(self, pcs: list[int]) -> str:
        sh = uses_sharps(self.k2) if pcs is self.added else uses_sharps(self.k1)
        return ", ".join(note_name(p, sh) for p in pcs) or "none"


def compare(k1: Key, k2: Key) -> Comparison:
    s1, s2 = scale(k1), scale(k2)
    t2 = set(triad(k2))
    return Comparison(
        k1, k2, move_name(k1, k2),
        shared=[p for p in s1 if p in s2],
        removed=[p for p in s1 if p not in s2],
        added=[p for p in s2 if p not in s1],
        shared_chord=[p for p in triad(k1) if p in t2],
        tonic_semitones=semitones_between(k1, k2),
    )


# What each move does to the music, in plain words. {a}/{b} are the key names.
_WHY = {
    "Same key": "Both tracks use exactly the same seven notes and the same home note. Nothing in the harmony "
                "changes, so you can layer any part of one over the other.",
    "+1": "One step clockwise on the wheel is one step up the circle of fifths. Six of the seven notes stay; "
          "the one that changes moves up a semitone (it gains a sharp). That small brightening is why it feels "
          "like a lift.",
    "−1": "One step anticlockwise is one step down the circle of fifths. Six of the seven notes stay; the one "
          "that changes drops a semitone (it gains a flat). The music relaxes slightly, so it feels calmer.",
    "Relative major": "Same number, A→B: the two keys use exactly the same seven notes. Only the home note "
                      "moves, from the minor tonic to the major one three semitones up. Same notes, sunnier "
                      "centre of gravity.",
    "Relative minor": "Same number, B→A: exactly the same seven notes, but home moves to the minor tonic three "
                      "semitones down. Nothing clashes; the mood just turns more serious.",
    "Diagonal up": "Minor to the major one step clockwise. Six of seven notes are shared, so it blends cleanly, "
                   "and you get both the lift of +1 and the brightness of going major.",
    "Diagonal down": "Major to the minor one step anticlockwise. Six of seven notes are shared; it settles and "
                     "darkens at the same time.",
    "Diagonal down (to major)": "Minor to the major one step anticlockwise. Six of seven notes are shared; "
                                "calmer, but brighter.",
    "Diagonal up (to minor)": "Major to the minor one step clockwise. Six of seven notes are shared; a lift "
                              "with a darker colour.",
    "+2": "Two steps clockwise: the home note rises a whole tone. Five of seven notes are shared, so a quick blend "
          "still works, but two notes change, which the ear hears as a clear step up in energy.",
    "−2": "Two steps anticlockwise: the home note falls a whole tone. Five of seven notes are shared; two "
          "notes change, which reads as a deliberate drop in energy.",
    "Semitone up": "Seven steps clockwise lands one semitone above. Only two notes are shared, so a long blend "
                   "would clash. Used as a cut on a phrase boundary, it's the classic \"key change\" lift: "
                   "everything suddenly sits higher.",
    "Semitone down": "Seven steps anticlockwise lands one semitone below. Only two notes are shared; cut on a "
                     "phrase boundary rather than blending. It deflates the energy on purpose.",
    "Tritone": "Six steps: the opposite side of the wheel, the furthest a key can be. The home notes are a "
               "tritone apart, the most unstable interval in Western music, and the scales share only two "
               "notes. Blending these clashes hard.",
    "Off-key": "These keys are far apart on the wheel and share few notes, so melodies and basslines will "
               "rub against each other during a blend.",
}

_TIPS = {
    "Same key": "Safe for long blends and mash-ups.",
    "+1": "Great default for building a set gradually.",
    "−1": "Use it to give the crowd a breather without losing the groove.",
    "Relative major": "Nice after a dark stretch, e.g. coming out of a breakdown.",
    "Relative minor": "Good for steering the set somewhere moodier.",
    "Diagonal up": "Blends as easily as +1; use it when you want the lift to feel happier too.",
    "Diagonal down": "Blends as easily as −1; good for winding down into something deeper.",
    "Diagonal down (to major)": "A gentle release: calmer but not darker.",
    "Diagonal up (to minor)": "Lifts the energy while turning the mood more serious.",
    "+2": "Mix on a phrase boundary and keep the blend shorter than with a smooth move.",
    "−2": "Mix on a phrase boundary; works well to reset after a peak.",
    "Semitone up": "Cut or swap at a drop rather than blending melodic parts.",
    "Semitone down": "Cut on a phrase boundary; avoid overlapping melodies.",
    "Tritone": "If you must, mix only drums: kill the mids/highs of the outgoing track or cut cleanly.",
    "Off-key": "Blend only percussion, or use an effect / a cut to hide the change.",
}


@dataclass(frozen=True)
class Explanation:
    title: str  # "8A → 9A: +1 (Lift)"
    why: str
    notes: str  # the shared / changed notes, concretely
    feel: str  # mood + energy + tier
    tip: str


def explain(k1: Key, k2: Key, moves: Optional[dict[str, MoveDef]] = None,
            energy_moves_in_key: bool = True) -> Explanation:
    c = compare(k1, k2)
    mv = classify(k1, k2, moves, energy_moves_in_key)
    title = f"{k1} ({key_name(k1)}) → {k2} ({key_name(k2)}): {mv.name}"
    if c.shared_count == 7:
        notes = f"All 7 notes shared: {c.names(c.shared)}."
    else:
        changes = ", ".join(
            f"{note_name(a, uses_sharps(k1))}→{note_name(b, uses_sharps(k2))}"
            for a, b in _pair_changes(c.removed, c.added)
        )
        notes = f"{c.shared_count} of 7 notes shared ({c.names(c.shared)}). Changes: {changes}."
    if c.shared_chord and len(c.shared_chord) < 3:
        notes += f" The home chords have {len(c.shared_chord)} note(s) in common."
    elif len(c.shared_chord) == 3:
        notes += " The home chords are identical."
    tiers = {"smooth": "smooth (in key)", "energy": "an energy move (in key, but noticeable)", "clash": "a clash"}
    if mv.energy is None or mv.is_clash:
        energy = "the energy line breaks here"
    elif mv.energy == 0:
        energy = "energy stays level"
    else:
        energy = f"energy {mv.energy:+d}"
    feel = f"{mv.label}. This is {tiers[mv.tier]}; {energy}."
    why = _WHY.get(c.move, _WHY["Off-key"])
    if c.move == "Off-key":
        why = (f"{wheel_distance(k1.number, k2.number):+d} steps on the wheel"
               f"{' with a mode change' if k1.mode != k2.mode else ''}. " + why)
    return Explanation(title, why, notes, feel, _TIPS.get(c.move, _TIPS["Off-key"]))


def _pair_changes(removed: list[int], added: list[int]) -> list[tuple[int, int]]:
    """Pair each removed note with the nearest added one (usually a semitone away)."""
    left = list(added)
    out = []
    for r in removed:
        if not left:
            break
        best = min(left, key=lambda a: min((a - r) % 12, (r - a) % 12))
        left.remove(best)
        out.append((r, best))
    return out


WHEEL_BASICS = (
    "The Camelot wheel puts the 24 keys on a clock. The number is the position on the circle of fifths; "
    "A is minor (inner ring), B is major (outer ring). Keys next to each other share six of their seven "
    "notes, and the same number in A and B share all seven. That's why the safe moves are: stay put, one "
    "step either way, or switch between A and B on the same number."
)
