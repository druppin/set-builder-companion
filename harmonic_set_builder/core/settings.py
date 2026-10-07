"""User-editable settings for the pure logic (no Qt). Serialized to config JSON."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from .bpm import BpmSettings
from .camelot import DEFAULT_MOVES, MoveDef


@dataclass
class Settings:
    bpm: BpmSettings = field(default_factory=BpmSettings)
    energy_moves_in_key: bool = True  # False = strict practice: +2/semitone are breaks
    keylock: bool = True
    energy_baseline: int = 5
    energy_from_tags: bool = False  # use "Energy N" comment tags as absolute levels
    # Overrides of the move table: {move name: {"label": str, "energy": int}}
    move_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    route_allow_caution: bool = False
    route_max_hops: int = 8
    library_fallback: bool = True
    key_notation: str = "camelot"
    show_history_playlists: bool = False
    show_autodj_playlist: bool = False

    def moves(self) -> dict[str, MoveDef]:
        out = dict(DEFAULT_MOVES)
        for name, ov in self.move_overrides.items():
            if name in out:
                base = out[name]
                out[name] = MoveDef(
                    name,
                    ov.get("label", base.label),
                    base.tier,
                    ov.get("energy", base.energy) if base.energy is not None else None,
                )
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        s = cls()
        names = {f.name for f in fields(cls)}
        for k, v in (d or {}).items():
            if k == "bpm" and isinstance(v, dict):
                bnames = {f.name for f in fields(BpmSettings)}
                s.bpm = BpmSettings(**{bk: bv for bk, bv in v.items() if bk in bnames})
            elif k in names:
                setattr(s, k, v)
        return s
