"""Entry point: ``python -m harmonic_set_builder [--db PATH]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="harmonic-set-builder", description="Plan harmonic DJ sets from a Mixxx library.")
    parser.add_argument("--db", help="path to mixxxdb.sqlite (saved as the default)")
    parser.add_argument("--data-dir", help="override where sets and config are stored")
    args = parser.parse_args(argv)

    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QApplication

    from .data.config import Config
    from .ui import theme
    from .ui.controller import Controller
    from .ui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Harmonic Set Builder")
    app.setOrganizationName("HarmonicSetBuilder")
    theme.apply(app)

    if args.data_dir:
        config_dir = data_dir = Path(args.data_dir)
    else:
        config_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation))
        data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.AppDataLocation))
    config = Config(config_dir / "config.json")
    ctrl = Controller(config, data_dir)
    win = MainWindow(ctrl)
    ctrl.load_initial()
    ctrl.refresh_library(args.db)
    if args.db:
        config.save()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
