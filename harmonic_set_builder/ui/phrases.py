"""Phrases view: analyze track structure (intro / build / drop / breakdown / outro),
see each track's energy curve, get transition points for the set, and optionally
export sections to Mixxx as hot cues."""
from __future__ import annotations

import html
import os
from pathlib import Path
from concurrent.futures import Future
from typing import Optional

import pyqtgraph as pg
from PySide6.QtCore import QSortFilterProxyModel, Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton, QSpinBox, QSplitter, QTableView,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ..analysis import batch, methods, transfer, transitions
from ..analysis.labels import BREAKDOWN, BUILD, DROP, GROOVE, INTRO, OUTRO, summary
from ..analysis.store import TrackAnalysis
from ..analysis.structure import ALLIN1, BUILTIN, allin1_available
from ..core.camelot import format_key
from ..core.track import Track
from ..data import mixxx_cues, mixxx_db
from ..data.paths import default_allin1_python
from . import theme
from .file_dialogs import open_file
from .sources import SET_KEY, SourcesTree

SECTION_COLORS = {
    INTRO: QColor("#32be44"), BUILD: QColor("#f8d200"), DROP: QColor("#e04040"),
    BREAKDOWN: QColor("#3b6cff"), GROOVE: QColor("#af5ccc"), OUTRO: QColor("#42d4f4"),
}
# Raw model labels coloured like the DJ label they usually mean.
RAW_COLORS = {"intro": INTRO, "outro": OUTRO, "chorus": DROP, "break": BREAKDOWN, "bridge": BREAKDOWN,
              "verse": GROOVE, "inst": GROOVE, "solo": GROOVE}
RAVEFORM_PALETTE = ["#8a8a8a", "#8a8a8a", "#32be44", "#42d4f4", "#e8a33a", "#b45cd6", "#3b6cff", "#e04040",
                    "#f8d200", "#6fd0a0", "#d06f9a"]


def section_color(label: str) -> QColor:
    if label in SECTION_COLORS:
        return SECTION_COLORS[label]
    base = label.split(" ")[0]
    if base in RAW_COLORS:
        return SECTION_COLORS[RAW_COLORS[base]]
    if base.startswith("r") and base[1:].isdigit():
        return QColor(RAVEFORM_PALETTE[int(base[1:]) % len(RAVEFORM_PALETTE)])
    return QColor(theme.DIM)


COLS = ["#", "Artist", "Title", "Genre", "BPM", "Key", "Duration", "Structure", "Status"]
C_POS, C_STRUCT, C_STATUS = 0, 7, 8
STATUS_FILTERS = (("all", "All tracks"), ("new", "Not analyzed"), ("analyzed", "Analyzed"),
                  ("changed", "Changed / outdated"), ("failed", "Failed"))
PATH_ROLE = Qt.UserRole + 1
SORT_ROLE = Qt.UserRole + 2


