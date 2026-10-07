"""Main window: energy graph on top; setlist + pool | track table | sources."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QItemSelectionModel, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

from ..core.bpm import BAND_NAMES
from ..core.camelot import TIER_NAMES
from ..core.setlist import POOL_ROUTE
from . import columns as C
from .controller import Controller
from .graph import EnergyGraph
from .models import RowModel, TrackProxy
from .covers import CoverCache
from .models import cover_tooltip
from .preview import PreviewBar
from .settings_dialog import SettingsDialog
from .sources import SourcesTree
from .views import RowTable


class MainWindow(QMainWindow):
    def __init__(self, ctrl: Controller):
        super().__init__()
        self.ctrl = ctrl
        self.setWindowTitle("Harmonic Set Builder")
        self.resize(1500, 900)
        self._building = False
        self.covers = CoverCache(ctrl.data_dir / "covers", self)

        # ---- energy graph
        self.graph = EnergyGraph()
        self.graph.pointClicked.connect(self._select_set_row)

        # ---- setlist
        self.set_model = RowModel("set", C.SET_COLUMNS)
        self.set_model.on_drop = ctrl.drop_on_setlist
        self.set_view = RowTable("set")
        self.set_view.setModel(self.set_model)
        self.set_view.setup_drag(True)
        self.set_view.setDefaultDropAction(Qt.MoveAction)
        self.set_view.clicked.connect(self._set_clicked)
        self.set_view.selectionModel().selectionChanged.connect(self._set_selection)
        self.set_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.set_view.customContextMenuRequested.connect(self._set_menu)

        self.pool_model = RowModel("pool", C.POOL_COLUMNS)
        self.pool_model.on_drop = lambda payload, row: ctrl.drop_on_pool(payload)
        self.pool_view = RowTable("pool")
        self.pool_view.setModel(self.pool_model)
        self.pool_view.setup_drag(True)
        self.pool_view.clicked.connect(self._pool_clicked)
        self.pool_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pool_view.customContextMenuRequested.connect(
            lambda pos: self._pool_menu(self.pool_view.indexAt(pos), self.pool_view.viewport().mapToGlobal(pos)))

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self._build_set_toolbar())
        self.left_split = QSplitter(Qt.Vertical)
        self.left_split.addWidget(self.set_view)
        pool_box = QWidget()
        pv = QVBoxLayout(pool_box)
        pv.setContentsMargins(0, 0, 0, 0)
        self.pool_toggle = QToolButton()
        self.pool_toggle.setCheckable(True)
        self.pool_toggle.setChecked(True)
        self.pool_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.pool_toggle.setArrowType(Qt.DownArrow)
        self.pool_toggle.setStyleSheet("QToolButton { border: 0; font-weight: bold; }")
        self.pool_toggle.toggled.connect(self._toggle_pool)
        pv.addWidget(self.pool_toggle)
        pv.addWidget(self.pool_view)
        self.left_split.addWidget(pool_box)
        self.left_split.setStretchFactor(0, 3)
        self.left_split.setStretchFactor(1, 1)
        lv.addWidget(self.left_split)

        # ---- track table
        self.track_model = RowModel("track", C.TRACK_COLUMNS)
        self.proxy = TrackProxy()
        self.proxy.setSourceModel(self.track_model)
        self.track_view = RowTable("track")
        self.track_view.setModel(self.proxy)
        self.track_view.setSortingEnabled(True)
        self.track_view.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self.track_view.setup_drag(False)
        self.track_view.doubleClicked.connect(self._track_double_clicked)
        self.track_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.track_view.customContextMenuRequested.connect(self._track_menu)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.hide()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Search the focused source…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.proxy.set_filter)
        self.hide_cb = QCheckBox("Hide tracks already in the set")
        self.hide_cb.toggled.connect(self._hide_toggled)
        suggested = QPushButton("Suggested order")
        suggested.setToolTip("Sort by suggestion ranking: tier, BPM band, |ΔBPM|, rating")
        suggested.clicked.connect(lambda: self.track_view.sortByColumn(-1, Qt.AscendingOrder))
        self.ref_label = QLabel()
        self.ref_label.setObjectName("hint")
        self.ref_cover = QLabel()
        self.ref_cover.setFixedSize(36, 36)
        mid = QWidget()
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(self.filter, 1)
        bar.addWidget(self.hide_cb)
        bar.addWidget(suggested)
        mv.addLayout(bar)
        mv.addWidget(self.banner)
        ref_row = QHBoxLayout()
        ref_row.setContentsMargins(4, 2, 4, 2)
        ref_row.addWidget(self.ref_cover)
        ref_row.addWidget(self.ref_label, 1)
        mv.addLayout(ref_row)
        mv.addWidget(self.track_view)

        # ---- sources
        self.sources = SourcesTree()
        self.sources.focused.connect(ctrl.set_focus)
        self.sources.openAsSet.connect(ctrl.import_collection)

        self.main_split = QSplitter(Qt.Horizontal)
        self.main_split.addWidget(left)
        self.main_split.addWidget(mid)
        self.main_split.addWidget(self.sources)
        self.main_split.setSizes([560, 700, 240])
        self.top_split = QSplitter(Qt.Vertical)
        self.top_split.addWidget(self.graph)
        self.top_split.addWidget(self.main_split)
        self.top_split.setSizes([200, 700])

        # ---- preview player
        self.preview = PreviewBar((ctrl.config.get("ui", {}) or {}).get("preview_volume", 0.8))
        self.preview.message.connect(lambda m: self.statusBar().showMessage(m, 10000))
        self.preview.playingChanged.connect(self._playing_changed)
        for m in (self.set_model, self.pool_model, self.track_model):
            m.covers = self.covers
            self.covers.loaded.connect(m.cover_loaded)
        self.covers.loaded.connect(self._cover_loaded)
        central = QWidget()
        cv = QVBoxLayout(central)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(self.top_split, 1)
        cv.addWidget(self.preview)
        self.setCentralWidget(central)
        for view in (self.set_view, self.pool_view, self.track_view):
            act = QAction("Preview", view)
            act.setShortcut(QKeySequence(Qt.Key_Space))
            act.setShortcutContext(Qt.WidgetShortcut)
            act.triggered.connect(lambda _=False, v=view: self._preview_current(v))
            view.addAction(act)
        self.track_view.clicked.connect(self._track_clicked)

        self.snapshot_label = QLabel()
        self.statusBar().addPermanentWidget(self.snapshot_label)
        self._build_menus()

        ctrl.libraryChanged.connect(self._library_changed)
        ctrl.setChanged.connect(self._set_changed)
        ctrl.viewChanged.connect(self._view_changed)
        ctrl.setsListChanged.connect(self._sets_list_changed)
        ctrl.message.connect(lambda m: self.statusBar().showMessage(m, 10000))
        ctrl.undo.canUndoChanged.connect(self.undo_btn.setEnabled)
        ctrl.undo.canRedoChanged.connect(self.redo_btn.setEnabled)

        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(1000)
        self._layout_timer.timeout.connect(self.save_layout)
        self.restore_layout()
        for v in (self.set_view, self.pool_view, self.track_view):
            v.layoutChanged.connect(self._layout_timer.start)
        for s in (self.left_split, self.main_split, self.top_split):
            s.splitterMoved.connect(lambda *a: self._layout_timer.start())

    # ------------------------------------------------------------ toolbar
    def _build_set_toolbar(self) -> QToolBar:
        tb = QToolBar()
        self.set_combo = QComboBox()
        self.set_combo.setMinimumWidth(160)
        self.set_combo.setToolTip("Switch set")
        self.set_combo.activated.connect(self._set_chosen)
        tb.addWidget(self.set_combo)
        set_btn = QToolButton()
        set_btn.setText("Set ▾")
        set_btn.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(set_btn)
        m.addAction("New set…", self._new_set)
        m.addAction("Rename…", self._rename_set)
        m.addAction("Duplicate…", self._duplicate_set)
        m.addAction("Delete set…", self._delete_set)
        m.addSeparator()
        m.addAction("Import Mixxx playlist…", self._import_mixxx)
        m.addAction("Import .m3u / .m3u8…", self._import_m3u)
        set_btn.setMenu(m)
        tb.addWidget(set_btn)
        tb.addSeparator()
        self.undo_btn = QToolButton(text="Undo")
        self.undo_btn.clicked.connect(self.ctrl.undo.undo)
        self.undo_btn.setEnabled(False)
        self.redo_btn = QToolButton(text="Redo")
        self.redo_btn.clicked.connect(self.ctrl.undo.redo)
        self.redo_btn.setEnabled(False)
        tb.addWidget(self.undo_btn)
        tb.addWidget(self.redo_btn)
        exp = QToolButton(text="Export ▾")
        exp.setPopupMode(QToolButton.InstantPopup)
        em = QMenu(exp)
        em.addAction("Playlist for Mixxx (.m3u8)…", lambda: self._export(False))
        em.addAction("Text tracklist (.txt)…", lambda: self._export(True))
        exp.setMenu(em)
        tb.addWidget(exp)
        tb.addSeparator()
        self.fix_btn = QToolButton(text="Help me fix key mixing mistakes")
        self.fix_btn.setCheckable(True)
        self.fix_btn.toggled.connect(self.ctrl.set_fix_mode)
        tb.addWidget(self.fix_btn)
        self.stop_btn = QToolButton(text="Stop showing route")
        self.stop_btn.clicked.connect(self.ctrl.close_route)
        self.stop_action = tb.addWidget(self.stop_btn)
        self.stop_action.setVisible(False)
        return tb

    def _build_menus(self) -> None:
        mb = self.menuBar()
        f = mb.addMenu("&File")
        f.addAction("Choose Mixxx library…", self._choose_db)
        refresh = f.addAction("Refresh library", self.ctrl.refresh_library)
        refresh.setShortcut(QKeySequence("Ctrl+R"))
        f.addSeparator()
        f.addAction("Export playlist for Mixxx (.m3u8)…", lambda: self._export(False))
        f.addAction("Export text tracklist…", lambda: self._export(True))
        f.addSeparator()
        f.addAction("Quit", self.close, QKeySequence.Quit)
        e = mb.addMenu("&Edit")
        u = self.ctrl.undo.createUndoAction(self, "Undo")
        u.setShortcut(QKeySequence.Undo)
        r = self.ctrl.undo.createRedoAction(self, "Redo")
        r.setShortcut(QKeySequence.Redo)
        e.addAction(u)
        e.addAction(r)
        e.addSeparator()
        e.addAction("Settings…", self._settings)
        h = mb.addMenu("&Help")
        h.addAction("Move legend", self._legend)
        delete = QAction(self)
        delete.setShortcut(QKeySequence.Delete)
        delete.triggered.connect(self._delete_selected)
        self.set_view.addAction(delete)
        delete.setShortcutContext(Qt.WidgetShortcut)

    # ------------------------------------------------------------ refresh
    def _library_changed(self) -> None:
        self.covers.clear_memory()
        lib = self.ctrl.library
        s = self.ctrl.settings
        focus = (self.ctrl.focus.kind, self.ctrl.focus.id) if self.ctrl.focus else None
        self.sources.populate(lib, s.show_history_playlists, s.show_autodj_playlist, focus)
        when = lib.snapshot_time.strftime("%H:%M:%S") if lib.snapshot_time else "—"
        self.snapshot_label.setText(f"Library snapshot {when} · {len(lib.tracks)} tracks")
        for m in (self.set_model, self.pool_model, self.track_model):
            m.notation = s.key_notation
        self.preview.notation = s.key_notation
        self._view_changed()

    def _set_changed(self) -> None:
        self._building = True
        rows, pts = self.ctrl.set_rows()
        self.set_model.set_rows(rows)
        sel = self.ctrl.selected_uid
        if sel:
            for i, r in enumerate(rows):
                if r.entry and r.entry.uid == sel:
                    self.set_view.selectionModel().select(
                        self.set_model.index(i, 0), QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                    break
        self._building = False
        self.graph.set_points(pts)
        self.pool_model.set_rows(self.ctrl.pool_rows())
        self.pool_toggle.setText(f"To be added ({len(self.ctrl.model.pool)})")
        route = self.ctrl.model.route
        self.stop_action.setVisible(route is not None)
        if route:
            self.statusBar().showMessage(route.message, 15000)
        self._sync_combo()
        self._view_changed()

    def _view_changed(self) -> None:
        rows, banner = self.ctrl.track_rows()
        h = self.track_view.horizontalHeader()
        col, order = h.sortIndicatorSection(), h.sortIndicatorOrder()
        self.track_model.set_rows(rows)
        if self.ctrl.slot_view:
            self.track_view.sortByColumn(-1, Qt.AscendingOrder)
        elif col >= 0:
            self.proxy.sort(col, order)
        self.proxy.set_hide_in_set(self.ctrl.hide_in_set)
        self.banner.setVisible(bool(banner))
        self.banner.setText(banner)
        ref = self.ctrl.reference_track()
        self._ref_track = ref
        self._update_ref_cover()
        self.ref_label.setText(f"Compared with: {ref.artist} – {ref.title}" if ref else
                               "Add a track to the set to see how others mix with it.")
        self.pool_model.set_rows(self.ctrl.pool_rows())

    def _sets_list_changed(self) -> None:
        self._sync_combo()

    def _sync_combo(self) -> None:
        sets = self.ctrl.store.list_sets()
        if self.ctrl.model.id not in [s[0] for s in sets]:
            sets.insert(0, (self.ctrl.model.id, self.ctrl.model.name))
        self.set_combo.blockSignals(True)
        self.set_combo.clear()
        for sid, name in sets:
            self.set_combo.addItem(self.ctrl.model.name if sid == self.ctrl.model.id else name, sid)
        self.set_combo.setCurrentIndex(self.set_combo.findData(self.ctrl.model.id))
        self.set_combo.blockSignals(False)

    # ------------------------------------------------------------ setlist
    def _row_at(self, view, index) -> C.Row | None:
        return index.data(C.ROW_ROLE) if index.isValid() else None

    def _set_clicked(self, index) -> None:
        r = self._row_at(self.set_view, index)
        if r is None:
            return
        col = self.set_model.cols[index.column()].id
        if col == "preview":
            self.preview.preview(r.track)
        elif col == "fix" and r.fixable:
            self.ctrl.open_fix_route(r.entry.uid)
        elif r.is_slot and r.slot_active:
            self.ctrl.toggle_slot_view()

    def _set_selection(self, *_):
        if self._building:
            return
        rows = self.set_view.selected_rows()
        real = next((r for r in rows if r.entry and r.entry.is_real), None)
        self.ctrl.select(real.entry.uid if real else None)

    def _select_set_row(self, row: int) -> None:
        self.set_view.selectRow(row)
        self.set_view.scrollTo(self.set_model.index(row, 0))

    def _delete_selected(self) -> None:
        uids = [r.entry.uid for r in self.set_view.selected_rows() if r.entry and r.entry.is_real]
        if uids:
            self.ctrl.remove_entries(uids)

    def _set_menu(self, pos) -> None:
        index = self.set_view.indexAt(pos)
        r = self._row_at(self.set_view, index)
        menu = QMenu(self)
        route = self.ctrl.model.route
        if r and r.track:
            menu.addAction("Preview", lambda: self.preview.preview(r.track))
        if r and r.entry and r.entry.is_real:
            menu.addAction("Remove from set", self._delete_selected)
            if r.track:
                menu.addAction("Add to To be added", lambda: self.ctrl.add_to_pool([r.track.id]))
            if r.clash:
                menu.addAction("Fix this key clash", lambda: self.ctrl.open_fix_route(r.entry.uid))
        if r and r.is_slot and r.slot_active:
            menu.addAction("Pick a track for this transition", self.ctrl.toggle_slot_view)
        if route and (r is None or r.placeholder or r.entry.uid == route.target_uid):
            menu.addAction("Stop showing route", self.ctrl.close_route)
        if not menu.isEmpty():
            menu.exec(self.set_view.viewport().mapToGlobal(pos))

    # --------------------------------------------------------------- pool
    def _toggle_pool(self, on: bool) -> None:
        self.pool_view.setVisible(on)
        self.pool_toggle.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

    def _pool_clicked(self, index) -> None:
        col = self.pool_model.cols[index.column()].id
        if col == "preview":
            self.preview.preview(self._row_at(self.pool_view, index).track)
        elif col == "menu":
            rect = self.pool_view.visualRect(index)
            self._pool_menu(index, self.pool_view.viewport().mapToGlobal(rect.bottomLeft()))

    def _pool_menu(self, index, global_pos) -> None:
        r = self._row_at(self.pool_view, index)
        if r is None:
            return
        uid = r.extra["uid"]
        route = self.ctrl.model.route
        menu = QMenu(self)
        if r.track:
            menu.addAction("Preview", lambda: self.preview.preview(r.track))
            menu.addAction("Suggest where to place it", lambda: self._placements(uid))
            menu.addAction("Show me how to get here", lambda: self._route_to(uid))
        if route and route.kind == POOL_ROUTE and route.target_uid == uid:
            menu.addAction("Stop showing route", self.ctrl.close_route)
        menu.addAction("Remove from To be added", lambda: self.ctrl.remove_from_pool(uid))
        menu.exec(global_pos)

    def _route_to(self, uid: str) -> None:
        out = self.ctrl.open_pool_route(uid)
        if out and "empty" in out.message:
            if QMessageBox.question(self, "Empty set", "The set is empty, so there is nothing to route from.\n"
                                    "Place this track as the opening track?") == QMessageBox.Yes:
                self.ctrl.place_as_opener(uid)

    def _placements(self, uid: str) -> None:
        gaps = self.ctrl.placements(uid)
        if not gaps:
            box = QMessageBox(self)
            box.setWindowTitle("No place fits")
            box.setText("There is no gap in the set where this track mixes in key on both sides.")
            go = box.addButton("Show me how to get here", QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Close)
            box.exec()
            if box.clickedButton() is go:
                self._route_to(uid)
            return
        PlacementDialog(self, uid, gaps).exec()

    def highlight_gap(self, gap: int) -> None:
        """Select the real rows on either side of a gap."""
        reals = [i for i, r in enumerate(self.set_model.rows) if r.entry and r.entry.is_real]
        sel = self.set_view.selectionModel()
        sel.clearSelection()
        for i in (gap - 1, gap):
            if 0 <= i < len(reals):
                sel.select(self.set_model.index(reals[i], 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
        if reals:
            self.set_view.scrollTo(self.set_model.index(reals[min(max(gap - 1, 0), len(reals) - 1)], 0))

    # -------------------------------------------------------------- tracks
    def _track_double_clicked(self, index) -> None:
        r = self._row_at(self.track_view, index)
        if r and r.track:
            if self.ctrl.slot_view:
                self.ctrl.fill_active([r.track.id])
            else:
                self.ctrl.append_tracks([r.track.id])

    def _track_menu(self, pos) -> None:
        rows = [r for r in self.track_view.selected_rows() if r.track]
        if not rows:
            return
        ids = [r.track.id for r in rows]
        menu = QMenu(self)
        menu.addAction("Preview", lambda: self.preview.preview(rows[0].track))
        if self.ctrl.slot_view:
            menu.addAction("Fill this transition", lambda: self.ctrl.fill_active(ids[:1]))
        menu.addAction("Add to set", lambda: self.ctrl.append_tracks(ids))
        menu.addAction("Add to To be added", lambda: self.ctrl.add_to_pool(ids))
        menu.exec(self.track_view.viewport().mapToGlobal(pos))

    def _track_clicked(self, index) -> None:
        if self.track_model.cols[index.column()].id == "preview":
            r = self._row_at(self.track_view, index)
            if r:
                self.preview.preview(r.track)

    def _preview_current(self, view) -> None:
        r = self._row_at(view, view.currentIndex())
        if r and r.track:
            self.preview.preview(r.track)
        else:
            self.preview.toggle()

    def _update_ref_cover(self) -> None:
        ref = getattr(self, "_ref_track", None)
        pm = self.covers.pixmap(ref, 36)
        self.ref_cover.setPixmap(pm) if pm else self.ref_cover.clear()
        self.ref_cover.setToolTip(cover_tooltip(self.covers, ref) or "")

    def _cover_loaded(self, track_id: int) -> None:
        ref = getattr(self, "_ref_track", None)
        if ref and ref.id == track_id:
            self._update_ref_cover()
        if self.preview.track and self.preview.track.id == track_id:
            self.preview.set_cover(self.covers.pixmap(self.preview.track, 44))

    def _playing_changed(self, track_id) -> None:
        if self.preview.track:
            self.preview.set_cover(self.covers.pixmap(self.preview.track, 44))
        for m in (self.set_model, self.pool_model, self.track_model):
            m.set_playing(track_id)

    def _hide_toggled(self, on: bool) -> None:
        self.ctrl.set_hide_in_set(on)

    # ------------------------------------------------------------- dialogs
    def _set_chosen(self, i: int) -> None:
        sid = self.set_combo.itemData(i)
        if sid and sid != self.ctrl.model.id:
            self.ctrl.open_set(sid)

    def _ask_name(self, title: str, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, "Set name:", text=default)
        return name.strip() if ok and name.strip() else None

    def _new_set(self):
        if name := self._ask_name("New set", "New set"):
            self.ctrl.new_set(name)

    def _rename_set(self):
        if name := self._ask_name("Rename set", self.ctrl.model.name):
            self.ctrl.rename_set(name)

    def _duplicate_set(self):
        if name := self._ask_name("Duplicate set", f"{self.ctrl.model.name} (copy)"):
            self.ctrl.duplicate_set(name)

    def _delete_set(self):
        if QMessageBox.question(self, "Delete set", f"Delete “{self.ctrl.model.name}” and its saved versions?"
                                ) == QMessageBox.Yes:
            self.ctrl.delete_set()

    def _import_mixxx(self):
        lib = self.ctrl.library
        items = [(f"Playlist: {p.name}", p) for p in lib.playlists if p.hidden == 0] + \
                [(f"Crate: {c.name}", c) for c in lib.crates]
        if not items:
            QMessageBox.information(self, "Import", "No Mixxx playlists or crates found.")
            return
        label, ok = QInputDialog.getItem(self, "Import Mixxx playlist", "Open as a new set:",
                                         [i[0] for i in items], 0, False)
        if ok:
            self.ctrl.import_collection(dict(items)[label])

    def _import_m3u(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import playlist", str(Path.home()), "Playlists (*.m3u *.m3u8)")
        if path:
            missing = self.ctrl.import_m3u(Path(path))
            if missing:
                QMessageBox.warning(self, "Some tracks not found",
                                    f"{len(missing)} file(s) are not in the Mixxx library:\n\n" + "\n".join(missing[:30]))

    def _export(self, as_text: bool) -> None:
        if self.ctrl.model.has_unfilled():
            if QMessageBox.question(
                self, "Unfilled transitions",
                "This set still has unfilled transitional entries.\nExport only the real tracks?",
            ) != QMessageBox.Yes:
                return
        ext, filt = (".txt", "Text (*.txt)") if as_text else (".m3u8", "Playlist (*.m3u8)")
        default = str(Path.home() / f"{self.ctrl.model.name}{ext}")
        path, _ = QFileDialog.getSaveFileName(self, "Export set", default, filt)
        if path:
            self.ctrl.export(Path(path), as_text)

    def _choose_db(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose mixxxdb.sqlite", str(Path.home()),
                                              "Mixxx library (mixxxdb.sqlite);;SQLite (*.sqlite)")
        if path:
            self.ctrl.refresh_library(path)
            self.ctrl.config.save()

    def _settings(self) -> None:
        if SettingsDialog(self.ctrl.settings, self).exec():
            self.ctrl.settings_changed()

    def _legend(self) -> None:
        moves = self.ctrl.settings.moves()
        lines = [
            f"<tr><td><b>{m.name}</b></td><td>{m.label}</td><td>{TIER_NAMES[m.tier]}</td>"
            f"<td align=right>{'—' if m.energy is None else f'{m.energy:+d}'}</td></tr>"
            for m in moves.values()
        ]
        b = self.ctrl.settings.bpm
        unit = "%" if b.percent else " BPM"
        QMessageBox.information(
            self, "Move legend",
            "<table cellspacing=6><tr><th align=left>Move</th><th align=left>Mood</th><th align=left>Tier</th>"
            "<th>Energy Δ</th></tr>" + "".join(lines) + "</table>"
            f"<p>BPM: {BAND_NAMES['safe']} ≤ {b.safe:g}{unit}, {BAND_NAMES['caution']} ≤ {b.caution:g}{unit}, "
            f"{BAND_NAMES['danger']} above.</p><p>Red rows clash with the track before them; amber rows are "
            "duplicates. Dashed rows are transitions still to fill.</p>",
        )

    # -------------------------------------------------------------- layout
    def save_layout(self) -> None:
        enc = lambda b: bytes(b.toBase64()).decode()  # noqa: E731
        self.ctrl.config.set("ui", {
            "geometry": enc(self.saveGeometry()),
            "top": enc(self.top_split.saveState()),
            "main": enc(self.main_split.saveState()),
            "left": enc(self.left_split.saveState()),
            "set_cols": self.set_view.state(),
            "pool_cols": self.pool_view.state(),
            "track_cols": self.track_view.state(),
            "pool_open": self.pool_toggle.isChecked(),
            "preview_volume": self.preview.volume.value() / 100,
        })
        self.ctrl.config.save()

    def restore_layout(self) -> None:
        ui = self.ctrl.config.get("ui", {}) or {}
        dec = lambda s: QByteArray.fromBase64(s.encode())  # noqa: E731
        if ui.get("geometry"):
            self.restoreGeometry(dec(ui["geometry"]))
        for key, split in (("top", self.top_split), ("main", self.main_split), ("left", self.left_split)):
            if ui.get(key):
                split.restoreState(dec(ui[key]))
        self.set_view.restore(ui.get("set_cols"))
        self.pool_view.restore(ui.get("pool_cols"))
        self.track_view.restore(ui.get("track_cols"))
        self.pool_toggle.setChecked(ui.get("pool_open", True))

    def closeEvent(self, ev) -> None:
        self.ctrl.save_now()
        self.save_layout()
        super().closeEvent(ev)


class PlacementDialog(QDialog):
    def __init__(self, win: MainWindow, pool_uid: str, gaps):
        super().__init__(win)
        self.win, self.uid = win, pool_uid
        self.setWindowTitle("Where to place it")
        self.resize(560, 320)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Gaps where this track mixes in key on both sides (best first). "
                             "Select one to highlight it in the set."))
        self.list = QListWidget()
        reals = win.ctrl.real_tracks()
        for g in gaps:
            before = f"{g.gap}. {reals[g.gap - 1].title}" if g.gap > 0 and g.gap - 1 < len(reals) else "the start"
            after = f"{g.gap + 1}. {reals[g.gap].title}" if g.gap < len(reals) else "the end"
            band = f", BPM {BAND_NAMES[g.worst_band]}" if g.worst_band else ""
            it = QListWidgetItem(f"Between {before} and {after} — {TIER_NAMES[g.worst_tier]}{band}")
            it.setData(Qt.UserRole, g.gap)
            self.list.addItem(it)
        self.list.currentItemChanged.connect(lambda it, _p: it and win.highlight_gap(it.data(Qt.UserRole)))
        self.list.itemDoubleClicked.connect(lambda _it: self._insert())
        lay.addWidget(self.list)
        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        ins = bb.addButton("Insert here", QDialogButtonBox.AcceptRole)
        ins.clicked.connect(self._insert)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.list.setCurrentRow(0)

    def _insert(self):
        it = self.list.currentItem()
        if it:
            self.win.ctrl.insert_pool_item(self.uid, it.data(Qt.UserRole))
            self.accept()
