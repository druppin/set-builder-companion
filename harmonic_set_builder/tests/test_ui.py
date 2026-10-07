"""UI smoke tests (pytest-qt, offscreen): drag-drop into the setlist and onto a
slot, slot view toggling, undo, fix mode."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QModelIndex, Qt  # noqa: E402

from harmonic_set_builder.core.settings import Settings  # noqa: E402
from harmonic_set_builder.data.config import Config  # noqa: E402
from harmonic_set_builder.data.mixxx_db import Library  # noqa: E402
from harmonic_set_builder.ui.controller import Controller  # noqa: E402
from harmonic_set_builder.ui.main_window import MainWindow  # noqa: E402
from harmonic_set_builder.ui.models import encode  # noqa: E402

from .helpers import tr  # noqa: E402


@pytest.fixture
def win(qtbot, tmp_path):
    config = Config(tmp_path / "config.json")
    config.settings = Settings(energy_moves_in_key=False)
    ctrl = Controller(config, tmp_path)
    ctrl.settings = config.settings
    lib = Library()
    for t in [tr(1, "8A"), tr(2, "11A"), tr(3, "9A"), tr(4, "10A"), tr(5, "7A")]:
        lib.tracks[t.id] = t
    lib._index()
    ctrl.library = lib
    w = MainWindow(ctrl)
    qtbot.addWidget(w)
    ctrl.load_initial()
    ctrl.libraryChanged.emit()
    ctrl.setChanged.emit()
    return w


def drop(model, kind, ids, row=-1, parent=QModelIndex(), uids=None):
    md = encode(kind, ids, uids or [""] * len(ids))
    assert model.canDropMimeData(md, Qt.CopyAction, row, 0, parent)
    model.dropMimeData(md, Qt.CopyAction, row, 0, parent)


def set_ids(w):
    return [r.track.id if r.track else None for r in w.set_model.rows]


def test_drop_tracks_into_setlist_and_autosave(win, qtbot, tmp_path):
    drop(win.set_model, "track", [1, 3])
    drop(win.set_model, "track", [4], row=1)  # insertion line between rows
    assert set_ids(win) == [1, 4, 3]
    assert win.track_model.rowCount() == 5
    set_dir = tmp_path / "sets" / win.ctrl.model.id
    qtbot.waitUntil(lambda: len(list(set_dir.glob("*.json"))) >= 2, timeout=3000)


def test_reorder_by_dragging_set_rows(win):
    drop(win.set_model, "track", [1, 3, 4])
    uid = win.set_model.rows[2].entry.uid
    drop(win.set_model, "set", [4], row=0, uids=[uid])
    assert set_ids(win) == [4, 1, 3]


def test_route_slot_view_and_fill_by_drop(win):
    c = win.ctrl
    drop(win.set_model, "track", [1])
    drop(win.pool_model, "track", [2])
    c.open_pool_route(c.model.pool[0].uid)
    kinds = [r.entry.kind for r in win.set_model.rows]
    assert kinds == ["track", "slot", "slot", "target"]
    assert not (win.set_model.flags(win.set_model.index(2, 0)) & Qt.ItemIsEnabled)  # only first is active

    slot_index = win.set_model.index(1, win.set_model.col_index("artist"))
    win._set_clicked(slot_index)
    assert c.slot_view and not win.banner.isHidden()
    assert "Transition 1 of 2" in win.banner.text()
    assert [r.track.id for r in win.track_model.rows] == [3]

    drop(win.set_model, "track", [3], parent=slot_index)  # drop onto the active entry
    assert set_ids(win)[:2] == [1, 3]
    assert "Transition 1 of 1" in win.banner.text()  # stays in slot view on the next one

    win.track_view.doubleClicked.emit(win.proxy.index(0, 0))  # double-click fills
    assert set_ids(win) == [1, 3, 4, 2]
    assert c.model.route is None and not c.slot_view and win.banner.isHidden()

    c.undo.undo()
    assert [r.entry.kind for r in win.set_model.rows][-2:] == ["slot", "target"]
    c.undo.redo()
    assert set_ids(win) == [1, 3, 4, 2]


def test_toggle_slot_view_back(win):
    c = win.ctrl
    drop(win.set_model, "track", [1])
    drop(win.pool_model, "track", [2])
    c.open_pool_route(c.model.pool[0].uid)
    idx = win.set_model.index(1, 3)
    win._set_clicked(idx)
    win._set_clicked(idx)
    assert not c.slot_view and c.model.route is not None
    assert win.track_model.rowCount() == 5


def test_fix_mode_button(win):
    c = win.ctrl
    drop(win.set_model, "track", [1, 2])  # 8A -> 11A clash
    assert win.set_model.rows[1].clash
    win.fix_btn.setChecked(True)
    assert win.set_model.rows[1].fixable
    win._set_clicked(win.set_model.index(1, win.set_model.col_index("fix")))
    assert c.model.route and c.model.route.kind == "fix"
    win.fix_btn.setChecked(False)
    assert c.model.route is None and set_ids(win) == [1, 2]


def test_graph_points_and_hide_in_set(win):
    drop(win.set_model, "track", [1, 3])
    win.hide_cb.setChecked(True)
    assert win.proxy.rowCount() == 3
    pts = win.ctrl.set_rows()[1]
    assert [p.y for p in pts] == [5, 6]


def _silent_wav(path, seconds=3):
    import wave

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 8000 * seconds)


def test_preview_plays_and_toggles(win, qtbot, tmp_path):
    wav = tmp_path / "song.wav"
    _silent_wav(wav)
    t = win.ctrl.library.get(3)
    t.location = str(wav)
    win.preview.volume.setValue(0)
    win.ctrl.viewChanged.emit()
    col = win.track_model.col_index("preview")
    row = next(i for i in range(win.proxy.rowCount())
               if win.proxy.index(i, 0).data(Qt.UserRole + 1).track.id == 3)
    win._track_clicked(win.proxy.index(row, col))
    qtbot.waitUntil(lambda: win.preview.playing_id == 3, timeout=5000)
    assert win.track_model.playing_id == 3
    assert win.proxy.index(row, col).data() == "⏸"
    assert "T3 9A" in win.preview.title.text()
    win._track_clicked(win.proxy.index(row, col))  # same track again: pause
    qtbot.waitUntil(lambda: win.preview.playing_id is None, timeout=3000)
    assert win.proxy.index(row, col).data() == "▶"


def test_preview_missing_file_reports(win, qtbot):
    with qtbot.waitSignal(win.preview.message) as sig:
        win.preview.preview(win.ctrl.library.get(1))
    assert "file not found" in sig.args[0]
    assert win.preview.playing_id is None
