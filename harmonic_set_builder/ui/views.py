"""Table views with a persisted column chooser and placeholder/duplicate styling."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPen
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QStyledItemDelegate, QTableView

from . import columns as C
from . import theme


class RowDelegate(QStyledItemDelegate):
    """Dashed edges for transitional entries; amber edge for red duplicates;
    color swatches in the Color column."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        r: C.Row = index.data(C.ROW_ROLE)
        if r is None:
            return
        rect: QRect = option.rect
        painter.save()
        if r.placeholder:
            pen = QPen(theme.ACCENT if r.slot_active or r.is_target else theme.SLOT_FG, 1, Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(rect.topLeft(), rect.topRight())
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            view = self.parent()
            if view and view.horizontalHeader().visualIndex(index.column()) == _first_visible(view):
                painter.drawLine(rect.topLeft(), rect.bottomLeft())
        elif r.clash and r.duplicate:
            painter.setPen(QPen(theme.DUP, 2))
            painter.drawLine(rect.topLeft(), rect.topRight())
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.restore()


def _first_visible(view: QTableView) -> int:
    h = view.horizontalHeader()
    for v in range(h.count()):
        if not h.isSectionHidden(h.logicalIndex(v)):
            return v
    return 0


class RowTable(QTableView):
    """QTableView whose columns can be toggled from a header right-click menu.
    Visibility, order and widths are saved via ``state()`` / ``restore()``."""

    layoutChanged = Signal()

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setItemDelegate(RowDelegate(self))
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(22)
        self.setIconSize(QSize(20, 20))
        self.setShowGrid(False)
        h = self.horizontalHeader()
        h.setSectionsMovable(True)
        h.setHighlightSections(False)
        h.setStretchLastSection(False)
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(self._header_menu)
        h.sectionMoved.connect(lambda *a: self.layoutChanged.emit())
        h.sectionResized.connect(lambda *a: self.layoutChanged.emit())
        self._defaults_applied = False

    def model_cols(self):
        m = self.model()
        while hasattr(m, "sourceModel"):
            m = m.sourceModel()
        return m.cols

    def apply_defaults(self) -> None:
        visible = set(C.DEFAULT_VISIBLE[self.kind])
        order = C.DEFAULT_VISIBLE[self.kind]
        h = self.horizontalHeader()
        cols = self.model_cols()
        for i, c in enumerate(cols):
            h.setSectionHidden(i, c.id not in visible)
            h.resizeSection(i, c.width)
        for target, cid in enumerate(order):
            logical = next((i for i, c in enumerate(cols) if c.id == cid), None)
            if logical is not None:
                h.moveSection(h.visualIndex(logical), target)

    def state(self) -> str:
        return bytes(self.horizontalHeader().saveState().toBase64()).decode()

    def restore(self, state: str | None) -> None:
        if state and self.horizontalHeader().restoreState(QByteArray.fromBase64(state.encode())):
            return
        self.apply_defaults()

    def _header_menu(self, pos) -> None:
        h = self.horizontalHeader()
        menu = QMenu(self)
        for i, c in enumerate(self.model_cols()):
            label = {"fix": "Fix (+)", "menu": "Actions (⋯)", "preview": "Preview (▶)", "cover": "Cover art"}.get(c.id, c.header or c.id)
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(not h.isSectionHidden(i))
            act.toggled.connect(lambda on, i=i: (h.setSectionHidden(i, not on), self.layoutChanged.emit()))
        menu.addSeparator()
        menu.addAction("Reset columns", lambda: (self.apply_defaults(), self.layoutChanged.emit()))
        menu.exec(h.mapToGlobal(pos))

    def selected_rows(self) -> list[C.Row]:
        rows = sorted({i.row() for i in self.selectionModel().selectedRows()})
        m = self.model()
        return [m.index(r, 0).data(C.ROW_ROLE) for r in rows]

    def setup_drag(self, accept_drops: bool) -> None:
        self.setDragEnabled(True)
        self.setAcceptDrops(accept_drops)
        self.setDropIndicatorShown(accept_drops)
        self.setDragDropOverwriteMode(False)
        self.setDragDropMode(QAbstractItemView.DragDrop if accept_drops else QAbstractItemView.DragOnly)
        self.setDefaultDropAction(Qt.CopyAction)
