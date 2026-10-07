"""Open/save dialogs whose sidebar lists mounted drives (USB sticks included).

On Linux the Qt dialog is used so the sidebar can be filled; it is rebuilt each
time a dialog opens, so a stick plugged in later still shows up. Windows and
macOS keep their native dialogs, which already list drives.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QStorageInfo, QUrl
from PySide6.QtWidgets import QFileDialog, QSplitter, QWidget

# Where desktop systems mount removable and extra drives.
_MOUNT_PARENTS = ("/run/media/", "/media/", "/mnt/", "/Volumes/")


def mounted_drives() -> list[Path]:
    out = []
    for v in QStorageInfo.mountedVolumes():
        root = v.rootPath()
        if not (v.isValid() and v.isReady()):
            continue
        if sys.platform.startswith("win") or root.startswith(_MOUNT_PARENTS):
            out.append(Path(root))
    return sorted(out, key=lambda p: str(p).casefold())


def sidebar_urls() -> list[QUrl]:
    home = Path.home()
    places = [home] + [p for p in (home / "Music", home / "Desktop") if p.is_dir()]
    urls = [QUrl("file:")] + [QUrl.fromLocalFile(str(p)) for p in places]
    return urls + [QUrl.fromLocalFile(str(p)) for p in mounted_drives()]


def _dialog(parent: QWidget, title: str, start: str, filt: str, save: bool) -> QFileDialog:
    d = QFileDialog(parent, title, start, filt)
    d.setAcceptMode(QFileDialog.AcceptSave if save else QFileDialog.AcceptOpen)
    d.setFileMode(QFileDialog.AnyFile if save else QFileDialog.ExistingFile)
    if not (sys.platform.startswith("win") or sys.platform == "darwin"):
        d.setOption(QFileDialog.DontUseNativeDialog, True)
        d.setSidebarUrls(sidebar_urls())
        split = d.findChild(QSplitter, "splitter")
        if split is not None:
            split.setSizes([180, 720])  # room for drive names like "A861-EA64"
    return d


def _run(d: QFileDialog) -> Optional[str]:
    if d.exec() and d.selectedFiles():
        return d.selectedFiles()[0]
    return None


def open_file(parent: QWidget, title: str, start: str, filt: str) -> Optional[str]:
    return _run(_dialog(parent, title, start, filt, save=False))


def save_file(parent: QWidget, title: str, start: str, filt: str, suffix: str = "") -> Optional[str]:
    d = _dialog(parent, title, start, filt, save=True)
    if suffix:
        d.setDefaultSuffix(suffix.lstrip("."))
    return _run(d)