class TrackFilter(QSortFilterProxyModel):
    """Search text (any column) plus an analysis-status filter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status = "all"
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(-1)

    def set_status(self, status: str) -> None:
        if hasattr(self, "beginFilterChange"):  # Qt 6.10+
            self.beginFilterChange()
            self.status = status
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:
            self.status = status
            self.invalidateFilter()

    def filterAcceptsRow(self, row, parent) -> bool:
        if self.status != "all":
            st = self.sourceModel().index(row, C_STATUS, parent).data() or ""
            ok = {"new": st in ("", "queued"), "analyzed": st == "analyzed", "changed": st in ("file changed", "outdated labels"),
                  "failed": st.startswith("failed")}[self.status]
            if not ok:
                return False
        return super().filterAcceptsRow(row, parent)


def fmt_time(sec: float) -> str:
    sec = max(sec, 0)
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


class TimeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        return [f"{int(v // 60)}:{int(v % 60):02d}" for v in values]


# ------------------------------------------------------------------ runner
class AnalysisRunner(QObject):
    """Runs analysis jobs in worker processes and reports back on the GUI thread."""

    progress = Signal(int, int)  # done, total
    trackDone = Signal(str, object, str)  # path, TrackAnalysis | None, error
    finished = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.ex = None
        self.futures: dict[Future, str] = {}
        self.total = self.done = 0
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)

    @property
    def running(self) -> bool:
        return self.ex is not None

    def start(self, jobs, workers: int) -> None:
        self.ex = batch.executor(max(1, min(workers, len(jobs))))
        self.futures = {self.ex.submit(batch.pipeline_analyze, j): j.path for j in jobs}
        self.total, self.done = len(jobs), 0
        self.progress.emit(0, self.total)
        self.timer.start()

    def _poll(self) -> None:
        for fut in [f for f in self.futures if f.done()]:
            path = self.futures.pop(fut)
            if fut.cancelled():
                continue
            self.done += 1
            try:
                a = batch.store_result(self.store, fut.result())
                self.trackDone.emit(path, a, "")
            except Exception as e:  # noqa: BLE001 - one bad file must not stop the batch
                self.trackDone.emit(path, None, str(e) or type(e).__name__)
            self.progress.emit(self.done, self.total)
        if not self.futures:
            self._finish()

    def cancel(self) -> None:
        for f in self.futures:
            f.cancel()
        self.futures = {f: p for f, p in self.futures.items() if not f.cancelled()}
        if not self.futures:
            self._finish()

    def _finish(self) -> None:
        self.timer.stop()
        if self.ex:
            self.ex.shutdown(wait=False, cancel_futures=True)
        self.ex = None
        self.finished.emit()


# ------------------------------------------------------------- the plot
class StructurePlot(pg.PlotWidget):
    seek = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent, background=theme.BASE, axisItems={"bottom": TimeAxis("bottom")})
        self.setMenuEnabled(False)
        self.setMouseEnabled(x=True, y=False)
        self.hideButtons()
        pi = self.getPlotItem()
        pi.setLabel("left", "Energy")
        for ax in ("left", "bottom"):
            pi.getAxis(ax).setTextPen(theme.DIM)
            pi.getAxis(ax).setPen(theme.DIM)
        pi.setYRange(0, 1.18, padding=0)
        self.playhead = pg.InfiniteLine(angle=90, pen=pg.mkPen(theme.ACCENT, width=2))
        self.playhead.hide()
        pi.addItem(self.playhead, ignoreBounds=True)
        self._items = []
        self._cue_items = []
        self.scene().sigMouseClicked.connect(self._clicked)

    def show_analysis(self, a: Optional[TrackAnalysis], sections=None, points=()) -> None:
        """Energy curve of ``a`` with ``sections`` (default: its own) shaded and ``points`` marked."""
        pi = self.getPlotItem()
        for it in self._items:
            pi.removeItem(it)
        self._items = []
        for t in points:
            line = pg.InfiniteLine(t, angle=90, pen=pg.mkPen(QColor("#5ad0e6"), width=2))
            line.setToolTip(f"CUE-DETR cue at {fmt_time(t)}")
            pi.addItem(line, ignoreBounds=True)
            self._items.append(line)
        for s in (a.sections if sections is None and a else sections or []):
            col = section_color(s.label)
            c = QColor(col)
            c.setAlpha(55)
            region = pg.LinearRegionItem((s.start_sec, s.end_sec), movable=False, brush=pg.mkBrush(c),
                                         pen=pg.mkPen(col, width=1))
            region.setZValue(-10)
            pi.addItem(region)
            label = pg.TextItem(f"{s.name}\n{s.bars} bars" if s.bars else s.name, color=col, anchor=(0, 0))
            label.setPos(s.start_sec, 1.17)
            pi.addItem(label)
            self._items += [region, label]
        if a is None or not a.bars:
            return
        xs = [b["start_sec"] for b in a.bars] + [a.duration]
        ys = [b["energy"] for b in a.bars]
        low = [b.get("low") or 0 for b in a.bars]
        lo = pg.PlotDataItem(xs, low, stepMode="center", pen=pg.mkPen(QColor("#6a6a8a"), width=1, style=Qt.DashLine))
        en = pg.PlotDataItem(xs, ys, stepMode="center", pen=pg.mkPen(theme.ACCENT, width=2),
                             fillLevel=0, brush=pg.mkBrush(224, 140, 26, 50))
        for it in (lo, en):
            pi.addItem(it)
            self._items.append(it)
        pi.setXRange(0, a.duration, padding=0.01)

    def show_cues(self, cues: list[dict], duration: float = 0.0) -> None:
        """Your hot cues from Mixxx as dashed lines numbered like Mixxx's pads, to compare with the sections."""
        pi = self.getPlotItem()
        for it in self._cue_items:
            pi.removeItem(it)
        self._cue_items = []
        for c in cues:
            num = c["hotcue"] + 1
            text = f"{num} {c['label']}" if c["label"] else str(num)
            colour = QColor((c.get("color") or 0xFFFFFF) & 0xFFFFFF) if c.get("color") else QColor("#ffffff")
            line = pg.InfiniteLine(c["start"], angle=90, pen=pg.mkPen(colour, width=1.5, style=Qt.DashLine),
                                   label=text, labelOpts={"position": 0.06, "color": colour, "anchors": [(0, 1), (0, 1)]})
            line.setToolTip(f"Your hot cue {text} at {fmt_time(c['start'])}")
            pi.addItem(line, ignoreBounds=True)
            self._cue_items.append(line)
        if cues and not self._items and duration:
            pi.setXRange(0, duration, padding=0.01)

    def set_playhead(self, sec: Optional[float]) -> None:
        if sec is None:
            self.playhead.hide()
        else:
            self.playhead.setPos(sec)
            self.playhead.show()

    def _clicked(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        vb = self.getPlotItem().vb
        if vb.sceneBoundingRect().contains(ev.scenePos()):
            self.seek.emit(vb.mapSceneToView(ev.scenePos()).x())


class FlowPlot(pg.PlotWidget):
    """Every analyzed set track's energy curve, laid along the set at its mix points."""

    def __init__(self, parent=None):
        super().__init__(parent, background=theme.BASE, axisItems={"bottom": TimeAxis("bottom")})
        self.setMenuEnabled(False)
        self.setMouseEnabled(x=True, y=False)
        self.hideButtons()
        pi = self.getPlotItem()
        pi.setLabel("left", "Energy")
        pi.setLabel("bottom", "Time in set")
        for ax in ("left", "bottom"):
            pi.getAxis(ax).setTextPen(theme.DIM)
            pi.getAxis(ax).setPen(theme.DIM)

    def show_flow(self, flow: list[transitions.FlowTrack], names: list[str]) -> None:
        pi = self.getPlotItem()
        pi.clear()
        palette = [theme.ACCENT, QColor("#5aa0e6")]
        for k, f in enumerate(flow):
            c = palette[k % 2]
            fill = QColor(c)
            fill.setAlpha(45)
            pi.addItem(pg.PlotDataItem(f.times, f.energy, pen=pg.mkPen(c, width=2), fillLevel=0, brush=fill))
            t = pg.TextItem(f"{f.index + 1}. {names[f.index]}", color=c, anchor=(0, 1))
            t.setPos(f.offset, 1.27 - 0.1 * (k % 2))
            pi.addItem(t)
            if f.mix_in is not None:
                pi.addItem(pg.InfiniteLine(f.mix_in, angle=90, pen=pg.mkPen(theme.DIM, style=Qt.DashLine)))
        pi.setYRange(0, 1.3, padding=0)


# ------------------------------------------------------------- the view
class PhrasesView(QWidget):
    def __init__(self, ctrl, preview, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.preview = preview
        self.store = ctrl.analysis_store
        self.runner = AnalysisRunner(self.store, self)
        self.runner.progress.connect(self._progress)
        self.runner.trackDone.connect(self._track_done)
        self.runner.finished.connect(self._run_finished)
        self.tracks: list[Track] = []
        self.status: dict[str, str] = {}  # path -> queued / analyzing / failed: …
        self.current: Optional[Track] = None
        self.analysis: Optional[TrackAnalysis] = None
        self._dirty = True
        self._errors: list[str] = []

        cfg = ctrl.config.get("phrases", {}) or {}
        self.focus_key = tuple(cfg.get("source") or SET_KEY)
        self.sources = SourcesTree(set_item=True)
        self.sources.setToolTip("Counts: tracks · analyzed (✓)")
        self.sources.focused.connect(self._source_focused)
        self.sources.openAsSet.connect(ctrl.import_collection)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search artist, title, genre…")
        self.search.setClearButtonEnabled(True)
        self.status_filter = QComboBox()
        for key, label in STATUS_FILTERS:
            self.status_filter.addItem(label, key)
        self.status_filter.setToolTip("Show tracks by analysis status")
        self.count = QLabel()
        self.count.setObjectName("hint")
        self.backend = QComboBox()
        self.backend.addItem("Built-in analyzer", BUILTIN)
        self.backend.addItem("allin1 (slow, ML)", ALLIN1)
        self.backend.setToolTip("Built-in: fast, tuned for dance music.\nallin1: All-In-One Music Structure "
                                "Analyzer, a neural network; a few minutes per track on CPU.")
        self.backend.setCurrentIndex(max(0, self.backend.findData(ctrl.settings.analysis_backend)))
        self.backend.currentIndexChanged.connect(self._backend_changed)
        self.analyze_btn = QPushButton("Analyze selected")
        self.analyze_btn.clicked.connect(lambda: self.analyze(self._selected_tracks()))
        self.analyze_all_btn = QPushButton("Analyze all new")
        self.analyze_all_btn.setToolTip("Every listed track that hasn't been analyzed (or whose file changed)")
        self.analyze_all_btn.clicked.connect(lambda: self.analyze(self.tracks))
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setToolTip("Cancel queued tracks (the ones already running finish)")
        self.stop_btn.clicked.connect(self.runner.cancel)
        self.stop_btn.setEnabled(False)
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(180)
        self.bar.setTextVisible(True)
        self.bar.hide()
        self.export_btn = QPushButton("Export cues to Mixxx…")
        self.export_btn.setToolTip("Write sections into Mixxx as hot cues and intro/outro markers "
                                   "(dry run first; Mixxx must be closed)")
        self.export_btn.clicked.connect(self._export)

        self.import_btn = QPushButton("Import…")
        self.import_btn.setToolTip("Load analysis results made on another computer (hsb-analysis.json.gz)")
        self.import_btn.clicked.connect(self._import)
        top = QHBoxLayout()
        top.addWidget(self.backend)
        top.addWidget(self.analyze_btn)
        top.addWidget(self.analyze_all_btn)
        top.addWidget(self.stop_btn)
        top.addWidget(self.bar)
        top.addStretch(1)
        top.addWidget(self.import_btn)
        top.addWidget(self.export_btn)

        self.model = QStandardItemModel(0, len(COLS))
        self.model.setHorizontalHeaderLabels(COLS)
        self.proxy = TrackFilter()
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(SORT_ROLE)
        self.search.textChanged.connect(lambda t: (self.proxy.setFilterFixedString(t), self._update_count()))
        self.status_filter.currentIndexChanged.connect(
            lambda _i: (self.proxy.set_status(self.status_filter.currentData()), self._update_count()))
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setAlternatingRowColors(True)
        h = self.table.horizontalHeader()
        for i, w in enumerate((32, 120, 170, 80, 44, 36, 48, 140, 85)):
            h.resizeSection(i, w)
        h.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(self._header_menu)
        for i in cfg.get("hidden_cols", []):
            if 0 < i < len(COLS):
                self.table.setColumnHidden(i, True)
        self.table.selectionModel().currentRowChanged.connect(lambda cur, _p: self._row_changed(cur))
        self.table.doubleClicked.connect(lambda idx: self.preview.preview(self._track_at(idx)))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)

        self.method = QComboBox()
        self.method.setToolTip("Compare analyzers on this track: the saved analysis, each model's raw output, "
                               "each model through the app's rules, and CUE-DETR's cue points")
        self.method.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.method.setMinimumContentsLength(28)
        self.method.currentIndexChanged.connect(lambda _i: self._show_method())
        self.method_note = QLabel()
        self.method_note.setObjectName("hint")
        self.view_sections: list = []
        self.title = QLabel("Pick a track")
        f = self.title.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.2)
        self.title.setFont(f)
        self.info = QLabel()
        self.info.setObjectName("hint")
        self.plot = StructurePlot()
        self.plot.setMinimumHeight(170)
        self.plot.seek.connect(self._seek)
        legend = QLabel("  ".join(f"<span style='color:{c.name()}'>■</span> {lab}" for lab, c in SECTION_COLORS.items())
                        + "   <span style='color:#e08c1a'>━</span> energy   "
                          "<span style='color:#6a6a8a'>┅</span> kick/bass   ┆ your Mixxx hot cues"
                          "   · click to play from there")
        legend.setObjectName("hint")

        self.sections = QTableWidget(0, 6)
        self.sections.setHorizontalHeaderLabels(["Section", "Starts", "Bar", "Bars", "Energy", "Source"])
        self.sections.verticalHeader().hide()
        self.sections.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sections.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sections.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sections.cellDoubleClicked.connect(self._section_activated)
        self.sections.setToolTip("Double-click to play from the start of a section")
        self.trans = QTextBrowser()
        self.trans.setOpenLinks(False)
        self.flow = FlowPlot()
        self.flow_hint = QLabel()
        self.flow_hint.setObjectName("hint")
        flow_box = QWidget()
        fv = QVBoxLayout(flow_box)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.addWidget(self.flow_hint)
        fv.addWidget(self.flow, 1)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.sections, "Sections")
        self.tabs.addTab(self.trans, "Transitions")
        self.tabs.addTab(flow_box, "Set energy flow")
        self.tabs.currentChanged.connect(lambda _i: self._update_tabs())

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(self.title, 1)
        head.addWidget(QLabel("Show:"))
        head.addWidget(self.method)
        rv.addLayout(head)
        rv.addWidget(self.info)
        rv.addWidget(self.method_note)
        rv.addWidget(self.plot, 2)
        rv.addWidget(legend)
        rv.addWidget(self.tabs, 2)

        lib_box = QWidget()
        lb = QVBoxLayout(lib_box)
        lb.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.addWidget(self.search, 1)
        bar.addWidget(self.status_filter)
        bar.addWidget(self.count)
        lb.addLayout(bar)
        lb.addWidget(self.table, 1)

        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(self.sources)
        self.split.addWidget(lib_box)
        self.split.addWidget(right)
        self.split.setStretchFactor(1, 1)
        self.split.setStretchFactor(2, 1)
        self.split.setSizes([200, 660, 640])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 0)
        lay.addLayout(top)
        lay.addWidget(self.split, 1)

        preview.player.positionChanged.connect(self._playhead)
        for sig in (ctrl.libraryChanged, ctrl.setChanged):
            sig.connect(self._mark_dirty)
        ctrl.libraryChanged.connect(self._populate_sources)
        ctrl.setChanged.connect(self._set_changed)
        self._sources_dirty = False

    # -------------------------------------------------------------- list
    def _mark_dirty(self) -> None:
        self._dirty = True
        if self.isVisible():
            QTimer.singleShot(0, self.refresh)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if self._dirty:
            self.refresh()

    # ----------------------------------------------------------- sources
    def _populate_sources(self) -> None:
        lib = self.ctrl.library
        s = self.ctrl.settings
        analyzed = set(self.store.index())
        loc = {t.id: t.location for t in lib.all_tracks}

        def count(ids) -> str:
            done = sum(1 for i in ids if loc.get(i) in analyzed)
            return f"{len(ids)} · {done} ✓" if done else str(len(ids))

        set_ids = [t.id for t in self.ctrl.real_tracks()]
        self.sources.populate(lib, s.show_history_playlists, s.show_autodj_playlist, self.focus_key, count, set_ids)
        self.focus_key = self.sources.focus_key()  # falls back to the library if the crate is gone

    def _set_changed(self) -> None:
        """The "Current set" count changed; rebuild the tree when the view is next refreshed."""
        self._sources_dirty = True

    def _source_focused(self, _src) -> None:
        self.focus_key = self.sources.focus_key()
        self._save_cfg()
        self._dirty = True
        self.refresh()

    def set_source(self, key: tuple) -> None:
        """Focus "Current set" (SET_KEY), the library (LIBRARY_KEY) or a (kind, id) collection."""
        self.focus_key = tuple(key)
        self._populate_sources()
        self._dirty = True
        self.refresh()

    def _source_tracks(self) -> list[Track]:
        key = self.focus_key
        if key == SET_KEY:
            return self.ctrl.real_tracks()
        c = self.sources.collection(key)
        if c is not None:
            return self.ctrl.library.collection_tracks(c)
        return sorted(self.ctrl.library.all_tracks, key=lambda t: (t.artist.casefold(), t.title.casefold()))

    def _header_menu(self, pos) -> None:
        m = QMenu(self)
        for i, name in enumerate(COLS):
            if i == C_POS:
                continue
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(not self.table.isColumnHidden(i))
            a.toggled.connect(lambda on, i=i: (self.table.setColumnHidden(i, not on), self._save_cfg()))
        m.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _update_count(self) -> None:
        shown, total = self.proxy.rowCount(), self.model.rowCount()
        done = sum(1 for r in range(total) if self.model.item(r, C_STRUCT).text())
        self.count.setText((f"{shown} of {total}" if shown != total else f"{total} tracks") + f" · {done} analyzed")

    def refresh(self) -> None:
        if not self._dirty and self.model.rowCount():
            return
        self._dirty = False
        if self._sources_dirty:
            self._sources_dirty = False
            self._populate_sources()
        keep = self.current.location if self.current else None
        self.tracks = self._source_tracks()
        index = self.store.index()
        notation = self.ctrl.settings.key_notation
        self.model.setRowCount(0)
        is_set = self.focus_key == SET_KEY
        for i, t in enumerate(self.tracks):
            row = index.get(t.location)
            st = self.status.get(t.location) or ("analyzed" if row else "")
            if row and not self.status.get(t.location):
                st = {"changed": "file changed", "outdated": "outdated labels"}.get(
                    self.store.status(t.location, row.analyzer.split("==")[0]), st)
            dur = f"{int(t.duration // 60)}:{int(t.duration % 60):02d}" if t.duration else ""
            vals = [str(i + 1) if is_set else "", t.artist, t.title, t.genre, f"{t.bpm:.1f}" if t.bpm else "?",
                    format_key(t.key, notation), dur, row.summary if row else "", st]
            sorts = [i, t.artist.casefold(), t.title.casefold(), t.genre.casefold(), t.bpm or 0,
                     (t.key.mode, t.key.number) if t.key else ("Z", 99), t.duration or 0,
                     row.summary if row else "~", st]
            items = []
            for v, srt in zip(vals, sorts):
                it = QStandardItem(v)
                it.setData(t.location, PATH_ROLE)
                it.setData(srt, SORT_ROLE)
                items.append(it)
            self._style_status(items[-1], st)
            items[C_STRUCT].setToolTip("I Intro · B Build · D Drop · Br Breakdown · G Groove · O Outro (bars)")
            self.model.appendRow(items)
        self.table.setColumnHidden(C_POS, not is_set)
        self._update_count()
        if keep:
            self._select_path(keep)
        elif self.tracks and not self.table.currentIndex().isValid():
            self.table.selectRow(0)
        self._update_tabs()

    def _style_status(self, it: QStandardItem, st: str) -> None:
        colors = {"analyzed": theme.BAND_COLORS["safe"], "analyzing…": theme.ACCENT, "queued": theme.DIM,
                  "file changed": theme.BAND_COLORS["caution"], "outdated labels": theme.BAND_COLORS["caution"]}
        if st == "outdated labels":
            it.setToolTip("Labeled by older rules: “Analyze all new” re-labels it in seconds from the saved model output")
        if st.startswith("failed"):
            it.setForeground(theme.CLASH)
            it.setToolTip(st)
        elif st in colors:
            it.setForeground(colors[st])

    def _select_path(self, path: str) -> None:
        for r in range(self.proxy.rowCount()):
            idx = self.proxy.index(r, 0)
            if idx.data(PATH_ROLE) == path:
                self.table.setCurrentIndex(idx)
                self.table.scrollTo(idx)
                return

    def _track_at(self, idx) -> Optional[Track]:
        path = idx.data(PATH_ROLE) if idx.isValid() else None
        return next((t for t in self.tracks if t.location == path), None)

    def _selected_tracks(self) -> list[Track]:
        paths = {i.data(PATH_ROLE) for i in self.table.selectionModel().selectedRows()}
        return [t for t in self.tracks if t.location in paths]

    def _row_changed(self, idx) -> None:
        t = self._track_at(idx)
        self.current = t
        self.analysis = self.store.get(t.location) if t else None
        self._show_current()

    def _show_current(self) -> None:
        t, a = self.current, self.analysis
        if t is None:
            self.title.setText("Pick a track")
            self.info.clear()
            self.plot.show_analysis(None)
            self.plot.show_cues([])
            self._update_tabs()
            return
        self.title.setText(f"{t.artist} – {t.title}" if t.artist else t.title)
        if a:
            grid = {"mixxx": "Mixxx's beat grid", "allin1": "allin1's beats", "detected": "detected beats"}.get(a.grid, a.grid)
            self.info.setText(f"{summary(a.sections)}   ·   {a.analyzer}, bars from {grid}, {a.bpm:g} BPM   ·   "
                              f"analyzed {a.analyzed_at.replace('T', ' ')}")
        else:
            st = self.status.get(t.location, "")
            self.info.setText(st if st else "Not analyzed yet: press “Analyze selected”.")
        keep = self.method.currentData()
        self.method.blockSignals(True)
        self.method.clear()
        for key, title in methods.available(set(self.store.raw_backends(t.location)), a is not None):
            self.method.addItem(title, key)
        self.method.setCurrentIndex(max(0, self.method.findData(keep)))
        self.method.setEnabled(self.method.count() > 1)
        self.method.blockSignals(False)
        self._show_method()
        self._update_tabs()

    def _show_method(self) -> None:
        """Draw the selected analyzer's prediction for the current track (plus your hot cues)."""
        t, a = self.current, self.analysis
        if t is None:
            return
        key = self.method.currentData() or methods.SAVED
        raw = None
        if key != methods.SAVED:
            backend = key.split(":")[0]
            raw = self.store.raw_get(t.location, backend, self.store.raw_backends(t.location).get(backend, ""))
        view = methods.build(key, a, raw)
        self.view_sections = view.sections
        self.method_note.setText(view.note)
        self.method_note.setVisible(bool(view.note) and key != methods.SAVED)
        self.plot.show_analysis(a, view.sections, view.points)
        self.plot.show_cues(self._hot_cues(t), (a.duration if a else 0.0) or t.duration)
        self._playhead(self.preview.player.position())
        rows = [(s.name, s.start_sec, str(s.start_bar + 1), str(s.bars), f"{s.mean_energy:.2f}", s.source, s.label)
                for s in view.sections] + \
               [(f"Cue {i + 1}", p, "", "", "", "CUE-DETR", "") for i, p in enumerate(view.points)]
        self.view_starts = [r[1] for r in rows]
        self.sections.setRowCount(len(rows))
        for r, (name, start, bar, bars, energy, src, label) in enumerate(rows):
            for c, v in enumerate((name, fmt_time(start), bar, bars, energy, src)):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setForeground(section_color(label) if label else QColor("#5ad0e6"))
                self.sections.setItem(r, c, it)

    def _hot_cues(self, t: Track) -> list[dict]:
        """The track's hot cues as of the last library snapshot (never read from Mixxx's live DB)."""
        snap = self.ctrl.snapshot_path
        if t.id < 0 or not snap.is_file():
            return []
        try:
            cues = mixxx_db.load_cues(snap, [t.id]).get(t.id, [])
        except Exception:  # noqa: BLE001 - an odd snapshot just means no overlay
            return []
        return sorted((c for c in cues if c["type"] == mixxx_cues.HOTCUE and c["start"] is not None),
                      key=lambda c: c["start"])

    # --------------------------------------------------- transitions/flow
    def _set_neighbours(self) -> tuple[Optional[Track], Optional[Track]]:
        if self.current is None:
            return None, None
        set_tracks = self.ctrl.real_tracks()
        idx = next((i for i, t in enumerate(set_tracks) if t.location == self.current.location), None)
        if idx is None:
            return None, None
        prev = set_tracks[idx - 1] if idx > 0 else None
        nxt = set_tracks[idx + 1] if idx + 1 < len(set_tracks) else None
        return prev, nxt

    def _update_tabs(self) -> None:
        i = self.tabs.currentIndex()
        if i == 1:
            self._show_transitions()
        elif i == 2:
            self._show_flow()

    def _pair_html(self, a: Track, b: Track) -> str:
        aa, bb = self.store.get(a.location), self.store.get(b.location)
        head = f"<h3 style='margin-bottom:2px'>A: {_h(a.display)}<br>→ B: {_h(b.display)}</h3>"
        missing = [t.display for t, x in ((a, aa), (b, bb)) if x is None]
        if missing:
            return head + f"<p style='color:{theme.DIM.name()}'>Analyze {_h(' and '.join(missing))} to get transition " \
                          "points.</p>"
        tips, flags = transitions.suggest(aa, bb)
        out = head
        for t in tips:
            out += (f"<p>▶ {_h(t.text)}<br><span style='color:{theme.DIM.name()}'>A at {fmt_time(t.a_sec)} "
                    f"(bar {t.a_bar + 1})</span></p>")
        for f in flags:
            col = theme.BAND_COLORS["caution"].name() if f.severity == "warn" else theme.DIM.name()
            out += f"<p style='color:{col}'>⚠ {_h(f.text)}</p>" if f.severity == "warn" else \
                f"<p style='color:{col}'>ℹ {_h(f.text)}</p>"
        if not tips and not flags:
            out += "<p>No specific suggestion: mix on a phrase boundary.</p>"
        return out

    def _show_transitions(self) -> None:
        if self.current is None:
            self.trans.setHtml("<p>Pick a track.</p>")
            return
        prev, nxt = self._set_neighbours()
        if prev is None and nxt is None:
            self.trans.setHtml("<p>Transition points use the order of the current set. This track isn't in the set "
                               "(or is its only track).</p>")
            return
        parts = []
        if nxt:
            parts.append("<h4 style='color:#e08c1a'>Into the next track</h4>" + self._pair_html(self.current, nxt))
        if prev:
            parts.append("<h4 style='color:#e08c1a'>From the previous track</h4>" + self._pair_html(prev, self.current))
        self.trans.setHtml("<hr>".join(parts) + f"<p style='color:{theme.DIM.name()}'>Bars count from each track's "
                           "first downbeat, with the tempos matched.</p>")

    def _show_flow(self) -> None:
        tracks = self.ctrl.real_tracks()
        analyses = [self.store.get(t.location) for t in tracks]
        n = sum(1 for a in analyses if a)
        flow = transitions.set_flow(analyses, self.ctrl.settings.mix_overlap_bars or 16)
        self.flow.show_flow(flow, [t.title for t in tracks])
        self.flow_hint.setText(
            f"The current set's energy, track by track, overlapping at each first suggested transition point "
            f"(dashed). {n} of {len(tracks)} tracks analyzed." if tracks else "The current set is empty.")

    # ------------------------------------------------------------- audio
    def _seek(self, sec: float) -> None:
        if self.current:
            self.preview.preview_at(self.current, max(0.0, sec))

    def _section_activated(self, row: int, _col: int) -> None:
        starts = getattr(self, "view_starts", [])
        if self.current and 0 <= row < len(starts):
            self.preview.preview_at(self.current, starts[row])

    def _import(self) -> None:
        start = "/run/media" if os.path.isdir("/run/media") else os.path.expanduser("~")
        path = open_file(self, "Import analysis results", start, "Analysis results (*.json.gz)")
        if not path:
            return
        try:
            r = transfer.import_file(self.store, Path(path))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import failed", str(e))
            return
        self.ctrl.refresh_structure_index()
        self._populate_sources()
        self._dirty = True
        self.refresh()
        if self.current:
            self._row_changed(self.table.currentIndex())
        msg = f"Imported {r.imported} track(s)."
        if r.kept_newer:
            msg += f" Kept {r.kept_newer} newer result(s) already here."
        if r.size_mismatch:
            msg += f"\n\n{len(r.size_mismatch)} skipped: the file here differs from the one analyzed."
        QMessageBox.information(self, "Import", msg)

    def _playhead(self, ms: int) -> None:
        t = self.preview.track
        on = t is not None and self.current is not None and t.location == self.current.location
        self.plot.set_playhead(ms / 1000 if on else None)

    def _table_menu(self, pos) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            return
        m = QMenu(self)
        m.addAction("Preview", lambda: self.preview.preview(tracks[0]))
        m.addAction("Analyze", lambda: self.analyze(tracks))
        m.addAction("Re-label (fast: reuses the saved model output)", lambda: self.analyze(tracks, relabel=True))
        m.addAction(f"Re-analyze from scratch ({self.backend.currentText()})", lambda: self.analyze(tracks, force=True))
        analyzed = [t for t in tracks if self.store.get(t.location)]
        if analyzed:
            m.addAction("Forget analysis", lambda: self._forget(analyzed))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _forget(self, tracks: list[Track]) -> None:
        for t in tracks:
            self.store.delete(t.location)
        self.ctrl.refresh_structure_index()
        self._populate_sources()
        self._dirty = True
        self.refresh()
        self._row_changed(self.table.currentIndex())

    # ----------------------------------------------------------- analyze
    def _backend_changed(self) -> None:
        self.ctrl.settings.analysis_backend = self.backend.currentData()
        self.ctrl.config.save()

    def allin1_python(self) -> str:
        return self.ctrl.settings.allin1_python or str(default_allin1_python(self.ctrl.data_dir))

    def analyze(self, tracks: list[Track], force: bool = False, relabel: bool = False) -> None:
        if self.runner.running:
            QMessageBox.information(self, "Analysis running", "Wait for the current analysis to finish, or press Stop.")
            return
        if not tracks:
            return
        backend = self.backend.currentData()
        if backend == ALLIN1 and not relabel:
            ok, msg = allin1_available(self.allin1_python())
            if not ok:
                QMessageBox.warning(self, "allin1 not available",
                                    f"{msg}\n\nSet its Python in Settings → Phrase analysis, or use the built-in analyzer.")
                return
        if mixxx_cues.mixxx_running():
            if QMessageBox.question(
                    self, "Mixxx is running",
                    "Analysis is CPU-heavy and could cause audio dropouts in Mixxx.\n"
                    "It's meant to run while Mixxx is closed. Analyze anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        grids = mixxx_db.load_grids(self.ctrl.snapshot_path, [t.id for t in tracks]) \
            if self.ctrl.snapshot_path.is_file() else {}
        jobs, skipped = batch.make_jobs(tracks, grids, self.store, backend, self.allin1_python(), force, relabel)
        missing = [t for t, why in skipped if why.startswith("file not found")]
        if not jobs:
            msg = "Everything listed is already analyzed." if not missing else \
                f"{len(missing)} file(s) not found — is the music drive mounted?"
            self.ctrl.message.emit(msg)
            return
        for j in jobs:
            self.status[j.path] = "queued"
        self._errors = []
        workers = self.ctrl.settings.analysis_workers or batch.default_workers(backend)
        self.runner.start(jobs, workers)
        self.stop_btn.setEnabled(True)
        self.analyze_btn.setEnabled(False)
        self.analyze_all_btn.setEnabled(False)
        self.bar.show()
        note = f" ({len(missing)} not found)" if missing else ""
        self.ctrl.message.emit(f"Analyzing {len(jobs)} track(s) with {self.backend.currentText()}{note}…")
        self._dirty = True
        self.refresh()

    def _progress(self, done: int, total: int) -> None:
        self.bar.setMaximum(total)
        self.bar.setValue(done)
        self.bar.setFormat(f"{done} / {total}")

    def _track_done(self, path: str, a, err: str) -> None:
        if a is None:
            self.status[path] = f"failed: {err}"
            self._errors.append(f"{os.path.basename(path)}: {err}")
        else:
            self.status.pop(path, None)
        self._dirty = True
        self.refresh()
        if self.current and self.current.location == path:
            self._row_changed(self.table.currentIndex())

    def _run_finished(self) -> None:
        for p, st in list(self.status.items()):
            if st == "queued":
                self.status.pop(p)
        self.stop_btn.setEnabled(False)
        self.analyze_btn.setEnabled(True)
        self.analyze_all_btn.setEnabled(True)
        self.bar.hide()
        self.ctrl.refresh_structure_index()
        self._populate_sources()
        self._dirty = True
        self.refresh()
        fails = f", {len(self._errors)} failed (hover the status for why)" if self._errors else ""
        self.ctrl.message.emit(f"Analysis finished{fails}.")

    def shutdown(self) -> None:
        if self.runner.running:
            self.runner.cancel()
            if self.runner.ex:
                self.runner.ex.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ export
    def _export(self) -> None:
        tracks = self._selected_tracks()
        if len(tracks) <= 1:
            tracks = self.tracks
        tracks = [t for t in tracks if t.id >= 0 and self.store.get(t.location)]
        if not tracks:
            QMessageBox.information(self, "Export cues", "No analyzed tracks in this list yet.")
            return
        if self.ctrl.db_path is None:
            QMessageBox.warning(self, "Export cues", "Mixxx's library wasn't found.")
            return
        CueExportDialog(self, tracks).exec()

    def _save_cfg(self) -> None:
        self.ctrl.config.set("phrases", {
            "source": list(self.focus_key), "split": self.split.sizes(),
            "hidden_cols": [i for i in range(1, len(COLS)) if self.table.isColumnHidden(i)],
        })

    def restore(self) -> None:
        sizes = (self.ctrl.config.get("phrases", {}) or {}).get("split")
        if sizes and len(sizes) == 3:
            self.split.setSizes(sizes)


def _h(s: str) -> str:
    return html.escape(s)


class CueExportDialog(QDialog):
    """Dry run first; writing needs Mixxx closed and an explicit confirmation."""

    def __init__(self, view: PhrasesView, tracks: list[Track]):
        super().__init__(view)
        self.view, self.tracks = view, tracks
        self.ctrl = view.ctrl
        self.plans: list[mixxx_cues.TrackPlan] = []
        self.setWindowTitle("Export cues to Mixxx")
        self.resize(760, 560)
        s = self.ctrl.settings
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            f"<b>{len(tracks)} analyzed track(s)</b> → Mixxx library <code>{_h(str(self.ctrl.db_path))}</code><br>"
            "Each section start becomes a labeled, colored hot cue (labels start with ◆); the Intro and Outro sections "
            "set Mixxx's intro/outro markers. Your own cues are never changed: hot cues only go into empty slots."))
        form = QFormLayout()
        self.max_hot = QSpinBox(minimum=1, maximum=mixxx_cues.MAX_HOTCUES, value=s.max_hotcues)
        self.max_hot.setToolTip("Hot cue slots this may use (Mixxx shows 8 by default)")
        self.replace = QCheckBox("Replace cues from my earlier exports")
        self.complete = QCheckBox("Fill in a missing intro end / outro start on Mixxx's own markers")
        self.complete.setChecked(s.complete_markers)
        self.complete.setToolTip("Mixxx's analyzer sets only the intro start and outro end; this adds the other "
                                 "end from the detected sections and keeps yours.")
        form.addRow("Use hot cue slots 1 –", self.max_hot)
        form.addRow(self.replace)
        form.addRow(self.complete)
        lay.addLayout(form)
        self.out = QTextBrowser()
        self.out.setLineWrapMode(QTextBrowser.NoWrap)
        f = self.out.font()
        f.setFamily("monospace")
        self.out.setFont(f)
        lay.addWidget(self.out, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        self.plan_btn = bb.addButton("Dry run", QDialogButtonBox.ActionRole)
        self.plan_btn.clicked.connect(self._plan)
        self.write_btn = bb.addButton("Write to Mixxx…", QDialogButtonBox.ActionRole)
        self.write_btn.setEnabled(False)
        self.write_btn.clicked.connect(self._write)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.max_hot, self.replace, self.complete):
            (w.valueChanged if isinstance(w, QSpinBox) else w.toggled).connect(self._invalidate)
        self._plan()

    def _invalidate(self, *_):
        self.write_btn.setEnabled(False)
        self.out.setPlainText("Options changed: press Dry run again.")

    def _plan(self) -> None:
        store = self.view.store
        items = [(t.id, t.location, t.display, t.samplerate, store.get(t.location).sections) for t in self.tracks]
        own = {t.id: store.own_cue_ids(t.id) for t in self.tracks}
        s = self.ctrl.settings
        s.max_hotcues, s.complete_markers = self.max_hot.value(), self.complete.isChecked()
        self.ctrl.config.save()
        try:
            self.plans = mixxx_cues.plan(self.ctrl.db_path, items, own, max_hotcues=self.max_hot.value(),
                                         replace_own=self.replace.isChecked(),
                                         complete_markers=self.complete.isChecked())
        except Exception as e:  # noqa: BLE001
            self.out.setPlainText(f"Could not read Mixxx's cues: {e}")
            return
        n = sum(len(p.ops) for p in self.plans)
        lines = [f"DRY RUN — nothing written. {n} change(s) for {sum(1 for p in self.plans if p.ops)} track(s).", ""]
        for p in self.plans:
            lines += p.lines() + [""]
        self.out.setPlainText("\n".join(lines))
        self.write_btn.setEnabled(n > 0)

    def _write(self) -> None:
        n = sum(len(p.ops) for p in self.plans)
        if mixxx_cues.mixxx_running():
            QMessageBox.warning(self, "Mixxx is running",
                                "Close Mixxx first: it caches tracks and would overwrite the new cues.")
            return
        if QMessageBox.question(
                self, "Write to Mixxx",
                f"Write {n} cue change(s) into Mixxx's library?\n\nA timestamped backup of mixxxdb.sqlite is made "
                "next to it first, and everything is written in one transaction.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            bak, inserted, deleted = mixxx_cues.apply(self.ctrl.db_path, self.plans)
        except mixxx_cues.CueExportError as e:
            QMessageBox.critical(self, "Not written", str(e))
            return
        store = self.view.store
        paths = {p.track_id: p.path for p in self.plans}
        for tid, cid, kind in inserted:
            store.record_cues(tid, paths[tid], [(cid, kind)])
        for tid, cid in deleted:
            store.forget_cues(tid, [cid])
        self.write_btn.setEnabled(False)
        self.out.setPlainText(f"Written: {len(inserted)} new cue(s), {n - len(inserted) - len(deleted)} updated, "
                              f"{len(deleted)} replaced.\n\nBackup: {bak}\n{mixxx_cues.restore_hint(bak)}")
        self.ctrl.message.emit(f"Exported cues to Mixxx (backup {bak.name}).")
