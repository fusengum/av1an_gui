"""設定画面の 1 ページを定義（shared.web.apps.WebApp の settings_sections と同じ形式）から作る

各項目は「名前 ⓘ / 入力欄 / その下に短い説明」の形で並べる。advanced の項目は「詳しい設定」に畳む。
値は apply() を呼んだときにだけ設定オブジェクトに書く（保存は呼び出し側）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)


def get_value(s, key: str):
    if "." in key:
        top, sub = key.split(".", 1)
        return (getattr(s, top, None) or {}).get(sub, "")
    return getattr(s, key, None)


def set_value(s, key: str, v) -> None:
    if "." in key:
        top, sub = key.split(".", 1)
        d = dict(getattr(s, top, None) or {})
        d[sub] = v
        setattr(s, top, d)
    else:
        setattr(s, key, v)


def resolve_key(s, key: str) -> str:
    """extra.{encoder} → extra.x265（同じ設定のその項目の値で置き換える）"""
    while "{" in key and "}" in key:
        a, rest = key.split("{", 1)
        name, b = rest.split("}", 1)
        key = f"{a}{get_value(s, name)}{b}"
    return key


class SchemaPage(QWidget):
    def __init__(self, section: dict, settings, tips: dict | None = None, actions: dict | None = None,
                 extra: QWidget | None = None, parent=None):
        """actions: ボタンの id → 押したときの処理。extra: ページの下に足すウィジェット"""
        super().__init__(parent)
        self.section, self.s, self.tips = section, settings, tips or {}
        self.inputs: dict[str, tuple[dict, QWidget, list[QWidget]]] = {}   # key → (定義, 入力欄, 表示を切り替える部品)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(area)
        body = QWidget()
        area.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        if section.get("note"):
            n = QLabel(section["note"])
            n.setWordWrap(True)
            n.setObjectName("muted")
            root.addWidget(n)

        fields = [f for f in section.get("fields", []) if not f.get("main")]
        basic = [f for f in fields if not f.get("advanced")]
        adv = [f for f in fields if f.get("advanced")]
        root.addLayout(self._grid(basic))
        for a in section.get("actions", []):
            if actions and a["id"] in actions:
                b = QPushButton(a["label"])
                b.clicked.connect(actions[a["id"]])
                h = QHBoxLayout()
                h.addWidget(b)
                h.addStretch(1)
                root.addLayout(h)
        if extra is not None:
            root.addWidget(extra)
        if adv:
            t = QToolButton()
            t.setText("詳しい設定")
            t.setCheckable(True)
            t.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            t.setArrowType(Qt.RightArrow)
            t.setStyleSheet("QToolButton { border: none; color: #9aa3b2; padding: 4px 0; }")
            box = QWidget()
            box.setLayout(self._grid(adv))
            box.setVisible(False)
            t.toggled.connect(lambda on: (box.setVisible(on), t.setArrowType(Qt.DownArrow if on else Qt.RightArrow)))
            root.addWidget(t)
            root.addWidget(box)
        root.addStretch(1)
        self._update_visibility()

    # ------------------------------------------------------------------
    def _grid(self, fields: list[dict]) -> QGridLayout:
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(2)
        g.setColumnStretch(1, 1)
        row = 0
        for f in fields:
            key = resolve_key(self.s, f["key"])
            tip = self.tips.get(f.get("tip", ""), "")
            lb = QLabel(f["label"] + (" ⓘ" if tip else ""))
            lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
            if tip:
                lb.setToolTip(tip)
                lb.setCursor(Qt.WhatsThisCursor)
            w, holder = self._input(f, get_value(self.s, key))
            if tip:
                w.setToolTip(tip)
            parts = [lb, holder]
            g.addWidget(lb, row, 0, Qt.AlignTop | Qt.AlignLeft)
            narrow = f["type"] in ("bool", "int", "number")
            g.addWidget(holder, row, 1, Qt.AlignLeft if narrow else Qt.Alignment())
            row += 1
            if f.get("hint"):
                h = QLabel(f["hint"])
                h.setWordWrap(True)
                h.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                h.setObjectName("muted")
                h.setStyleSheet("font-size: 9pt; margin-bottom: 8px;")
                g.addWidget(h, row, 1)
                parts.append(h)
                row += 1
            else:
                holder.setContentsMargins(0, 0, 0, 8)
            self.inputs[key] = (f, w, parts)
        return g

    def _input(self, f: dict, v):
        t = f["type"]
        if t == "select":
            w = QComboBox()
            for k, label in (f.get("choices") or {}).items():
                w.addItem(str(label), k)
            w.setCurrentIndex(max(0, w.findData(v)))
            w.currentIndexChanged.connect(self._update_visibility)
            return w, w
        if t == "bool":
            w = QCheckBox()
            w.setChecked(bool(v))
            w.toggled.connect(self._update_visibility)
            return w, w
        if t in ("int", "number"):
            w = QSpinBox() if t == "int" else QDoubleSpinBox()
            if t == "number":
                step = float(f.get("step", 1))
                w.setDecimals(0 if step >= 1 else 1)
                w.setSingleStep(step)
            w.setRange(f.get("min", 0), f.get("max", 10 ** 6))
            if f.get("suffix"):
                w.setSuffix(" " + f["suffix"])
            if f.get("special"):
                w.setSpecialValueText(f["special"])
            w.setValue(int(v or 0) if t == "int" else float(v or 0))
            w.setMaximumWidth(180)
            w.valueChanged.connect(self._update_visibility)
            return w, w
        w = QLineEdit(str(v or ""))
        w.setPlaceholderText(f.get("placeholder", ""))
        if t not in ("dir", "file"):
            return w, w
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(w, 1)
        b = QPushButton("参照…")
        b.clicked.connect(lambda: self._browse(w, t == "dir", f["label"]))
        h.addWidget(b)
        return w, holder

    def _browse(self, edit: QLineEdit, is_dir: bool, title: str):
        start = edit.text() or str(Path.home())
        if is_dir:
            d = QFileDialog.getExistingDirectory(self, title, start)
        else:
            d, _ = QFileDialog.getOpenFileName(self, title, start, "実行ファイル (*.exe);;すべて (*)")
        if d:
            edit.setText(str(Path(d)))

    def _current(self, key: str):
        if key in self.inputs:
            return self._read(*self.inputs[key][:2])
        return get_value(self.s, key)

    @staticmethod
    def _read(f: dict, w: QWidget):
        t = f["type"]
        if t == "select":
            return w.currentData()
        if t == "bool":
            return w.isChecked()
        if t in ("int", "number"):
            return w.value()
        return w.text().strip().strip('"')

    def _update_visibility(self, *_):
        for f, _w, parts in self.inputs.values():
            cond = f.get("show_if") or {}
            ok = all(self._current(k) in (v if isinstance(v, list) else [v]) for k, v in cond.items())
            for p in parts:
                p.setVisible(ok)

    def apply(self) -> None:
        for key, (f, w, _parts) in self.inputs.items():
            set_value(self.s, key, self._read(f, w))
