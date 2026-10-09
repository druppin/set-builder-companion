"""Where config and data live (shared by the app and the ``hsb`` CLI)."""
from __future__ import annotations

import sys
from pathlib import Path

APP_NAME = "Harmonic Set Builder"
ORG_NAME = "HarmonicSetBuilder"


def app_dirs() -> tuple[Path, Path]:
    """(config dir, data dir), e.g. ~/.config/HarmonicSetBuilder/Harmonic Set Builder and
    ~/.local/share/HarmonicSetBuilder/Harmonic Set Builder on Linux."""
    from PySide6.QtCore import QCoreApplication, QStandardPaths

    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationName(ORG_NAME)
    return (Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation)),
            Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)))


def default_cuedetr_python(data_dir: Path) -> Path:
    venv = Path(data_dir) / "tools" / "cue-detr-venv"
    return venv / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")


def default_allin1_python(data_dir: Path) -> Path:
    venv = Path(data_dir) / "allin1-venv"
    return venv / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
