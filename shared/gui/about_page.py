"""設定画面の「バージョン情報」タブ（バージョン・作者・ライセンス・更新・謝辞）

表示する内容はツールの app.json の "about"（author / homepage / license / credits_note / credits）。"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)


def _link(url: str, text: str = "") -> str:
    return f'<a href="{html.escape(url)}" style="color:#9cc0ff">{html.escape(text or url)}</a>'


class AboutPage(QWidget):
    def __init__(self, name: str, version: str, desc: str, about: dict, check=None, start_update=None,
                 update_info: dict | None = None, parent=None):
        """check: () -> 更新の確認結果（shared.update.check の形）。start_update: 更新を始める（画面を閉じてよい）"""
        super().__init__(parent)
        self.check_fn, self.start_update = check, start_update
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

        title = QLabel(f'<span style="font-size:16pt;font-weight:700">{html.escape(name)}</span>'
                       f'&nbsp;&nbsp;<span style="color:#9aa3b2">バージョン {html.escape(version)}</span>')
        root.addWidget(title)
        if desc:
            d = QLabel(desc)
            d.setObjectName("muted")
            root.addWidget(d)
        rows = [("作者", html.escape(about.get("author", ""))),
                ("ホームページ", _link(about["homepage"]) if about.get("homepage") else ""),
                ("ライセンス", html.escape(about.get("license", "")) or "（未設定）")]
        info = QLabel("<table cellspacing=4>" + "".join(
            f"<tr><td style='color:#9aa3b2;padding-right:14px'>{k}</td><td>{v}</td></tr>" for k, v in rows if v) +
            "</table>")
        info.setTextInteractionFlags(Qt.TextBrowserInteraction)
        info.setOpenExternalLinks(True)
        root.addWidget(info)

        # 更新
        g = QGroupBox("更新")
        gl = QVBoxLayout(g)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.status.setOpenExternalLinks(True)
        gl.addWidget(self.status)
        h = QHBoxLayout()
        self.btn_check = QPushButton("更新を確認")
        self.btn_check.clicked.connect(self._check)
        self.btn_update = QPushButton("更新する")
        self.btn_update.setObjectName("primary")
        self.btn_update.clicked.connect(lambda: self.start_update and self.start_update())
        h.addWidget(self.btn_check)
        h.addWidget(self.btn_update)
        h.addStretch(1)
        gl.addLayout(h)
        note = QLabel("新しいバージョンが出ていると、メイン画面の見出しにも「更新」が出ます（開いたときと 1 日 1 回確認します）。"
                      "設定・キュー・取得した外部ツールはそのまま残ります。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        gl.addWidget(note)
        g.setVisible(check is not None)
        root.addWidget(g)
        self._show(update_info or {}, version)

        # 謝辞
        credits = about.get("credits") or []
        if credits:
            g2 = QGroupBox("謝辞・使っているソフトウェア")
            l2 = QVBoxLayout(g2)
            if about.get("credits_note"):
                n = QLabel(about["credits_note"])
                n.setWordWrap(True)
                l2.addWidget(n)
            t = QTableWidget(len(credits), 3)
            t.setHorizontalHeaderLabels(["名前", "使いどころ", "ライセンス"])
            t.verticalHeader().setVisible(False)
            t.setEditTriggers(QTableWidget.NoEditTriggers)
            t.setSelectionMode(QTableWidget.NoSelection)
            t.setWordWrap(True)
            for i, c in enumerate(credits):
                lb = QLabel(_link(c["url"], c["name"]) if c.get("url") else html.escape(c["name"]))
                lb.setOpenExternalLinks(True)
                lb.setContentsMargins(6, 0, 6, 0)
                t.setCellWidget(i, 0, lb)
                t.setItem(i, 1, QTableWidgetItem(c.get("role", "")))
                t.setItem(i, 2, QTableWidgetItem(c.get("license", "")))
            hh = t.horizontalHeader()
            hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            hh.setSectionResizeMode(1, QHeaderView.Stretch)
            hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            t.setMinimumHeight(min(30 * len(credits) + 34, 520))
            l2.addWidget(t)
            root.addWidget(g2)
        root.addStretch(1)

    def _show(self, u: dict, version: str = ""):
        cur = u.get("current") or version
        if u.get("available"):
            text = f"新しいバージョン <b>{html.escape(u['latest'])}</b> があります（現在のバージョン {html.escape(cur)}）。"
            if u.get("url"):
                text += f"　{_link(u['url'], '変更内容を見る')}"
        elif u.get("error"):
            text = f"現在のバージョン {html.escape(cur)}　—　{html.escape(u['error'])}"
        elif u.get("latest"):
            text = f"現在のバージョン {html.escape(cur)}　—　最新です"
        else:
            text = f"現在のバージョン {html.escape(cur)}　—　「更新を確認」で、新しいバージョンがあるか確かめます"
        self.status.setText(text)
        self.btn_update.setVisible(bool(u.get("available")) and self.start_update is not None)

    def _check(self):
        if not self.check_fn:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._show(self.check_fn())
        finally:
            QApplication.restoreOverrideCursor()
