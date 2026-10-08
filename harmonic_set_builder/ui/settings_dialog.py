"""Settings dialog: BPM bands, harmonic rules, energy, routes, display, the move table
and phrase analysis."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..core.camelot import DEFAULT_MOVES, TIER_NAMES
from ..core.settings import Settings
from .file_dialogs import open_file


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, parent=None, allin1_default: str = ""):
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
        self.overlap = QSpinBox(minimum=0, maximum=128, value=s.mix_overlap_bars, suffix=" bars")
        self.overlap.setToolTip("How long two tracks play together in a mix, for the set-length estimate "
                                "(0 = back to back)")
        f.addRow("Mix overlap", self.overlap)
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

        ph = QWidget()
        f = QFormLayout(ph)
        self.backend = QComboBox()
        self.backend.addItem("Built-in (fast, tuned for dance music)", "builtin")
        self.backend.addItem("allin1 (neural network, minutes per track on CPU)", "allin1")
        self.backend.setCurrentIndex(max(0, self.backend.findData(s.analysis_backend)))
        self.allin1 = QLineEdit(s.allin1_python)
        self.allin1.setPlaceholderText(allin1_default or "python of the allin1 environment")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_allin1)
        check = QPushButton("Check")
        check.clicked.connect(self._check_allin1)
        row = QHBoxLayout()
        row.addWidget(self.allin1, 1)
        row.addWidget(browse)
        row.addWidget(check)
        self.allin1_status = QLabel()
        self.allin1_status.setObjectName("hint")
        self.allin1_status.setWordWrap(True)
        self._allin1_default = allin1_default
        self.workers = QSpinBox(minimum=0, maximum=16, value=s.analysis_workers, specialValueText="Automatic")
        self.workers.setToolTip("Tracks analyzed at once (allin1 always uses one)")
        self.max_hotcues = QSpinBox(minimum=1, maximum=36, value=s.max_hotcues)
        self.complete = QCheckBox("Cue export may fill in a missing intro end / outro start on Mixxx's markers")
        self.complete.setChecked(s.complete_markers)
        f.addRow("Analyzer", self.backend)
        f.addRow("allin1 Python", row)
        f.addRow("", self.allin1_status)
        f.addRow("Parallel analyses", self.workers)
        f.addRow("Hot cue slots for export", self.max_hotcues)
        f.addRow(self.complete)
        tabs.addTab(ph, "Phrase analysis")

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._defaults)
        lay.addWidget(bb)

    def _browse_allin1(self) -> None:
        path = open_file(self, "Python of the allin1 environment", self.allin1.text() or self._allin1_default, "")
        if path:
            self.allin1.setText(path)

    def _check_allin1(self) -> None:
        from ..analysis.structure import allin1_available

        self.allin1_status.setText("Checking…")
        self.allin1_status.repaint()
        ok, msg = allin1_available(self.allin1.text().strip() or self._allin1_default)
        self.allin1_status.setText(("✓ " if ok else "✗ ") + msg)

    def _defaults(self):
        d = Settings()
        self.safe.setValue(d.bpm.safe)
        self.caution.setValue(d.bpm.caution)
        self.percent.setChecked(d.bpm.percent)
        self.half.setChecked(d.bpm.half_double)
        self.keylock.setChecked(d.keylock)
        self.energy_in_key.setChecked(d.energy_moves_in_key)
        self.overlap.setValue(d.mix_overlap_bars)
        self.baseline.setValue(d.energy_baseline)
        self.tags.setChecked(d.energy_from_tags)
        for i, m in enumerate(DEFAULT_MOVES.values()):
            self.moves.item(i, 1).setText(m.label)
            if (sp := self.moves.cellWidget(i, 3)) is not None:
                sp.setValue(m.energy)
        self.allow_caution.setChecked(d.route_allow_caution)
        self.max_hops.setValue(d.route_max_hops)
        self.fallback.setChecked(d.library_fallback)
        self.backend.setCurrentIndex(self.backend.findData(d.analysis_backend))
        self.allin1.setText(d.allin1_python)
        self.workers.setValue(d.analysis_workers)
        self.max_hotcues.setValue(d.max_hotcues)
        self.complete.setChecked(d.complete_markers)

    def accept(self):
        s = self.s
        s.bpm.safe = self.safe.value()
        s.bpm.caution = max(self.caution.value(), self.safe.value())
        s.bpm.percent = self.percent.isChecked()
        s.bpm.half_double = self.half.isChecked()
        s.keylock = self.keylock.isChecked()
        s.energy_moves_in_key = self.energy_in_key.isChecked()
        s.mix_overlap_bars = self.overlap.value()
        s.energy_baseline = self.baseline.value()
        s.energy_from_tags = self.tags.isChecked()
        s.analysis_backend = self.backend.currentData()
        s.allin1_python = self.allin1.text().strip()
        s.analysis_workers = self.workers.value()
        s.max_hotcues = self.max_hotcues.value()
        s.complete_markers = self.complete.isChecked()
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
