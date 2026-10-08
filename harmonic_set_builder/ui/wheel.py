"""Interactive Camelot wheel: outer ring B (major), inner ring A (minor), number n
at n o'clock. Click a key to pick it; tint keys to show compatible moves."""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..core.camelot import MAJOR, MINOR, Key
from ..core.theory import key_name, scale_names, short_name
from . import theme


def base_color(key: Key) -> QColor:
    """The wheel's familiar rainbow, toned down for the dark theme."""
    hue = ((key.number - 1) * 30 + 200) % 360
    return QColor.fromHsv(hue, 120 if key.mode == MAJOR else 150, 120 if key.mode == MAJOR else 95)


class CamelotWheel(QWidget):
    keyClicked = Signal(object)  # Key

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.from_key: Optional[Key] = None
        self.to_key: Optional[Key] = None
        self.tints: dict[Key, QColor] = {}  # e.g. tier colours for compatible keys
        self.dimmed = False  # grey out keys without a tint
        self.show_names = True
        self.marks: dict[Key, str] = {}  # small text under a key, e.g. "3 tracks"

    def set_state(self, from_key=None, to_key=None, tints=None, dimmed=False, marks=None) -> None:
        self.from_key, self.to_key = from_key, to_key
        self.tints = tints or {}
        self.dimmed = dimmed
        self.marks = marks or {}
        self.update()

    def heightForWidth(self, w: int) -> int:
        return w

    # -------------------------------------------------------------- geometry
    def _geom(self):
        side = min(self.width(), self.height()) - 8
        c = QPointF(self.width() / 2, self.height() / 2)
        r = side / 2
        return c, r, r * 0.66, r * 0.33

    @staticmethod
    def _angle(n: int) -> float:
        return 90 - n * 30  # degrees, Qt's counter-clockwise from 3 o'clock

    def key_at(self, pos: QPointF) -> Optional[Key]:
        c, r_out, r_mid, r_in = self._geom()
        dx, dy = pos.x() - c.x(), c.y() - pos.y()
        d = math.hypot(dx, dy)
        if d > r_out or d < r_in:
            return None
        ang = math.degrees(math.atan2(dy, dx))
        n = round((90 - ang) / 30) % 12 or 12
        return Key(n, MAJOR if d >= r_mid else MINOR)

    # ---------------------------------------------------------------- paint
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c, r_out, r_mid, r_in = self._geom()
        for mode, (r1, r0) in ((MAJOR, (r_out, r_mid)), (MINOR, (r_mid, r_in))):
            for n in range(1, 13):
                key = Key(n, mode)
                path = self._segment(c, r0, r1, self._angle(n))
                fill = self.tints.get(key)
                if fill is None:
                    fill = base_color(key)
                    if self.dimmed:
                        fill = QColor(fill).darker(260)
                p.fillPath(path, fill)
                pen = QPen(theme.BG, 2)
                if key == self.from_key:
                    pen = QPen(theme.ACCENT, 4)
                elif key == self.to_key:
                    pen = QPen(QColor("#ffffff"), 3)
                p.strokePath(path, pen)
        # labels on top of all strokes
        for mode, (r1, r0) in ((MAJOR, (r_out, r_mid)), (MINOR, (r_mid, r_in))):
            for n in range(1, 13):
                key = Key(n, mode)
                rr = (r0 + r1) / 2
                a = math.radians(self._angle(n))
                pt = QPointF(c.x() + rr * math.cos(a), c.y() - rr * math.sin(a))
                self._label(p, pt, key, r1 - r0)
        p.setPen(theme.DIM)
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, r_in / 7))
        p.setFont(f)
        p.drawText(QRectF(c.x() - r_in, c.y() - r_in, 2 * r_in, 2 * r_in), Qt.AlignCenter, "A minor\ninside\n\nB major\noutside")

    def _segment(self, c: QPointF, r0: float, r1: float, mid_deg: float) -> QPainterPath:
        start, span = mid_deg - 15, 30
        outer = QRectF(c.x() - r1, c.y() - r1, 2 * r1, 2 * r1)
        inner = QRectF(c.x() - r0, c.y() - r0, 2 * r0, 2 * r0)
        path = QPainterPath()
        path.arcMoveTo(outer, start)
        path.arcTo(outer, start, span)
        path.arcTo(inner, start + span, -span)
        path.closeSubpath()
        return path

    def _label(self, p: QPainter, pt: QPointF, key: Key, ring: float) -> None:
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(max(8.0, ring / 4.2))
        p.setFont(f)
        p.setPen(QColor("#ffffff") if not (self.dimmed and key not in self.tints) else theme.DIM)
        box = QRectF(pt.x() - ring, pt.y() - ring / 2, 2 * ring, ring)
        main = str(key)
        sub = self.marks.get(key) or (short_name(key) if self.show_names else "")
        if sub:
            p.drawText(box.adjusted(0, -ring * 0.18, 0, -ring * 0.18), Qt.AlignCenter, main)
            f2 = QFont(self.font())
            f2.setPointSizeF(max(6.5, ring / 6.5))
            p.setFont(f2)
            p.drawText(box.adjusted(0, ring * 0.26, 0, ring * 0.26), Qt.AlignCenter, sub)
        else:
            p.drawText(box, Qt.AlignCenter, main)

    # ---------------------------------------------------------------- mouse
    def mousePressEvent(self, ev):
        k = self.key_at(ev.position())
        if k is not None and ev.button() == Qt.LeftButton:
            self.keyClicked.emit(k)

    def mouseMoveEvent(self, ev):
        k = self.key_at(ev.position())
        if k is None:
            QToolTip.hideText()
            return
        self.setCursor(Qt.PointingHandCursor)
        QToolTip.showText(ev.globalPosition().toPoint(),
                          f"<b>{k} · {key_name(k)}</b><br>{' '.join(scale_names(k))}", self)
