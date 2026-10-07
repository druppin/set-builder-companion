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


def test_sources_selection_survives_heading_click(qtbot):
    from PySide6.QtTest import QTest

    from harmonic_set_builder.data.mixxx_db import Collection
    from harmonic_set_builder.ui.sources import SourcesTree

    lib = Library()
    lib.crates = [Collection("crate", 1, "House", []), Collection("crate", 2, "DnB", [])]
    tree = SourcesTree()
    qtbot.addWidget(tree)
    tree.populate(lib, False, False, None)
    tree.resize(300, 400)
    tree.show()
    crates = tree.topLevelItem(1)
    house = crates.child(0)

    def click(item):
        QTest.mouseClick(tree.viewport(), Qt.LeftButton, pos=tree.visualItemRect(item).center())

    with qtbot.waitSignal(tree.focused):
        click(house)
    click(crates)  # heading: folds, but House stays highlighted
    assert not crates.isExpanded()
    assert tree.selectedItems() == [house]
    click(crates)
    assert crates.isExpanded() and tree.selectedItems() == [house]


def test_duration_label(win):
    for i in (1, 3, 4):
        win.ctrl.library.get(i).duration = 300
    assert win.duration_label.text() == "Empty set"
    drop(win.set_model, "track", [1, 3, 4])
    assert win.duration_label.text() == "3 tracks · ≈ 14:00 mixed (15:00 back to back)"
    drop(win.pool_model, "track", [2])
    win.ctrl.library.get(2).duration = 300
    win.ctrl.open_pool_route(win.ctrl.model.pool[0].uid)  # 10A -> 11A is direct: completes
    assert win.duration_label.text().startswith("4 tracks · ≈ 18:30 mixed")


def test_track_filters(win):
    # library: 8A, 11A, 9A, 10A, 7A (all 128 BPM)
    win.ctrl.library.get(5).bpm = 140  # 7A: Danger BPM from 8A
    drop(win.pool_model, "track", [2, 4])
    win.want_btn.setChecked(True)
    assert sorted(win.proxy.index(i, 0).data(Qt.UserRole + 1).track.id for i in range(win.proxy.rowCount())) == [2, 4]
    assert win.count_label.text() == "2 of 5"
    win.want_btn.setChecked(False)

    win.f_in_key.setChecked(True)  # no reference yet: ignored
    assert win.proxy.rowCount() == 5
    drop(win.set_model, "track", [1])  # reference 8A
    ids = sorted(win.proxy.index(i, 0).data(Qt.UserRole + 1).track.id for i in range(win.proxy.rowCount()))
    assert ids == [1, 3, 5]  # 11A is off-key; strict mode makes 10A (+2) a break
    assert win.filter_btn.text() == "Filters (1) ▾"
    win.f_band["safe"].trigger()
    ids = sorted(win.proxy.index(i, 0).data(Qt.UserRole + 1).track.id for i in range(win.proxy.rowCount()))
    assert 5 not in ids
    win._clear_filters()
    assert win.proxy.rowCount() == 5 and win.filter_btn.text() == "Filters ▾"
    win.save_layout()
    assert win.ctrl.config.get("ui")["filters"]["band"] == "any"


def test_pool_sorts_by_column_and_keeps_drops_working(win):
    lib = win.ctrl.library
    lib.get(3).artist, lib.get(4).artist, lib.get(5).artist = "Charlie", "Alpha", "Bravo"
    drop(win.pool_model, "track", [3, 4, 5])

    def shown():
        p = win.pool_proxy
        return [p.index(i, 0).data(Qt.UserRole + 1).track.id for i in range(p.rowCount())]

    assert shown() == [3, 4, 5]  # order added
    artist = win.pool_model.col_index("artist")
    win.pool_view.sortByColumn(artist, Qt.AscendingOrder)
    assert shown() == [4, 5, 3]
    win.pool_view.sortByColumn(artist, Qt.DescendingOrder)
    assert shown() == [3, 5, 4]
    drop(win.pool_proxy, "track", [1])  # dropping still goes through the proxy
    assert 1 in shown() and shown()[-1] == 4  # new row sorted in
    win.pool_view.sortByColumn(-1, Qt.AscendingOrder)
    assert shown() == [3, 4, 5, 1]
    # the ⋯ / ▶ columns still resolve the right row through the proxy
    idx = win.pool_proxy.index(1, win.pool_model.col_index("artist"))
    assert win._row_at(win.pool_view, idx).track.id == 4


def test_pool_round_trip_through_the_ui(win, qtbot):
    drop(win.pool_model, "track", [3])
    assert win.pool_toggle.text() == "To be added (1)"
    drop(win.set_model, "track", [1, 3])  # dragged in from the track table
    assert win.pool_toggle.text() == "To be added (0)"
    win.set_view.selectRow(1)
    win._delete_selected()
    assert set_ids(win) == [1]
    assert win.pool_toggle.text() == "To be added (1)"
    win.ctrl.undo.undo()  # undoing the delete takes it back out again
    assert win.pool_toggle.text() == "To be added (0)" and set_ids(win) == [1, 3]


def test_file_dialog_sidebar_lists_drives(qtbot, monkeypatch):
    from pathlib import Path

    from harmonic_set_builder.ui import file_dialogs

    monkeypatch.setattr(file_dialogs, "mounted_drives", lambda: [Path("/run/media/me/USBSTICK")])
    d = file_dialogs._dialog(None, "Export set", str(Path.home()), "Playlist (*.m3u8)", save=True)
    qtbot.addWidget(d)
    urls = [u.toString() for u in d.sidebarUrls()]
    assert urls[0] == "file:" and urls[-1] == "file:///run/media/me/USBSTICK"
    assert d.acceptMode() == d.AcceptMode.AcceptSave
