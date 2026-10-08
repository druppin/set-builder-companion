"""One column system for the track table, setlist and pool.

Each table row is a ``Row``: a track (or transitional entry) plus its relation to
the reference track. Columns know how to display and sort a Row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont

from ..core.bpm import BAND_NAMES
from ..core.camelot import TIER_NAMES, format_key
from ..core.ranking import Relation
from ..core.setlist import Entry, SLOT, TARGET
from ..core.track import Track
from . import theme


@dataclass
class Row:
    track: Optional[Track] = None
    rel: Optional[Relation] = None
    entry: Optional[Entry] = None
    pos: Optional[int] = None  # 1-based set position
    in_set: bool = False
    want: bool = False
    clash: bool = False
    duplicate: bool = False
    missing: bool = False
    slot_active: bool = False
    slot_moves: str = ""  # expected move labels for a slot
    fixable: bool = False  # fix mode: "+" shown
    pinned: bool = False  # slot view: fitting pool track pinned at top
    rank: tuple = ()
    haystack: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def is_slot(self) -> bool:
        return bool(self.entry and self.entry.kind == SLOT)

    @property
    def is_target(self) -> bool:
        return bool(self.entry and self.entry.kind == TARGET)

    @property
    def placeholder(self) -> bool:
        return self.is_slot or self.is_target

    @property
    def ref_text(self) -> tuple[str, str]:
        ref = self.entry.ref if self.entry and self.entry.ref else self.extra.get("ref")
        return (ref.artist, ref.title) if ref else ("", "")


def _dur(sec: float) -> str:
    if not sec:
        return ""
    s = int(round(sec))
    return f"{s // 60}:{s % 60:02d}"


@dataclass
class Col:
    id: str
    header: str
    width: int
    display: Callable[[Row, str], Any]  # (row, key notation) -> text
    sort: Optional[Callable[[Row], Any]] = None
    align_right: bool = False
    relational: bool = False
    tooltip: str = ""


def _t(attr: str, fallback_ref: int = -1):
    def get(r: Row, _n):
        if r.is_slot:
            return "?" if attr in ("artist", "title") else ""
        if r.track is None:
            if fallback_ref >= 0:
                return r.ref_text[fallback_ref]
            return ""
        return getattr(r.track, attr) or ""

    return get


def _key(r: Row, n: str) -> str:
    if r.is_slot:
        return r.entry.slot.key_text(lambda k: format_key(k, n))
    return format_key(r.track.key, n) if r.track else ""


def _bpm(r: Row, _n) -> str:
    if r.is_slot:
        return r.entry.slot.bpm_text()
    if not r.track:
        return ""
    return f"{r.track.bpm:.1f}" if r.track.bpm else "?"


def _move(r: Row, _n) -> str:
    if r.is_slot:
        return r.slot_moves
    return r.rel.move.name if r.rel and r.rel.move else ""


def _mood(r: Row, _n) -> str:
    if r.is_slot:
        return "expected"
    return r.rel.move.label if r.rel and r.rel.move else ""


def _energy(r: Row, _n) -> str:
    if not (r.rel and r.rel.move):
        return ""
    e = r.rel.move.energy
    return "—" if e is None or r.rel.is_clash else f"{e:+d}" if e else "0"


def _bpmd(r: Row, _n) -> str:
    if not (r.rel and r.rel.bpm) or r.is_slot:
        return ""
    return f"{r.rel.bpm.delta:+.1f}"


def _tier(r: Row, _n) -> str:
    return TIER_NAMES[r.rel.move.tier] if r.rel and r.rel.move and not r.is_slot else ""


def _half(r: Row, _n) -> str:
    return r.rel.bpm.label if r.rel and r.rel.bpm and not r.is_slot else ""


def _rating(r: Row, _n) -> str:
    return "★" * r.track.rating if r.track and r.track.rating else ""


# Phrase-analysis summaries by file path ("I16 B8 D32 …"), kept current by the Phrases view.
STRUCTURE: dict[str, str] = {}


def _structure(r: Row, _n) -> str:
    return STRUCTURE.get(r.track.location, "") if r.track else ""


def _num_sort(attr):
    return lambda r: (getattr(r.track, attr) or 0) if r.track else -1


COLUMNS: list[Col] = [
    Col("pos", "#", 36, lambda r, n: r.pos or "", lambda r: r.pos or 0, True),
    Col("fix", "", 26, lambda r, n: "＋" if r.fixable else "", tooltip="Bridge this key clash"),
    Col("menu", "", 26, lambda r, n: "⋯", tooltip="Actions"),
    Col("preview", "▶", 28, lambda r, n: "▶" if r.track else "", tooltip="Preview (Space)"),
    Col("cover", "Art", 28, lambda r, n: "", tooltip="Cover art"),
    Col("artist", "Artist", 160, _t("artist", 0)),
    Col("title", "Title", 220, _t("title", 1)),
    Col("album", "Album", 150, _t("album")),
    Col("album_artist", "Album Artist", 120, _t("album_artist")),
    Col("genre", "Genre", 100, _t("genre")),
    Col("composer", "Composer", 100, _t("composer")),
    Col("grouping", "Grouping", 90, _t("grouping")),
    Col("year", "Year", 50, _t("year")),
    Col("tracknumber", "Track #", 50, _t("tracknumber"), align_right=True),
    Col("duration", "Duration", 60, lambda r, n: _dur(r.track.duration) if r.track else "", _num_sort("duration"), True),
    Col("bpm", "BPM", 60, _bpm, lambda r: (r.track.bpm or 0) if r.track else (r.entry.slot.bpm_min if r.is_slot else 0), True),
    Col("key", "Key", 70, _key, lambda r: (r.track.key.mode, r.track.key.number) if r.track and r.track.key else ("Z", 99)),
    Col("rating", "Rating", 70, _rating, _num_sort("rating")),
    Col("timesplayed", "Played", 50, lambda r, n: r.track.timesplayed if r.track else "", _num_sort("timesplayed"), True),
    Col("last_played_at", "Last played", 130, _t("last_played_at")),
    Col("comment", "Comment", 160, _t("comment")),
    Col("datetime_added", "Date added", 130, _t("datetime_added")),
    Col("bitrate", "Bitrate", 60, lambda r, n: r.track.bitrate or "" if r.track else "", _num_sort("bitrate"), True),
    Col("filetype", "Type", 45, _t("filetype")),
    Col("color", "Color", 45, lambda r, n: "", lambda r: (r.track.color or 0) if r.track else 0),
    Col("location", "Location", 260, _t("location")),
    Col("move", "Move", 110, _move, relational=True, tooltip="Camelot move from the reference track"),
    Col("mood", "Mood", 150, _mood, relational=True),
    Col("energy", "Energy Δ", 60, _energy,
        lambda r: (r.rel.move.energy if r.rel and r.rel.move and r.rel.move.energy is not None else -99), True, True),
    Col("bpm_delta", "BPM Δ", 60, _bpmd, lambda r: r.rel.bpm.abs_delta if r.rel and r.rel.bpm else 1e9, True, True),
    Col("tier", "Tier", 85, _tier, lambda r: r.rank[:1] if r.rank else (9,), relational=True),
    Col("half", "½/2×", 75, _half, relational=True, tooltip="Matched at half or double tempo"),
    Col("structure", "Structure", 150, _structure,
        tooltip="Sections from phrase analysis: I Intro, B Build, D Drop, Br Breakdown, G Groove, O Outro, "
                "with lengths in bars"),
    Col("in_set", "In set", 45, lambda r, n: "✓" if r.in_set else "", lambda r: r.in_set),
    Col("want", "★", 30, lambda r, n: "★" if r.want else "", lambda r: r.want, tooltip="In To be added"),
]
COL_BY_ID = {c.id: c for c in COLUMNS}

TRACK_COLUMNS = [c.id for c in COLUMNS if c.id not in ("pos", "fix", "menu")]
SET_COLUMNS = [c.id for c in COLUMNS if c.id != "menu"]
POOL_COLUMNS = [c.id for c in COLUMNS if c.id not in ("pos", "fix")]

DEFAULT_VISIBLE = {
    "track": ["preview", "cover", "artist", "title", "key", "bpm", "move", "mood", "energy", "bpm_delta", "tier", "half",
              "in_set", "want", "genre", "duration", "rating"],
    "set": ["fix", "preview", "cover", "pos", "artist", "title", "key", "bpm", "move", "mood", "energy", "bpm_delta",
            "structure"],
    "pool": ["menu", "preview", "cover", "artist", "title", "key", "bpm", "move", "bpm_delta"],
}


def foreground(col: Col, r: Row) -> Optional[QColor]:
    if r.placeholder and not r.is_target:
        return theme.SLOT_FG
    if col.id in ("key", "bpm") and r.track and (
        (col.id == "key" and r.track.key is None) or (col.id == "bpm" and not r.track.bpm)
    ):
        return theme.DIM
    if col.id == "bpm_delta" and r.rel and r.rel.bpm:
        return theme.BAND_COLORS[r.rel.bpm.band]
    if col.id in ("tier", "move") and r.rel and r.rel.move:
        return theme.TIER_COLORS[r.rel.move.tier]
    if col.id in ("want", "fix", "preview"):
        return theme.ACCENT
    if r.missing:
        return theme.DIM
    return None


def background(r: Row) -> Optional[QColor]:
    if r.clash:
        return theme.CLASH_BG
    if r.duplicate:
        return theme.DUP_BG
    if r.slot_active:
        return theme.ACTIVE_SLOT_BG
    return None


def font(r: Row) -> Optional[QFont]:
    if r.placeholder or r.missing:
        f = QFont()
        f.setItalic(True)
        return f
    return None


def tooltip(col: Col, r: Row) -> Optional[str]:
    if r.is_slot and not r.slot_active:
        return "Fill the earlier transition first"
    if r.is_slot:
        s = r.entry.slot
        src = " (from library)" if s.from_library else ""
        return f"Click to pick a track for this transition: {len(s.candidates)} candidate(s){src}"
    if r.is_target:
        return "Route target — fill the transitions above to reach it"
    if r.missing:
        return "This track can no longer be found in the Mixxx library"
    if col.id == "bpm_delta" and r.rel and r.rel.bpm:
        return BAND_NAMES[r.rel.bpm.band]
    if col.id in ("move", "mood", "energy", "tier") and r.rel and r.rel.move:
        m = r.rel.move
        e = "line breaks" if m.energy is None or r.rel.is_clash else f"energy {m.energy:+d}"
        tip = f"{m.name}: {m.label} ({TIER_NAMES[m.tier]}, {e})"
        if r.track and r.rel.effective_key and r.rel.effective_key != r.track.key:
            tip += f"\nKeylock off: plays as {r.rel.effective_key} when tempo-matched"
        return tip
    if r.duplicate:
        return "This track appears more than once in the set"
    return col.tooltip or None


def color_swatch(r: Row) -> Optional[QColor]:
    if r.track and r.track.color is not None:
        return QColor((r.track.color or 0) & 0xFFFFFF)
    return None


def make_haystack(t: Track) -> str:
    return " ".join(
        x for x in (t.artist, t.title, t.album, t.album_artist, t.genre, t.comment, t.grouping, t.composer) if x
    ).casefold()


ROW_ROLE = Qt.UserRole + 1
SORT_ROLE = Qt.UserRole + 2
