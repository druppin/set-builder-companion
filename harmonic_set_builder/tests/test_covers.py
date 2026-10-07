import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from harmonic_set_builder.data.covers import COVER_FILE, extract_embedded, find_cover  # noqa: E402

IMG = b"\x89PNG\r\n\x1a\n-fake-image-bytes"
BACK = b"\xff\xd8\xff-back-cover"


def _syncsafe(n):
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def id3_file(path, major=3, frames=None, unicode_desc=False):
    if frames is None:
        desc = "cover".encode("utf-16") + b"\x00\x00" if unicode_desc else b"cover\x00"
        enc = b"\x01" if unicode_desc else b"\x00"
        frames = [
            (b"TIT2", b"\x00Title"),
            (b"APIC", b"\x00image/jpeg\x00\x04back\x00" + BACK),  # back cover first
            (b"APIC", enc + b"image/png\x00\x03" + desc + IMG),  # front cover
        ]
    body = b""
    for fid, data in frames:
        size = _syncsafe(len(data)) if major == 4 else struct.pack(">I", len(data))
        body += fid + size + b"\x00\x00" + data
    path.write_bytes(b"ID3" + bytes([major, 0, 0]) + _syncsafe(len(body)) + body + b"\xff\xfb" + b"\0" * 64)
    return path


def test_id3v23_prefers_front_cover(tmp_path):
    assert extract_embedded(id3_file(tmp_path / "a.mp3")) == IMG


def test_id3v24_and_utf16_description(tmp_path):
    assert extract_embedded(id3_file(tmp_path / "a.mp3", major=4, unicode_desc=True)) == IMG


def test_id3v22_pic(tmp_path):
    data = b"\x00PNG\x03cover\x00" + IMG
    body = b"PIC" + len(data).to_bytes(3, "big") + data
    p = tmp_path / "old.mp3"
    p.write_bytes(b"ID3\x02\x00\x00" + _syncsafe(len(body)) + body)
    assert extract_embedded(p) == IMG


def test_flac_picture(tmp_path):
    def block(btype, data, last=False):
        return bytes([btype | (0x80 if last else 0)]) + len(data).to_bytes(3, "big") + data

    pic = struct.pack(">I", 3) + struct.pack(">I", 9) + b"image/png" + struct.pack(">I", 0) + b"\0" * 16
    pic += struct.pack(">I", len(IMG)) + IMG
    p = tmp_path / "a.flac"
    p.write_bytes(b"fLaC" + block(0, b"\0" * 34) + block(6, pic, last=True) + b"audio")
    assert extract_embedded(p) == IMG


def test_mp4_covr(tmp_path):
    def atom(kind, payload):
        return struct.pack(">I", 8 + len(payload)) + kind + payload

    covr = atom(b"covr", atom(b"data", struct.pack(">II", 14, 0) + IMG))
    meta = atom(b"meta", b"\0\0\0\0" + atom(b"hdlr", b"\0" * 25) + atom(b"ilst", covr))
    moov = atom(b"moov", atom(b"mvhd", b"\0" * 100) + atom(b"udta", meta))
    p = tmp_path / "a.m4a"
    p.write_bytes(atom(b"ftyp", b"M4A \0\0\0\0") + atom(b"mdat", b"\0" * 50) + moov)
    assert extract_embedded(p) == IMG


def test_mixxx_cover_file_then_folder_then_none(tmp_path):
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    track = album / "Song - Artist.mp3"
    track.write_bytes(b"\xff\xfb" + b"\0" * 32)  # no tag
    (album / "Scan.jpg").write_bytes(b"scan")
    (album / "Folder.jpg").write_bytes(b"folder")
    assert find_cover(str(track), COVER_FILE, "Scan.jpg") == b"scan"
    assert find_cover(str(track)) == b"folder"  # folder/cover names win over other images
    (album / "Folder.jpg").unlink()
    assert find_cover(str(track)) == b"scan"  # the only image left
    (album / "Scan.jpg").unlink()
    assert find_cover(str(track)) is None
    assert find_cover("/no/such/file.mp3") is None


def test_corrupt_tag_does_not_raise(tmp_path):
    p = tmp_path / "bad.mp3"
    p.write_bytes(b"ID3\x03\x00\x00\x00\x00\x01\x00APIC\xff\xff\xff\xff")
    assert find_cover(str(p)) is None


def test_cover_cache_loads_in_background(qtbot, tmp_path):
    from PySide6.QtGui import QColor, QImage

    from harmonic_set_builder.core.track import Track
    from harmonic_set_builder.ui.covers import CoverCache

    img = QImage(64, 64, QImage.Format_RGB32)
    img.fill(QColor("red"))
    (tmp_path / "music").mkdir()
    img.save(str(tmp_path / "music" / "cover.png"))
    song = tmp_path / "music" / "song.mp3"
    song.write_bytes(b"\xff\xfb" + b"\0" * 32)
    t = Track(7, location=str(song))
    cache = CoverCache(tmp_path / "cache")
    with qtbot.waitSignal(cache.loaded):
        assert cache.pixmap(t, 20) is None  # starts loading
    pm = cache.pixmap(t, 20)
    assert pm is not None and pm.width() == 20
    assert len(list((tmp_path / "cache").glob("*.png"))) == 1  # disk cache written
    cache.clear_memory()
    with qtbot.waitSignal(cache.loaded):
        cache.pixmap(t, 20)
    assert cache.has_cover(t)
