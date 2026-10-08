"""Learn view: explore the Camelot wheel, hear what keys sound like, and quiz
yourself ("guess first"), with every answer explained."""
from __future__ import annotations

import hashlib
import html
import random
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout,
    QWidget,
)

from ..core import quiz, synth
from ..core.camelot import ALL_KEYS, CLASH, TIER_NAMES, Key, classify
from ..core.theory import WHEEL_BASICS, explain, key_name, scale_names, short_name
from ..core.track import Track
from . import theme
from .wheel import CamelotWheel


class SynthPlayer:
    """Plays rendered reference audio (cached as small WAVs in the data dir)."""

    def __init__(self, cache_dir: Path, parent):
        self.cache = Path(cache_dir)
        self.audio = QAudioOutput(parent)
        self.player = QMediaPlayer(parent)
        self.player.setAudioOutput(self.audio)
        self.on_start = None  # callback: pause other audio

    def play(self, name: str, render) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        path = self.cache / f"{hashlib.sha1(name.encode()).hexdigest()[:16]}.wav"
        if not path.is_file():
            path.write_bytes(synth.wav_bytes(render()))
        if self.on_start:
            self.on_start()
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def stop(self) -> None:
        self.player.stop()


def _h(text: str) -> str:
    return html.escape(text)


def explanation_html(k1: Key, k2: Key, settings) -> str:
    e = explain(k1, k2, settings.moves(), settings.energy_moves_in_key)
    mv = classify(k1, k2, settings.moves(), settings.energy_moves_in_key)
    col = theme.TIER_COLORS[mv.tier].name()
    return (
        f"<h3 style='margin:0'>{_h(e.title)}</h3>"
        f"<p style='color:{col}; margin-top:4px'><b>{_h(e.feel)}</b></p>"
        f"<p><b>Why:</b> {_h(e.why)}</p>"
        f"<p><b>The notes:</b> {_h(e.notes)}</p>"
        f"<p><b>Tip:</b> {_h(e.tip)}</p>"
    )


