"""Settings dialog: BPM bands, harmonic rules, energy, routes, display and the move table."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHeaderView, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..core.camelot import DEFAULT_MOVES, TIER_NAMES
from ..core.settings import Settings


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Settings")
        self.resize(560, 520)
        tabs = QTabWidget()

        mix = QWidget()
        f = QFormLayout(mix)
        self.safe = QDoubleSpinBox(decimals=1, maximum=50, singleStep=0.5, value=s.bpm.safe)
        self.caution = QDoubleSpinBox(decimals=1, maximum=50, singleStep=0.5, value=s.bpm.caution)
        self.percent = QCheckBox("Judge BPM difference as percent of tempo")
        self.percent.setChecked(s.bpm.percent)
        self.half = QCheckBox("Match half / double time (dubstep, dnb)")
        self.half.setChecked(s.bpm.half_double)
        self.keylock = QCheckBox("Keylock on (key unaffected by tempo)")
        self.keylock.setChecked(s.keylock)
        self.energy_in_key = QCheckBox("Energy moves (+2, semitone) count as in key")
        self.energy_in_key.setChecked(s.energy_moves_in_key)
        self.energy_in_key.setToolTip("Turn off for strict practice: they become key-mixing breaks")
        f.addRow("Safe ≤", self.safe)
        f.addRow("Caution ≤", self.caution)
        f.addRow(self.percent)
        f.addRow(self.half)
        f.addRow(self.keylock)
        f.addRow(self.energy_in_key)
        tabs.addTab(mix, "Mixing")

        en = QWidget()
        f = QFormLayout(en)
        self.baseline = QSpinBox(minimum=0, maximum=20, value=s.energy_baseline)
        self.tags = QCheckBox("Use “Energy N” comment tags as absolute levels")
        self.tags.setChecked(s.energy_from_tags)
        f.addRow("Starting energy", self.baseline)
        f.addRow(self.tags)
        self.moves = QTableWidget(len(DEFAULT_MOVES), 4)
        self.moves.setHorizontalHeaderLabels(["Move", "Mood label", "Tier", "Energy Δ"])
        self.moves.verticalHeader().hide()
        current = s.moves()
        for i, name in enumerate(DEFAULT_MOVES):
            m = current[name]
            self.moves.setItem(i, 0, _ro(name))
            self.moves.setItem(i, 1, QTableWidgetItem(m.label))
            self.moves.setItem(i, 2, _ro(TIER_NAMES[m.tier]))
            if m.energy is None:
                self.moves.setItem(i, 3, _ro("—"))
            else:
                sp = QSpinBox(minimum=-10, maximum=10, value=m.energy)
                self.moves.setCellWidget(i, 3, sp)
        self.moves.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        f.addRow(self.moves)
        tabs.addTab(en, "Energy && moves")

        rt = QWidget()
        f = QFormLayout(rt)
        self.allow_caution = QCheckBox("Allow Caution BPM steps in routes")
        self.allow_caution.setChecked(s.route_allow_caution)
        self.max_hops = QSpinBox(minimum=2, maximum=20, value=s.route_max_hops)
        self.fallback = QCheckBox("Fall back to the whole library when the focused source has no route")
        self.fallback.setChecked(s.library_fallback)
        f.addRow(self.allow_caution)
        f.addRow("Max hops", self.max_hops)
        f.addRow(self.fallback)
        tabs.addTab(rt, "Routes")

        ds = QWidget()
        f = QFormLayout(ds)
        self.notation = QComboBox()
        for n in ("camelot", "lancelot", "openkey", "traditional"):
            self.notation.addItem(n.capitalize() if n != "openkey" else "OpenKey", n)
        self.notation.setCurrentIndex(self.notation.findData(s.key_notation))
        self.history = QCheckBox("Show history playlists in Sources")
        self.history.setChecked(s.show_history_playlists)
        self.autodj = QCheckBox("Show Auto DJ playlist in Sources")
        self.autodj.setChecked(s.show_autodj_playlist)
        f.addRow("Key notation", self.notation)
        f.addRow(self.history)
        f.addRow(self.autodj)
        tabs.addTab(ds, "Display")

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._defaults)
        lay.addWidget(bb)

    def _defaults(self):
        d = Settings()
        self.safe.setValue(d.bpm.safe)
        self.caution.setValue(d.bpm.caution)
        self.percent.setChecked(d.bpm.percent)
        self.half.setChecked(d.bpm.half_double)
        self.keylock.setChecked(d.keylock)
        self.energy_in_key.setChecked(d.energy_moves_in_key)
        self.baseline.setValue(d.energy_baseline)
        self.tags.setChecked(d.energy_from_tags)
        for i, m in enumerate(DEFAULT_MOVES.values()):
            self.moves.item(i, 1).setText(m.label)
            if (sp := self.moves.cellWidget(i, 3)) is not None:
                sp.setValue(m.energy)
        self.allow_caution.setChecked(d.route_allow_caution)
        self.max_hops.setValue(d.route_max_hops)
        self.fallback.setChecked(d.library_fallback)

    def accept(self):
        s = self.s
        s.bpm.safe = self.safe.value()
        s.bpm.caution = max(self.caution.value(), self.safe.value())
        s.bpm.percent = self.percent.isChecked()
        s.bpm.half_double = self.half.isChecked()
        s.keylock = self.keylock.isChecked()
        s.energy_moves_in_key = self.energy_in_key.isChecked()
        s.energy_baseline = self.baseline.value()
        s.energy_from_tags = self.tags.isChecked()
        overrides = {}
        for i, (name, m) in enumerate(DEFAULT_MOVES.items()):
            ov = {}
            label = self.moves.item(i, 1).text().strip()
            if label and label != m.label:
                ov["label"] = label
            sp = self.moves.cellWidget(i, 3)
            if sp is not None and sp.value() != m.energy:
                ov["energy"] = sp.value()
            if ov:
                overrides[name] = ov
        s.move_overrides = overrides
        s.route_allow_caution = self.allow_caution.isChecked()
        s.route_max_hops = self.max_hops.value()
        s.library_fallback = self.fallback.isChecked()
        s.key_notation = self.notation.currentData()
        s.show_history_playlists = self.history.isChecked()
        s.show_autodj_playlist = self.autodj.isChecked()
        super().accept()


def _ro(text: str) -> QTableWidgetItem:
    from PySide6.QtCore import Qt

    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    return it
