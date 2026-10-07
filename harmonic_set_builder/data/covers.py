"""Find a track's cover art: Mixxx's recorded cover file, art embedded in the
audio file (ID3v2 APIC/PIC, FLAC PICTURE, MP4 covr) or an image in the album
folder. Pure standard library; returns raw image bytes."""
from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import BinaryIO, Optional

# Mixxx CoverInfo::Type
COVER_NONE, COVER_METADATA, COVER_FILE = 0, 1, 2

FOLDER_NAMES = ("cover", "folder", "front", "album", "albumart", "albumartsmall")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")
MAX_TAG = 64 * 1024 * 1024  # ignore absurd sizes from corrupt headers


def find_cover(location: str, cover_type: int = COVER_NONE, cover_location: str = "") -> Optional[bytes]:
    if not location:
        return None
    track = Path(location)
    if cover_type == COVER_FILE and cover_location:
        p = Path(cover_location)
        p = p if p.is_absolute() else track.parent / p
        data = _read(p)
        if data:
            return data
    if track.is_file():
        try:
            data = extract_embedded(track)
        except (OSError, ValueError, struct.error):
            data = None
        if data:
            return data
    p = folder_image(track)
    return _read(p) if p else None


def _read(p: Path) -> Optional[bytes]:
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None


def folder_image(track: Path) -> Optional[Path]:
    """cover/folder/front.* in the track's folder, else the only image there."""
    try:
        images = [e for e in os.scandir(track.parent) if e.is_file() and e.name.lower().endswith(IMAGE_EXTS)]
    except OSError:
        return None
    by_stem = {Path(e.name).stem.lower(): e for e in images}
    for name in FOLDER_NAMES:
        if name in by_stem:
            return Path(by_stem[name].path)
    return Path(images[0].path) if len(images) == 1 else None


def extract_embedded(path: Path) -> Optional[bytes]:
    with open(path, "rb") as f:
        head = f.read(12)
        f.seek(0)
        if head[:3] == b"ID3":
            return _id3(f)
        if head[:4] == b"fLaC":
            return _flac(f)
        if head[4:8] == b"ftyp":
            return _mp4(f, os.fstat(f.fileno()).st_size)
    return None


# ---------------------------------------------------------------- ID3v2
def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _id3(f: BinaryIO) -> Optional[bytes]:
    hdr = f.read(10)
    major, flags = hdr[3], hdr[5]
    size = _syncsafe(hdr[6:10])
    if size > MAX_TAG:
        return None
    tag = f.read(size)
    if flags & 0x80 and major < 4:  # whole-tag unsynchronisation
        tag = tag.replace(b"\xff\x00", b"\xff")
    pos = 0
    if flags & 0x40 and major >= 3:  # extended header
        ext = _syncsafe(tag[:4]) if major == 4 else struct.unpack(">I", tag[:4])[0] + 4
        pos = ext
    best = None
    while pos + (6 if major == 2 else 10) <= len(tag):
        if major == 2:
            fid, fsize = tag[pos:pos + 3], int.from_bytes(tag[pos + 3:pos + 6], "big")
            body_at = pos + 6
            fflags = 0
        else:
            fid = tag[pos:pos + 4]
            raw = tag[pos + 4:pos + 8]
            fsize = _syncsafe(raw) if major == 4 else struct.unpack(">I", raw)[0]
            fflags = int.from_bytes(tag[pos + 8:pos + 10], "big")
            body_at = pos + 10
        if not fid.strip(b"\x00") or fsize <= 0:
            break
        body = tag[body_at:body_at + fsize]
        pos = body_at + fsize
        if fid in (b"APIC", b"PIC"):
            if major == 4 and fflags & 0x0002:  # frame-level unsynchronisation
                body = body.replace(b"\xff\x00", b"\xff")
            if major == 4 and fflags & 0x0001:  # data length indicator
                body = body[4:]
            pic = _apic(body, fid == b"PIC")
            if pic:
                ptype, data = pic
                if ptype == 3:  # front cover
                    return data
                best = best or data
    return best


def _apic(body: bytes, v22: bool) -> Optional[tuple[int, bytes]]:
    if len(body) < 4:
        return None
    enc = body[0]
    if v22:
        i = 4  # encoding + 3-char image format
    else:
        i = body.index(b"\x00", 1) + 1  # MIME type, latin-1
    ptype = body[i]
    i += 1
    if enc in (1, 2):  # UTF-16 description: double-NUL terminator on an even boundary
        while i + 1 < len(body) and body[i:i + 2] != b"\x00\x00":
            i += 2
        i += 2
    else:
        i = body.index(b"\x00", i) + 1
    data = body[i:]
    return (ptype, data) if data else None


# ----------------------------------------------------------------- FLAC
def _flac(f: BinaryIO) -> Optional[bytes]:
    f.read(4)
    best = None
    while True:
        h = f.read(4)
        if len(h) < 4:
            return best
        last, btype, size = h[0] & 0x80, h[0] & 0x7F, int.from_bytes(h[1:4], "big")
        if btype == 6:
            b = f.read(size)
            ptype = struct.unpack(">I", b[:4])[0]
            i = 4
            n = struct.unpack(">I", b[i:i + 4])[0]
            i += 4 + n
            n = struct.unpack(">I", b[i:i + 4])[0]
            i += 4 + n + 16
            n = struct.unpack(">I", b[i:i + 4])[0]
            data = b[i + 4:i + 4 + n]
            if ptype == 3:
                return data
            best = best or data
        else:
            f.seek(size, 1)
        if last:
            return best


# ------------------------------------------------------------------ MP4
_CONTAINERS = {b"moov", b"udta", b"meta", b"ilst", b"covr"}


def _mp4(f: BinaryIO, end: int, start: int = 0, path: tuple = ()) -> Optional[bytes]:
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        h = f.read(8)
        size, kind = struct.unpack(">I", h[:4])[0], h[4:8]
        hdr = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return None
        if kind == b"data" and path and path[-1] == b"covr":
            f.seek(pos + hdr + 8)  # 4 bytes type + 4 bytes locale
            return f.read(size - hdr - 8)
        if kind in _CONTAINERS:
            inner = pos + hdr + (4 if kind == b"meta" else 0)  # meta is a full box
            found = _mp4(f, pos + size, inner, path + (kind,))
            if found:
                return found
        pos += size
    return None
