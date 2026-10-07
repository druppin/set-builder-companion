"""Mixxx-like dark theme and the shared status colors."""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = QColor("#1e1e1e")
BASE = QColor("#141414")
ALT = QColor("#1a1a1a")
TEXT = QColor("#d6d6d6")
DIM = QColor("#8a8a8a")
ACCENT = QColor("#e08c1a")  # Mixxx orange

CLASH_BG = QColor("#5a1d1d")
CLASH = QColor("#e04848")
DUP = QColor("#d79a1e")
DUP_BG = QColor("#4a3810")
SLOT_FG = QColor("#9a9a9a")
ACTIVE_SLOT_BG = QColor("#25313d")

BAND_COLORS = {"safe": QColor("#5cbf5c"), "caution": QColor("#e0b030"), "danger": QColor("#e05050")}
TIER_COLORS = {"smooth": QColor("#5cbf5c"), "energy": QColor("#5aa0e6"), "clash": QColor("#e05050")}


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, BG)
    p.setColor(QPalette.WindowText, TEXT)
    p.setColor(QPalette.Base, BASE)
    p.setColor(QPalette.AlternateBase, ALT)
    p.setColor(QPalette.ToolTipBase, QColor("#2a2a2a"))
    p.setColor(QPalette.ToolTipText, TEXT)
    p.setColor(QPalette.Text, TEXT)
    p.setColor(QPalette.Button, QColor("#2b2b2b"))
    p.setColor(QPalette.ButtonText, TEXT)
    p.setColor(QPalette.BrightText, QColor("#ffffff"))
    p.setColor(QPalette.Highlight, QColor("#3d5f80"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Link, ACCENT)
    p.setColor(QPalette.PlaceholderText, DIM)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#5a5a5a"))
    app.setPalette(p)
    app.setStyleSheet(
        """
        QHeaderView::section { background: #262626; color: #bdbdbd; border: 0;
            border-right: 1px solid #333; padding: 3px 6px; }
        QTableView { gridline-color: #262626; selection-background-color: #3d5f80; }
        QSplitter::handle { background: #2a2a2a; }
        QToolBar { border: 0; spacing: 4px; }
        QLabel#banner { background: #25313d; color: #e6e6e6; padding: 6px 10px; border-left: 3px solid #e08c1a; }
        QLabel#hint { color: #8a8a8a; padding: 2px 6px; }
        QToolButton:checked { background: #4a3a20; border: 1px solid #e08c1a; }
        """
    )