class LearnView(QWidget):
    def __init__(self, ctrl, preview, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.preview = preview
        self.synth = SynthPlayer(ctrl.data_dir / "learn_audio", self)
        self.synth.on_start = lambda: preview.player.pause()
        self.synth.audio.setVolume(preview.audio.volume())
        preview.volume.valueChanged.connect(lambda v: self.synth.audio.setVolume(v / 100))
        preview.playingChanged.connect(lambda tid: tid is not None and self.synth.stop())
        self.rng = random.Random()
        cfg = ctrl.config.get("learn", {}) or {}
        self.stats = quiz.Stats.from_dict(cfg.get("stats"))
        self.session = quiz.Stats()
        self.question: Optional[quiz.Question] = None
        self.answered = False
        self.from_key: Optional[Key] = Key(8, "A")
        self.to_key: Optional[Key] = None

        self.wheel = CamelotWheel()
        self.wheel.keyClicked.connect(self._wheel_clicked)
        self.wheel_hint = QLabel()
        self.wheel_hint.setObjectName("hint")
        self.wheel_hint.setWordWrap(True)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(6, 6, 6, 6)
        lv.addWidget(self.wheel, 1)
        lv.addWidget(self.wheel_hint)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_explore(), "Explore && listen")
        self.tabs.addTab(self._build_quiz(), "Quiz")
        self.tabs.currentChanged.connect(self._tab_changed)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([480, 720])
        self.split = split
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split)

        self.tabs.setCurrentIndex(int(cfg.get("tab", 0)))
        self._refresh_explore()
        self._tab_changed(self.tabs.currentIndex())

    # =============================================================== explore
    def _build_explore(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        basics = QLabel(WHEEL_BASICS)
        basics.setWordWrap(True)
        basics.setObjectName("hint")
        v.addWidget(basics)

        self.key_title = QLabel()
        f = self.key_title.font()
        f.setPointSizeF(f.pointSizeF() * 1.5)
        f.setBold(True)
        self.key_title.setFont(f)
        v.addWidget(self.key_title)
        self.key_notes = QLabel()
        self.key_notes.setObjectName("hint")
        v.addWidget(self.key_notes)

        hear = QHBoxLayout()
        hear.addWidget(QLabel("Hear this key:"))
        for label, kind, tip in (("▶ Scale", "scale", "The seven notes, up and down"),
                                 ("▶ Home chord", "chord", "The tonic chord: where the key feels at rest"),
                                 ("▶ Progression", "progression",
                                  "A typical dance-music chord loop in this key (major I–V–vi–IV, minor i–VI–III–VII)")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=kind: self._play_key(k))
            hear.addWidget(b)
        hear.addStretch(1)
        stop = QToolButton(text="■")
        stop.setToolTip("Stop")
        stop.clicked.connect(self.synth.stop)
        hear.addWidget(stop)
        v.addLayout(hear)

        v.addWidget(QLabel("<b>Moves from this key</b> — click one (or another key on the wheel) to see why it works"))
        self.moves_table = QTableWidget(0, 5)
        self.moves_table.setHorizontalHeaderLabels(["Move", "To", "Tier", "Mood", "Energy"])
        self.moves_table.verticalHeader().hide()
        self.moves_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.moves_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.moves_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.moves_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.moves_table.horizontalHeader().setStretchLastSection(True)
        self.moves_table.itemSelectionChanged.connect(self._move_row_selected)
        self.moves_table.setMaximumHeight(210)
        v.addWidget(self.moves_table)

        self.explain_box = QTextBrowser()
        self.explain_box.setOpenLinks(False)
        self.explain_box.setMinimumHeight(170)
        v.addWidget(self.explain_box, 1)
        pair = QHBoxLayout()
        self.hear_move = QPushButton("▶ Hear the move")
        self.hear_move.setToolTip("The first key's chords, then the second's")
        self.hear_move.clicked.connect(lambda: self._play_pair("transition"))
        self.hear_blend = QPushButton("▶ Hear them blended")
        self.hear_blend.setToolTip("Both keys' chords at once, like a long DJ blend: smooth moves sound "
                                   "consonant, clashes sound sour")
        self.hear_blend.clicked.connect(lambda: self._play_pair("blend"))
        pair.addWidget(self.hear_move)
        pair.addWidget(self.hear_blend)
        pair.addStretch(1)
        v.addLayout(pair)

        v.addWidget(QLabel("<b>Your tracks in these keys</b> — double-click to hear real music in the key"))
        self.tracks_list = QListWidget()
        self.tracks_list.setMaximumHeight(150)
        self.tracks_list.itemDoubleClicked.connect(self._track_activated)
        v.addWidget(self.tracks_list)
        return w

    def _wheel_clicked(self, k: Key) -> None:
        if self.tabs.currentIndex() == 1:
            self._quiz_wheel_click(k)
            return
        if self.from_key is None or k == self.from_key:
            self.from_key, self.to_key = k, None
        elif self.to_key == k:
            self.from_key, self.to_key = k, None  # click the compared key again: explore from it
        else:
            self.to_key = k
        self._refresh_explore()

    def _refresh_explore(self) -> None:
        k = self.from_key
        s = self.ctrl.settings
        moves = s.moves()
        tints = {}
        rows = []
        for t in ALL_KEYS:
            mv = classify(k, t, moves, s.energy_moves_in_key)
            if mv.tier != CLASH:
                tints[t] = tier_tint(mv.tier, strong=(t == k))
                rows.append((mv, t))
        counts = self._key_counts()
        self.wheel.set_state(k, self.to_key, tints, dimmed=True)
        self.wheel_hint.setText(
            "Click a key to explore from it; click a second key to compare. Coloured keys mix from the "
            "orange one: green = smooth, blue = energy move. Unlit keys clash.")
        self.key_title.setText(f"{k} · {key_name(k)}")
        tracks_here = counts.get(k, 0)
        self.key_notes.setText(f"Notes: {'  '.join(scale_names(k))}" +
                               (f"   ·   {tracks_here} track(s) in your library" if self.ctrl.library.tracks else ""))
        rows.sort(key=lambda r: ({"smooth": 0, "energy": 1}.get(r[0].tier, 2), r[0].name != "Same key"))
        self.moves_table.blockSignals(True)
        self.moves_table.setRowCount(len(rows))
        sel_row = -1
        for i, (mv, t) in enumerate(rows):
            e = "line breaks" if mv.energy is None else f"{mv.energy:+d}" if mv.energy else "0"
            for c, text in enumerate((mv.name, f"{t} ({short_name(t)})", TIER_NAMES[mv.tier], mv.label, e)):
                it = QTableWidgetItem(text)
                it.setData(Qt.UserRole, t)
                if c in (0, 2):
                    it.setForeground(theme.TIER_COLORS[mv.tier])
                self.moves_table.setItem(i, c, it)
            if t == self.to_key:
                sel_row = i
        self.moves_table.clearSelection()
        if sel_row >= 0:
            self.moves_table.selectRow(sel_row)
        self.moves_table.blockSignals(False)
        target = self.to_key
        if target is None:
            self.explain_box.setHtml(
                f"<p>Pick a move above, or click any key on the wheel, to see why {k} → that key works "
                "(or doesn't), which notes change, and how it feels.</p>")
        else:
            self.explain_box.setHtml(explanation_html(k, target, s))
        self.hear_move.setEnabled(target is not None)
        self.hear_blend.setEnabled(target is not None)
        self._fill_tracks([k] + ([target] if target else []))

    def _move_row_selected(self) -> None:
        items = self.moves_table.selectedItems()
        if items:
            t = items[0].data(Qt.UserRole)
            if t != self.from_key:
                self.to_key = t
                self._refresh_explore()

    def _key_counts(self) -> dict[Key, int]:
        out: dict[Key, int] = {}
        for t in self.ctrl.library.all_tracks:
            if t.key:
                out[t.key] = out.get(t.key, 0) + 1
        return out

    def _fill_tracks(self, keys: list[Key]) -> None:
        self.tracks_list.clear()
        lib = self.ctrl.library.all_tracks
        if not lib:
            self.tracks_list.addItem("Library not loaded.")
            return
        for k in keys:
            tracks = sorted((t for t in lib if t.key == k), key=lambda t: (-t.rating, t.artist.casefold()))
            head = QListWidgetItem(f"{k} · {key_name(k)} — {len(tracks)} track(s)")
            head.setFlags(Qt.ItemIsEnabled)
            head.setForeground(theme.ACCENT)
            self.tracks_list.addItem(head)
            for t in tracks[:40]:
                bpm = f"{t.bpm:.0f} BPM" if t.bpm else "? BPM"
                it = QListWidgetItem(f"   ▶  {t.artist} – {t.title}   ({bpm})")
                it.setData(Qt.UserRole, t)
                self.tracks_list.addItem(it)

    def _track_activated(self, it: QListWidgetItem) -> None:
        t = it.data(Qt.UserRole)
        if isinstance(t, Track):
            self.synth.stop()
            self.preview.preview(t)

    def _play_key(self, kind: str) -> None:
        k = self.from_key
        self.synth.play(f"{kind}:{k}", lambda: synth.key_reference(k, kind))

    def _play_pair(self, kind: str, k1: Optional[Key] = None, k2: Optional[Key] = None) -> None:
        k1, k2 = k1 or self.from_key, k2 or self.to_key
        if k1 is None or k2 is None:
            return
        fn = synth.transition if kind == "transition" else synth.blend
        self.synth.play(f"{kind}:{k1}:{k2}", lambda: fn(k1, k2))

    # ================================================================== quiz
    def _build_quiz(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        cfg = self.ctrl.config.get("learn", {}) or {}
        kinds_row = QHBoxLayout()
        kinds_row.addWidget(QLabel("Ask:"))
        self.kind_boxes: dict[str, QCheckBox] = {}
        chosen = cfg.get("kinds") or list(quiz.KINDS)
        for kind, label in quiz.KINDS.items():
            cb = QCheckBox(label)
            cb.setChecked(kind in chosen)
            cb.toggled.connect(self._save_cfg)
            self.kind_boxes[kind] = cb
            kinds_row.addWidget(cb)
        kinds_row.addStretch(1)
        v.addLayout(kinds_row)
        opts = QHBoxLayout()
        self.use_library = QCheckBox("Use tracks from my library")
        self.use_library.setToolTip("Ask about real pairs of your tracks (you can preview them)")
        self.use_library.setChecked(cfg.get("use_library", True))
        self.use_library.toggled.connect(self._save_cfg)
        self.practice = QCheckBox("Practise my weak moves more")
        self.practice.setChecked(cfg.get("practice", True))
        self.practice.toggled.connect(self._save_cfg)
        opts.addWidget(self.use_library)
        opts.addWidget(self.practice)
        opts.addStretch(1)
        self.score = QLabel()
        opts.addWidget(self.score)
        v.addLayout(opts)

        self.q_label = QLabel()
        self.q_label.setWordWrap(True)
        f = self.q_label.font()
        f.setPointSizeF(f.pointSizeF() * 1.35)
        self.q_label.setFont(f)
        self.q_label.setMinimumHeight(60)
        v.addWidget(self.q_label)

        listen = QHBoxLayout()
        self.q_blend = QPushButton("▶ Play the blend")
        self.q_blend.clicked.connect(lambda: self.question and self._play_pair("blend", self.question.k1, self.question.k2))
        self.q_move = QPushButton("▶ Play one, then the other")
        self.q_move.clicked.connect(
            lambda: self.question and self._play_pair("transition", self.question.k1, self.question.k2))
        self.q_track_a = QPushButton("▶ Track A")
        self.q_track_a.clicked.connect(lambda: self._preview_q(0))
        self.q_track_b = QPushButton("▶ Track B")
        self.q_track_b.clicked.connect(lambda: self._preview_q(1))
        for b in (self.q_blend, self.q_move, self.q_track_a, self.q_track_b):
            listen.addWidget(b)
        listen.addStretch(1)
        v.addLayout(listen)

        self.opt_grid = QGridLayout()
        self.opt_buttons: list[QPushButton] = []
        for i in range(4):
            b = QPushButton()
            b.setMinimumHeight(38)
            b.setShortcut(QKeySequence(str(i + 1)))
            b.clicked.connect(lambda _=False, i=i: self._answer(i))
            self.opt_buttons.append(b)
            self.opt_grid.addWidget(b, i // 2, i % 2)
        v.addLayout(self.opt_grid)

        self.feedback = QTextBrowser()
        self.feedback.setOpenLinks(False)
        v.addWidget(self.feedback, 1)
        nav = QHBoxLayout()
        self.hear_answer = QPushButton("▶ Hear the move")
        self.hear_answer.clicked.connect(
            lambda: self.question and self._play_pair("transition", self.question.k1, self.question.k2))
        self.next_btn = QPushButton("Next question  ⏎")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self.next_question)
        for seq in ("Return", "Enter"):
            sc = QShortcut(QKeySequence(seq), w)
            sc.activated.connect(lambda: self.answered and self.next_question())
        reset = QToolButton(text="Reset stats")
        reset.clicked.connect(self._reset_stats)
        nav.addWidget(self.hear_answer)
        nav.addStretch(1)
        nav.addWidget(reset)
        nav.addWidget(self.next_btn)
        v.addLayout(nav)

        self.stats_table = QTableWidget(0, 3)
        self.stats_table.setHorizontalHeaderLabels(["Move", "Right / asked", "Accuracy"])
        self.stats_table.verticalHeader().hide()
        self.stats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.stats_table.setMaximumHeight(170)
        v.addWidget(self.stats_table)
        return w

    def _quiz_config(self) -> quiz.QuizConfig:
        s = self.ctrl.settings
        kinds = [k for k, cb in self.kind_boxes.items() if cb.isChecked()] or [quiz.NAME_MOVE]
        return quiz.QuizConfig(kinds=kinds, moves=s.moves(), energy_moves_in_key=s.energy_moves_in_key,
                               notation=s.key_notation,
                               weights=self.stats.practice_weights() if self.practice.isChecked() else None)

    def next_question(self) -> None:
        tracks = self.ctrl.library.all_tracks if self.use_library.isChecked() else ()
        self.question = q = quiz.make_question(self.rng, self._quiz_config(), tracks)
        self.answered = False
        self.q_label.setText(_h(q.prompt))
        for i, b in enumerate(self.opt_buttons):
            b.setVisible(i < len(q.options))
            b.setEnabled(True)
            b.setStyleSheet("")
            if i < len(q.options):
                b.setText(f"{i + 1}.  {q.options[i]}")
        self.q_blend.setVisible(q.audio)
        self.q_move.setVisible(q.audio)
        self.q_track_a.setVisible(bool(q.tracks[0]) and not q.audio)
        self.q_track_b.setVisible(bool(q.tracks[1]) and not q.audio)
        self.hear_answer.setEnabled(False)
        self.next_btn.setEnabled(False)
        hint = "Click a key on the wheel to answer." if q.kind == quiz.FIND_KEY else ""
        self.feedback.setHtml(f"<p style='color:{theme.DIM.name()}'>Guess first, then see why. {hint}</p>")
        self._quiz_wheel()
        if q.audio:
            QTimer.singleShot(150, lambda: self._play_pair("blend", q.k1, q.k2))

    def _quiz_wheel(self) -> None:
        q = self.question
        if q is None:
            self.wheel.set_state(None, None, {}, dimmed=False)
            return
        if not self.answered:
            show_to = q.kind not in (quiz.FIND_KEY, quiz.EAR)
            self.wheel.set_state(q.k1 if q.kind != quiz.EAR else None, q.k2 if show_to else None, {}, dimmed=False)
            self.wheel_hint.setText("The orange key is where you are." if q.kind != quiz.EAR else
                                    "Ear training: listen first. The keys are revealed after you answer.")
            return
        s = self.ctrl.settings
        tints = {}
        for t in ALL_KEYS:
            mv = classify(q.k1, t, s.moves(), s.energy_moves_in_key)
            if mv.tier != CLASH:
                tints[t] = tier_tint(mv.tier, strong=(t == q.k1))
        self.wheel.set_state(q.k1, q.k2, tints, dimmed=True)
        self.wheel_hint.setText("Answer shown in white. Coloured keys mix from the orange one.")

    def _quiz_wheel_click(self, k: Key) -> None:
        q = self.question
        if q is None or self.answered:
            return
        if q.kind == quiz.FIND_KEY:
            if str(k) in q.options:
                self._answer(q.options.index(str(k)))
            else:
                self._answer(-1, picked=str(k))

    def _answer(self, i: int, picked: Optional[str] = None) -> None:
        q = self.question
        if q is None or self.answered:
            return
        self.answered = True
        ok = q.check(i)
        self.stats.record(q.move, ok)
        self.session.record(q.move, ok)
        for j, b in enumerate(self.opt_buttons):
            b.setEnabled(False)
            if j == q.answer:
                b.setStyleSheet(f"background: #22492a; border: 1px solid {theme.TIER_COLORS['smooth'].name()};")
            elif j == i:
                b.setStyleSheet(f"background: {theme.CLASH_BG.name()}; border: 1px solid {theme.CLASH.name()};")
        verdict = ("<p style='font-size:large; color:#5cbf5c'><b>✓ Right!</b></p>" if ok else
                   f"<p style='font-size:large; color:#e05050'><b>✗ Not quite</b> — "
                   f"{'you picked ' + _h(picked) + '; ' if picked else ''}the answer is "
                   f"<b>{_h(q.options[q.answer])}</b>.</p>")
        reveal = ""
        if q.kind == quiz.EAR or (q.tracks[0] is None and q.kind == quiz.FIND_KEY):
            reveal = f"<p>The keys were <b>{q.k1}</b> ({key_name(q.k1)}) and <b>{q.k2}</b> ({key_name(q.k2)}).</p>"
        self.feedback.setHtml(verdict + reveal + explanation_html(q.k1, q.k2, self.ctrl.settings))
        self.hear_answer.setEnabled(True)
        self.next_btn.setEnabled(True)
        self.next_btn.setFocus()
        self._quiz_wheel()
        self._update_score()
        self._save_cfg()

    def _preview_q(self, idx: int) -> None:
        if self.question and self.question.tracks[idx]:
            self.synth.stop()
            self.preview.preview(self.question.tracks[idx])

    def _update_score(self) -> None:
        s = self.session
        pct = f" ({100 * s.correct / s.asked:.0f}%)" if s.asked else ""
        self.score.setText(f"This session: {s.correct}/{s.asked}{pct} · streak {s.streak} · "
                           f"best ever {self.stats.best_streak}")
        rows = sorted(self.stats.per_move.items(), key=lambda kv: (kv[1][0] / kv[1][1] if kv[1][1] else 1, kv[0]))
        self.stats_table.setRowCount(len(rows))
        for r, (m, (c, n)) in enumerate(rows):
            acc = c / n if n else 0
            items = [QTableWidgetItem(m), QTableWidgetItem(f"{c} / {n}"), QTableWidgetItem(f"{100 * acc:.0f}%")]
            items[2].setForeground(theme.BAND_COLORS["safe" if acc >= 0.8 else "caution" if acc >= 0.5 else "danger"])
            for c_, it in enumerate(items):
                self.stats_table.setItem(r, c_, it)

    def _reset_stats(self) -> None:
        self.stats = quiz.Stats()
        self.session = quiz.Stats()
        self._update_score()
        self._save_cfg()

    def _save_cfg(self) -> None:
        self.ctrl.config.set("learn", {
            "stats": self.stats.to_dict(),
            "kinds": [k for k, cb in self.kind_boxes.items() if cb.isChecked()],
            "use_library": self.use_library.isChecked(),
            "practice": self.practice.isChecked(),
            "tab": self.tabs.currentIndex() if hasattr(self, "tabs") else 0,
        })

    # ================================================================ common
    def _tab_changed(self, i: int) -> None:
        if i == 1:
            if self.question is None:
                self.next_question()
            else:
                self._quiz_wheel()
            self._update_score()
        else:
            self._refresh_explore()
        self._save_cfg()

    def library_changed(self) -> None:
        if self.tabs.currentIndex() == 0:
            self._refresh_explore()


def tier_tint(tier: str, strong: bool = False) -> QColor:
    """Wheel fill for a key reachable by a move of this tier."""
    c = QColor(theme.TIER_COLORS[tier])
    return c.darker(115) if strong else c.darker(170)
