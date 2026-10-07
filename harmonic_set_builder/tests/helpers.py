from harmonic_set_builder.core.camelot import parse_key
from harmonic_set_builder.core.setlist import Context
from harmonic_set_builder.core.settings import Settings
from harmonic_set_builder.core.track import Track


def tr(tid, key, bpm=128.0, rating=0, **kw):
    return Track(tid, artist=f"Artist{tid}", title=f"T{tid} {key}", key=parse_key(key) if key else None,
                 bpm=bpm, rating=rating, location=f"/music/{tid}.mp3", **kw)


class Lib:
    def __init__(self, tracks):
        self.tracks = {t.id: t for t in tracks}

    def ctx(self, settings=None, focused=None):
        tracks = list(self.tracks.values())
        return Context(
            settings or Settings(),
            resolve=lambda ref: self.tracks.get(ref.track_id),
            focused=focused if focused is not None else tracks,
            library=tracks,
            by_id=self.tracks.get,
        )
