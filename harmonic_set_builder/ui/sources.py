"""Mixxx-style sources tree: Library, Crates and Playlists with track counts."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem

from ..data.mixxx_db import Collection, Library, visible_playlists

LIBRARY_KEY = ("library", 0)


class SourcesTree(QTreeWidget):
    focused = Signal(object)  # Collection | None (whole library)
    openAsSet = Signal(object)  # Collection

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setRootIsDecorated(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemClicked.connect(self._clicked)
        self._collections: dict[tuple, Collection] = {}
        self._expanded = {"crates": True, "playlists": True}

    def populate(self, lib: Library, show_history: bool, show_autodj: bool, focus: Optional[tuple]) -> None:
        for key, item in (("crates", self._crates_item()), ("playlists", self._playlists_item())):
            if item is not None:
                self._expanded[key] = item.isExpanded()
        self.clear()
        self._collections = {}
        lib_item = QTreeWidgetItem([f"Library ({len(lib.tracks)})"])
        lib_item.setData(0, Qt.UserRole, LIBRARY_KEY)
        self.addTopLevelItem(lib_item)
        selected = lib_item
        for name, key, items in (
            ("Crates", "crates", lib.crates),
            ("Playlists", "playlists", visible_playlists(lib, show_history, show_autodj)),
        ):
            head = QTreeWidgetItem([name])
            head.setData(0, Qt.UserRole, ("header", key))
            head.setFlags(Qt.ItemIsEnabled)
            self.addTopLevelItem(head)
            for c in items:
                it = QTreeWidgetItem([f"{c.name} ({len(c.track_ids)})"])
                k = (c.kind, c.id)
                it.setData(0, Qt.UserRole, k)
                self._collections[k] = c
                head.addChild(it)
                if focus == k:
                    selected = it
            head.setExpanded(self._expanded[key])
        self.setCurrentItem(selected)

    def _crates_item(self):
        return self.topLevelItem(1) if self.topLevelItemCount() > 1 else None

    def _playlists_item(self):
        return self.topLevelItem(2) if self.topLevelItemCount() > 2 else None

    def collection(self, key) -> Optional[Collection]:
        return self._collections.get(tuple(key)) if key else None

    def _clicked(self, item, _col):
        key = item.data(0, Qt.UserRole)
        if not key or key[0] == "header":
            return
        self.focused.emit(None if tuple(key) == LIBRARY_KEY else self._collections.get(tuple(key)))

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
