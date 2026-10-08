"""Quiz ("guess first") questions about Camelot moves, with answers explained.

Questions are drawn so every move type comes up regularly (random key pairs
would be mostly off-key). With library tracks, questions use real tracks.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .camelot import (
    ALL_KEYS, CLASH, DEFAULT_MOVES, ENERGY, SMOOTH, TIER_NAMES, Key, MoveDef, classify, move_name,
)
from .theory import Explanation, explain, key_name
from .track import Track

# Question kinds
NAME_MOVE = "name_move"  # What move is 8A → 9A?
TIER = "tier"  # Smooth, energy move or clash?
ENERGY_DIR = "energy"  # Does the energy go up, down or stay?
FIND_KEY = "find_key"  # Which key is a +1 from 8A?
EAR = "ear"  # Listen: does this blend sound in key?
KINDS = {
    NAME_MOVE: "Name the move",
    TIER: "In key or clash?",
    ENERGY_DIR: "Energy up or down?",
    FIND_KEY: "Find the key",
    EAR: "Ear training",
}

# Moves a quiz asks about, with how often (relative weights).
QUIZ_MOVES = {
    "Same key": 1, "+1": 3, "−1": 3, "Relative major": 2, "Relative minor": 2,
    "Diagonal up": 2, "Diagonal down": 2, "+2": 2, "−2": 2, "Semitone up": 1, "Semitone down": 1,
    "Tritone": 1, "Off-key": 2,
}
MOVE_CHOICES = [m for m in QUIZ_MOVES if m != "Same key"]


@dataclass
class Question:
    kind: str
    prompt: str
    k1: Key
    k2: Optional[Key]
    options: list[str]
    answer: int
    explanation: Explanation
    move: str  # the move being tested, for per-move stats
    tracks: tuple[Optional[Track], Optional[Track]] = (None, None)
    audio: bool = False  # offer "play the blend" before answering

    def check(self, choice: int) -> bool:
        return choice == self.answer


def targets(k1: Key, move: str) -> list[Key]:
    return [k for k in ALL_KEYS if move_name(k1, k) == move]


def target_for(k1: Key, move: str, rng: random.Random) -> Key:
    return rng.choice(targets(k1, move))


def start_keys(move: str) -> list[Key]:
    """Keys a move can start from (diagonals only go one way per mode)."""
    return [k for k in ALL_KEYS if targets(k, move)]


def _pick_move(rng: random.Random, weights: Optional[dict[str, float]] = None) -> str:
    w = weights or QUIZ_MOVES
    names = list(w)
    return rng.choices(names, [w[n] for n in names])[0]


def _options(rng: random.Random, correct: str, pool: Sequence[str], n: int = 4) -> tuple[list[str], int]:
    others = [p for p in pool if p != correct]
    rng.shuffle(others)
    opts = others[: n - 1] + [correct]
    rng.shuffle(opts)
    return opts, opts.index(correct)


def _energy_word(md: MoveDef) -> str:
    if md.energy is None or md.tier == CLASH:
        return "Clash: the energy line breaks"
    if md.energy > 0:
        return "Goes up"
    if md.energy < 0:
        return "Goes down"
    return "Stays level"


@dataclass
class QuizConfig:
    kinds: list[str] = field(default_factory=lambda: [NAME_MOVE, TIER, ENERGY_DIR, FIND_KEY, EAR])
    moves: Optional[dict[str, MoveDef]] = None
    energy_moves_in_key: bool = True
    notation: str = "camelot"
    weights: Optional[dict[str, float]] = None  # practice weak moves more


def _pair_from_tracks(rng: random.Random, move: str, tracks: Sequence[Track]):
    """Two library tracks whose keys make ``move``, or None."""
    keyed = [t for t in tracks if t.key]
    rng.shuffle(keyed)
    by_key: dict[Key, list[Track]] = {}
    for t in keyed:
        by_key.setdefault(t.key, []).append(t)
    for a in keyed[:200]:
        cands = [k for k in by_key if move_name(a.key, k) == move]
        cands = [k for k in cands if any(t.id != a.id for t in by_key[k])]
        if cands:
            k = rng.choice(cands)
            b = rng.choice([t for t in by_key[k] if t.id != a.id])
            return a, b
    return None


def make_question(rng: random.Random, cfg: QuizConfig, tracks: Sequence[Track] = ()) -> Question:
    kind = rng.choice(cfg.kinds or [NAME_MOVE])
    move = _pick_move(rng, cfg.weights)
    pair = _pair_from_tracks(rng, move, tracks) if tracks and kind != FIND_KEY else None
    if pair:
        ta, tb = pair
        k1, k2 = ta.key, tb.key
    else:
        ta = tb = None
        k1 = rng.choice(start_keys(move))
        k2 = target_for(k1, move, rng)
    md = (cfg.moves or DEFAULT_MOVES)[move]
    mv = classify(k1, k2, cfg.moves, cfg.energy_moves_in_key)
    expl = explain(k1, k2, cfg.moves, cfg.energy_moves_in_key)
    who = (f"“{ta.artist} – {ta.title}” ({k1}) → “{tb.artist} – {tb.title}” ({k2})" if ta else f"{k1} → {k2}")

    if kind == NAME_MOVE:
        opts, ans = _options(rng, move, list(QUIZ_MOVES))
        return Question(kind, f"What move is {who}?", k1, k2, opts, ans, expl, move, (ta, tb))
    if kind == TIER:
        opts = [TIER_NAMES[SMOOTH], TIER_NAMES[ENERGY], TIER_NAMES[CLASH]]
        return Question(kind, f"Is {who} smooth, an energy move, or a clash?", k1, k2, opts,
                        opts.index(TIER_NAMES[mv.tier]), expl, move, (ta, tb))
    if kind == ENERGY_DIR:
        opts = ["Goes up", "Goes down", "Stays level", "Clash: the energy line breaks"]
        eff = MoveDef(md.name, md.label, mv.tier, mv.energy)
        return Question(kind, f"What happens to the energy on {who}?", k1, k2, opts,
                        opts.index(_energy_word(eff)), expl, move, (ta, tb))
    if kind == EAR:
        opts = ["Sounds in key (smooth)", "Noticeable but in key (energy move)", "Clashes"]
        ans = {SMOOTH: 0, ENERGY: 1, CLASH: 2}[mv.tier]
        return Question(kind, "Listen to the blend. How do these two keys sit together?", k1, k2, opts, ans,
                        expl, move, (ta, tb), audio=True)
    # FIND_KEY: which key is <move> from k1?
    move = rng.choice([m for m in MOVE_CHOICES[:8] if targets(k1, m)])  # the moves to know by heart
    k2 = target_for(k1, move, rng)
    wrong = {k for k in ALL_KEYS if k != k2 and move_name(k1, k) != move}
    near = sorted(wrong, key=lambda k: (abs((k.number - k2.number + 6) % 12 - 6) + (k.mode != k2.mode), rng.random()))
    opts = [str(k) for k in near[:3]] + [str(k2)]
    rng.shuffle(opts)
    return Question(FIND_KEY, f"You're in {k1} ({key_name(k1)}). Which key is a “{move}” move?", k1, k2, opts,
                    opts.index(str(k2)), explain(k1, k2, cfg.moves, cfg.energy_moves_in_key), move)


@dataclass
class Stats:
    """Running score; per-move counts persist in the config."""

    asked: int = 0
    correct: int = 0
    streak: int = 0
    best_streak: int = 0
    per_move: dict[str, list[int]] = field(default_factory=dict)  # move -> [correct, asked]

    def record(self, move: str, ok: bool) -> None:
        self.asked += 1
        self.correct += int(ok)
        self.streak = self.streak + 1 if ok else 0
        self.best_streak = max(self.best_streak, self.streak)
        c = self.per_move.setdefault(move, [0, 0])
        c[0] += int(ok)
        c[1] += 1

    def accuracy(self, move: str) -> Optional[float]:
        c = self.per_move.get(move)
        return c[0] / c[1] if c and c[1] else None

    def practice_weights(self) -> dict[str, float]:
        """Ask more about moves answered wrongly: weight × (1 + 2 × error rate)."""
        out = {}
        for m, w in QUIZ_MOVES.items():
            acc = self.accuracy(m)
            out[m] = w * (1 + 2 * (1 - acc)) if acc is not None else w
        return out

    def to_dict(self) -> dict:
        return {"best_streak": self.best_streak, "per_move": self.per_move,
                "asked": self.asked, "correct": self.correct}

    @classmethod
    def from_dict(cls, d: dict) -> "Stats":
        d = d or {}
        return cls(asked=int(d.get("asked", 0)), correct=int(d.get("correct", 0)),
                   best_streak=int(d.get("best_streak", 0)),
                   per_move={k: list(v) for k, v in (d.get("per_move") or {}).items()})
