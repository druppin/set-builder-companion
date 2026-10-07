"""Qt table models over ``Row`` lists, drag-and-drop payloads and the track proxy."""
from __future__ import annotations

import json
from typing import Callable, Optional

from PySide6.QtCore import QAbstractTableModel, QMimeData, QModelIndex, QSortFilterProxyModel, Qt

from . import columns as C

MIME = "application/x-harmonic-set-builder"


def encode(kind: str, ids: list[int], uids: list[str]) -> QMimeData:
    md = QMimeData()
    md.setData(MIME, json.dumps({"from": kind, "ids": ids, "uids": uids}).encode())
    return md


def decode(md: QMimeData) -> Optional[dict]:
    if not md.hasFormat(MIME):
        return None
    try:
        return json.loads(bytes(md.data(MIME)).decode())
    except ValueError:
        return None


class RowModel(QAbstractTableModel):
    """``kind`` is "track", "set" or "pool"; it names the drag source."""

    def __init__(self, kind: str, col_ids: list[str], parent=None):
        super().__init__(parent)
        self.kind = kind
        self.cols = [C.COL_BY_ID[c] for c in col_ids]
        self.rows: list[C.Row] = []
        self.notation = "camelot"
        self.playing_id: Optional[int] = None  # track being previewed
        self.on_drop: Optional[Callable[[dict, int], None]] = None

    # --- data
    def set_rows(self, rows: list[C.Row]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def col_index(self, col_id: str) -> int:
        return next((i for i, c in enumerate(self.cols) if c.id == col_id), -1)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self.cols[section].header
            if role == Qt.ToolTipRole:
                return self.cols[section].tooltip or self.cols[section].header
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        col = self.cols[index.column()]
        if role == Qt.DisplayRole:
            if col.id == "preview" and r.track and r.track.id == self.playing_id:
                return "⏸"
            v = col.display(r, self.notation)
            return "" if v is None else str(v)
        if role == C.SORT_ROLE:
            if col.sort:
                return col.sort(r)
            v = col.display(r, self.notation)
            return str(v).casefold() if v is not None else ""
        if role == C.ROW_ROLE:
            return r
        if role == Qt.ForegroundRole:
            return C.foreground(col, r)
        if role == Qt.BackgroundRole:
            return C.background(r)
        if role == Qt.FontRole:
            return C.font(r)
        if role == Qt.ToolTipRole:
            return C.tooltip(col, r)
        if role == Qt.DecorationRole and col.id == "color":
            return C.color_swatch(r)
        if role == Qt.TextAlignmentRole:
            if col.id in ("fix", "menu", "want", "in_set", "preview"):
                return int(Qt.AlignCenter)
            if col.align_right:
                return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsDropEnabled if self.on_drop else Qt.NoItemFlags
        r = self.rows[index.row()]
        f = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if r.is_slot and not r.slot_active:
            f = Qt.ItemIsSelectable  # disabled: fill the earlier transition first
        if not r.placeholder and r.track is not None:
            f |= Qt.ItemIsDragEnabled
        if self.on_drop:
            f |= Qt.ItemIsDropEnabled
        return f

    def set_playing(self, track_id: Optional[int]) -> None:
        self.playing_id = track_id
        c = self.col_index("preview")
        if c >= 0 and self.rows:
            self.dataChanged.emit(self.index(0, c), self.index(len(self.rows) - 1, c), [Qt.DisplayRole])

    # --- drag and drop
    def mimeTypes(self):
        return [MIME]

    def mimeData(self, indexes):
        rows = sorted({i.row() for i in indexes})
        picked = [self.rows[i] for i in rows if not self.rows[i].placeholder and self.rows[i].track]
        return encode(
            self.kind,
            [r.track.id for r in picked],
            [r.entry.uid if r.entry else r.extra.get("uid", "") for r in picked],
        )

    def supportedDragActions(self):
        return Qt.CopyAction

    def supportedDropActions(self):
        return Qt.CopyAction | Qt.MoveAction

    def canDropMimeData(self, data, action, row, column, parent):
        return self.on_drop is not None and data.hasFormat(MIME)

    def dropMimeData(self, data, action, row, column, parent):
        payload = decode(data)
        if payload is None or self.on_drop is None:
            return False
        if row < 0:
            row = parent.row() if parent.isValid() else len(self.rows)
        self.on_drop(payload, row)
        return False  # the controller rebuilds the rows; nothing for the view to remove


class TrackProxy(QSortFilterProxyModel):
    """Search filter, "hide tracks already in the set", pinned pool tracks on top."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tokens: list[str] = []
        self.hide_in_set = False
        self.setSortRole(C.SORT_ROLE)

    def _refilter(self, apply) -> None:
        if hasattr(self, "beginFilterChange"):  # Qt 6.10+
            self.beginFilterChange()
            apply()
            self.endFilterChange(QSortFilterProxyModel.Direction.Rows)
        else:
            apply()
            self.invalidateFilter()

    def set_filter(self, text: str) -> None:
        self._refilter(lambda: setattr(self, "tokens", text.casefold().split()))

    def set_hide_in_set(self, on: bool) -> None:
        self._refilter(lambda: setattr(self, "hide_in_set", on))

    def filterAcceptsRow(self, source_row, source_parent):
        r: C.Row = self.sourceModel().rows[source_row]
        if self.hide_in_set and r.in_set:
            return False
        return all(t in r.haystack for t in self.tokens)

    def lessThan(self, left, right):
        lr = left.data(C.ROW_ROLE)
        rr = right.data(C.ROW_ROLE)
        if lr.pinned != rr.pinned:
            # Keep pinned rows on top whichever way the column is sorted.
            return lr.pinned if self.sortOrder() == Qt.AscendingOrder else rr.pinned
        a, b = left.data(C.SORT_ROLE), right.data(C.SORT_ROLE)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)
