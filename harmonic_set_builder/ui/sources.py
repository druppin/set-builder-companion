"""Mixxx-style sources tree: Library, Crates and Playlists with track counts."""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem

from ..data.mixxx_db import Collection, Library, visible_playlists

LIBRARY_KEY = ("library", 0)
SET_KEY = ("set", 0)
CURRENT_SET = "current-set"  # emitted by ``focused`` for the "Current set" item


class SourcesTree(QTreeWidget):
    focused = Signal(object)  # Collection | None (whole library) | CURRENT_SET
    openAsSet = Signal(object)  # Collection

    def __init__(self, parent=None, set_item: bool = False):
        super().__init__(parent)
        self.set_item = set_item  # offer "Current set" above the library
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setExpandsOnDoubleClick(False)  # a single click on a heading folds it
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemSelectionChanged.connect(self._selection_changed)
        self._collections: dict[tuple, Collection] = {}
        self._focus_item: Optional[QTreeWidgetItem] = None
        self._expanded = {"crates": True, "playlists": True}

    def populate(self, lib: Library, show_history: bool, show_autodj: bool, focus: Optional[tuple],
                 count: Callable[[Sequence[int]], str] = lambda ids: str(len(ids)),
                 set_ids: Sequence[int] = ()) -> None:
        """``count`` labels each source from its track ids (e.g. "40 · 12 ✓")."""
        for key in ("crates", "playlists"):
            item = self._heading(key)
            if item is not None:
                self._expanded[key] = item.isExpanded()
        self.blockSignals(True)  # rebuilding: no focus changes, no stale items
        self._focus_item = None
        self.clear()
        self._collections = {}
        selected = None
        if self.set_item:
            set_it = QTreeWidgetItem([f"Current set ({count(list(set_ids))})"])
            set_it.setData(0, Qt.UserRole, SET_KEY)
            self.addTopLevelItem(set_it)
            if focus == SET_KEY:
                selected = set_it
        lib_item = QTreeWidgetItem([f"Library ({count(list(lib.tracks))})"])
        lib_item.setData(0, Qt.UserRole, LIBRARY_KEY)
        self.addTopLevelItem(lib_item)
        selected = selected or lib_item
        for name, key, items in (
            ("Crates", "crates", lib.crates),
            ("Playlists", "playlists", visible_playlists(lib, show_history, show_autodj)),
        ):
            head = QTreeWidgetItem([name])
            head.setData(0, Qt.UserRole, ("header", key))
            head.setFlags(Qt.ItemIsEnabled)
            self.addTopLevelItem(head)
            for c in items:
                it = QTreeWidgetItem([f"{c.name} ({count(c.track_ids)})"])
                k = (c.kind, c.id)
                it.setData(0, Qt.UserRole, k)
                self._collections[k] = c
                head.addChild(it)
                if focus == k:
                    selected = it
            head.setExpanded(self._expanded[key])
        self._focus_item = selected
        self.setCurrentItem(selected)
        self.blockSignals(False)

    def _heading(self, key: str) -> Optional[QTreeWidgetItem]:
        for i in range(self.topLevelItemCount()):
            it = self.topLevelItem(i)
            if it.data(0, Qt.UserRole) == ("header", key):
                return it
        return None

    def focus_key(self) -> tuple:
        return tuple(self._focus_item.data(0, Qt.UserRole)) if self._focus_item else LIBRARY_KEY

    def collection(self, key) -> Optional[Collection]:
        return self._collections.get(tuple(key)) if key else None


    @staticmethod
    def _is_heading(item) -> bool:
        key = item.data(0, Qt.UserRole) if item else None
        return bool(key) and key[0] == "header"

    def mousePressEvent(self, ev):
        """Clicking a heading folds it (like Mixxx) without touching the selection."""
        item = self.itemAt(ev.position().toPoint())
        if ev.button() == Qt.LeftButton and self._is_heading(item):
            item.setExpanded(not item.isExpanded())
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        if self._is_heading(self.itemAt(ev.position().toPoint())):
            self.mousePressEvent(ev)  # the second click of a quick double-click
            return
        super().mouseDoubleClickEvent(ev)

    def _selection_changed(self) -> None:
        """Selecting a source (mouse or keyboard) focuses it. Anything else, such as
        a heading or folding the focused item away, keeps the focused source highlighted."""
        sel = [i for i in self.selectedItems() if not self._is_heading(i)]
        if sel and sel[0] is not self._focus_item:
            self._focus_item = sel[0]
            key = tuple(sel[0].data(0, Qt.UserRole))
            self.focused.emit(CURRENT_SET if key == SET_KEY else
                              None if key == LIBRARY_KEY else self._collections.get(key))
            return
        if self._focus_item is None or self._focus_item in self.selectedItems():
            return
        self.blockSignals(True)
        self.setCurrentItem(self._focus_item)
        self._focus_item.setSelected(True)
        self.blockSignals(False)

    def _menu(self, pos):
        item = self.itemAt(pos)
        if not item:
            return
        c = self._collections.get(tuple(item.data(0, Qt.UserRole) or ()))
        if not c:
            return
        menu = QMenu(self)
        menu.addAction("Open as set", lambda: self.openAsSet.emit(c))
        menu.exec(self.viewport().mapToGlobal(pos))
