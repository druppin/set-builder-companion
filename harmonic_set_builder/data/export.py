"""Export a set as .m3u8 (for Mixxx's Import Playlist) or a plain-text tracklist,
and read .m3u/.m3u8 files back."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Sequence

from ..core.camelot import format_key
from ..core.ranking import Relation
from ..core.track import Track


def m3u8_text(tracks: Sequence[Track]) -> str:
    lines = ["#EXTM3U"]
    for t in tracks:
        lines.append(f"#EXTINF:{int(round(t.duration or 0))},{t.artist} - {t.title}")
        lines.append(t.location)
    return "\n".join(lines) + "\n"


def write_m3u8(path: Path, tracks: Sequence[Track]) -> None:
    Path(path).write_text(m3u8_text(tracks), encoding="utf-8")


def tracklist_text(
    tracks: Sequence[Track], relations: Sequence[Optional[Relation]], notation: str = "camelot"
) -> str:
    lines = []
    for i, (t, rel) in enumerate(zip(tracks, relations), 1):
        bpm = f"{t.bpm:.1f}" if t.bpm else "?"
        move = rel.move.name if rel and rel.move else ""
        lines.append(f"{i:>2}. {t.artist} – {t.title}  [{format_key(t.key, notation)}, {bpm} BPM]  {move}".rstrip())
    return "\n".join(lines) + "\n"


def read_m3u(path: Path) -> list[str]:
    """Absolute paths listed in an .m3u/.m3u8 file (relative ones resolved)."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    base = Path(path).parent
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("file://"):
            from urllib.parse import unquote, urlparse

            line = unquote(urlparse(line).path)
        p = Path(line)
        out.append(str(p if p.is_absolute() else (base / p)))
    return [os.path.normpath(p) for p in out]


def match_paths(paths: Sequence[str], lookup: Callable[[str], Optional[Track]]) -> tuple[list[Track], list[str]]:
    found, missing = [], []
    for p in paths:
        t = lookup(p)
        (found.append(t) if t else missing.append(p))
    return found, missing
