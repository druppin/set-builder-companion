"""Energy graph: x = set position, y = energy, points labeled with the Camelot key.

Modeled on camelotwheel.org's Energy Level Progression. Segments break at
clashes (red marker); transitional entries are hollow points on a dashed line;
the route target is a solid star at the end.
"""
from __future__ import annotations

from dataclasses import dataclass

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal

from . import theme


@dataclass
class GraphPoint:
    row: int  # setlist row index
    y: float
    label: str  # key text
    tip: str
    kind: str  # "track" | "slot" | "target"
    clash: bool


class EnergyGraph(pg.PlotWidget):
    pointClicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent, background=theme.BASE)
        self.setMinimumHeight(110)
        self.setMenuEnabled(False)
        self.setMouseEnabled(x=False, y=False)
        self.hideButtons()
        pi = self.getPlotItem()
        pi.showGrid(x=False, y=True, alpha=0.15)
        pi.setLabel("left", "Energy")
        for ax in ("left", "bottom"):
            pi.getAxis(ax).setTextPen(theme.DIM)
            pi.getAxis(ax).setPen(theme.DIM)
        self._items = []

    def set_points(self, pts: list[GraphPoint]) -> None:
        pi = self.getPlotItem()
        for it in self._items:
            pi.removeItem(it)
        self._items = []
        if not pts:
            return
        xs = list(range(1, len(pts) + 1))
        for i in range(1, len(pts)):
            a, b = pts[i - 1], pts[i]
            if b.clash:
                continue  # the line breaks; the next segment starts here
            dashed = a.kind != "track" or b.kind != "track"
            pen = pg.mkPen(theme.ACCENT if not dashed else theme.SLOT_FG, width=2,
                           style=Qt.DashLine if dashed else Qt.SolidLine)
            item = pg.PlotDataItem([xs[i - 1], xs[i]], [a.y, b.y], pen=pen)
            pi.addItem(item)
            self._items.append(item)
        spots = []
        for x, p in zip(xs, pts):
            if p.kind == "slot":
                brush, pen, symbol, size = pg.mkBrush(theme.BASE), pg.mkPen(theme.SLOT_FG, width=1.5), "o", 10
            elif p.kind == "target":
                brush, pen, symbol, size = pg.mkBrush(theme.ACCENT), pg.mkPen(theme.ACCENT), "star", 16
            elif p.clash:
                brush, pen, symbol, size = pg.mkBrush(theme.CLASH), pg.mkPen("#ffffff", width=1), "o", 12
            else:
                brush, pen, symbol, size = pg.mkBrush(theme.ACCENT), pg.mkPen(theme.ACCENT), "o", 9
            spots.append({"pos": (x, p.y), "brush": brush, "pen": pen, "symbol": symbol, "size": size,
                          "data": (p.row, p.tip)})
        scatter = pg.ScatterPlotItem(spots=spots, hoverable=True, hoverSize=14,
                                     tip=lambda x, y, data: data[1])
        scatter.sigClicked.connect(self._clicked)
        pi.addItem(scatter)
        self._items.append(scatter)
        for x, p in zip(xs, pts):
            t = pg.TextItem(p.label, color=theme.TEXT if p.kind == "track" else theme.SLOT_FG, anchor=(0.5, 1.4))
            t.setPos(x, p.y)
            pi.addItem(t)
            self._items.append(t)
        ys = [p.y for p in pts]
        pi.setXRange(0.5, len(pts) + 0.5, padding=0)
        pi.setYRange(min(ys) - 1.5, max(ys) + 2, padding=0)

    def _clicked(self, _item, points, _ev=None):
        if len(points):
            self.pointClicked.emit(points[0].data()[0])
