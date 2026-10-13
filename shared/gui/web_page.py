"""設定画面の「Web 画面」タブ（LAN に公開するか・ポート・ログオン時の自動起動）"""
from __future__ import annotations

import socket

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QGridLayout, QLabel, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout,
                               QWidget)

from shared.core.config import IS_WIN
from shared.web import config as W


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))           # 実際には送らない（経路から自分の IP を知るだけ）
            return s.getsockname()[0]
    except OSError:
        return "この PC の IP"


class WebPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cfg = W.WebConfig.load()
        actual = W.autostart_state()
        if actual is not None:
            self.cfg.autostart = actual

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
        n = QLabel("キュー（エンコードの順番待ち）と進捗は、この PC で動く Web サーバーが受け持ちます。"
                   "この画面と Web 画面には同じキューが出ます。LAN に公開しない場合も、サーバーはこの PC の中だけで動きます。")
        n.setWordWrap(True)
        n.setObjectName("muted")
        root.addWidget(n)

        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(2)
        g.setColumnStretch(1, 1)
        self.cb_lan = QCheckBox("他の PC のブラウザから使えるようにする")
        self.cb_lan.setChecked(self.cfg.lan)
        self.sp_port = QSpinBox()
        self.sp_port.setRange(1024, 65535)
        self.sp_port.setValue(self.cfg.port)
        self.sp_port.setMaximumWidth(140)
        self.cb_auto = QCheckBox("ログオン時に自動で起動する" if IS_WIN else "PC の起動時に自動で起動する")
        self.cb_auto.setChecked(self.cfg.autostart)
        rows = [
            ("Web 画面", self.cb_lan, "認証が無いため、LAN の中だけで使ってください。オンにすると、"
                                      "ファイアウォールの許可のために管理者の確認が 1 回出ます。"),
            ("ポート", self.sp_port, "ほかのソフトと重ならない番号にします（既定 8765）。"),
            ("自動起動", self.cb_auto, "オフでも、デスクトップ版を開けば自動で起動します。"),
        ]
        if not IS_WIN:
            self.cb_auto.setEnabled(False)
            rows[2] = ("自動起動", self.cb_auto, f"Linux では systemd のサービス（{W.service_name()}）で管理しています。"
                                                f"sudo systemctl enable / disable {W.service_name()} で切り替えます。")
        r = 0
        for label, w, hint in rows:
            lb = QLabel(label)
            lb.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
            g.addWidget(lb, r, 0, Qt.AlignTop | Qt.AlignLeft)
            g.addWidget(w, r, 1, Qt.AlignLeft)
            h = QLabel(hint)
            h.setWordWrap(True)
            h.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            h.setObjectName("muted")
            h.setStyleSheet("font-size: 9pt; margin-bottom: 8px;")
            g.addWidget(h, r + 1, 1)
            r += 2
        root.addLayout(g)
        self.url = QLabel()
        self.url.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.url.setOpenExternalLinks(True)
        root.addWidget(self.url)
        root.addStretch(1)
        for w in (self.cb_lan, self.sp_port):
            (w.toggled if isinstance(w, QCheckBox) else w.valueChanged).connect(self._show_url)
        self._show_url()

    def _show_url(self, *_):
        host = lan_ip() if self.cb_lan.isChecked() else "127.0.0.1"
        u = f"http://{host}:{self.sp_port.value()}/"
        self.url.setText(f'保存後のアドレス: <a href="{u}" style="color:#9cc0ff">{u}</a>')

    # ------------------------------------------------------------------
    def wanted(self) -> W.WebConfig:
        return W.WebConfig(lan=self.cb_lan.isChecked(), port=self.sp_port.value(), autostart=self.cb_auto.isChecked())

    def needs_restart(self) -> bool:
        w = self.wanted()
        return (w.lan, w.port) != (self.cfg.lan, self.cfg.port)

    def apply(self, client) -> list[str]:
        """保存して反映する。戻り値は利用者向けの結果。失敗したものは「（失敗）…」で返す"""
        w, old = self.wanted(), self.cfg
        if w == old:
            return []
        msgs = []
        w.save()
        if IS_WIN and w.autostart != old.autostart:
            msgs.append(self._try(lambda: W.set_autostart(w.autostart)))
        if w.lan and (not old.lan or w.port != old.port):
            msgs.append(self._try(lambda: W.open_firewall(w.port)))
        if (w.lan, w.port) != (old.lan, old.port):
            msgs.append(self._try(lambda: (client.restart(), "Web サーバーを新しい設定で起動し直しました")[1]))
        self.cfg = w
        return msgs

    @staticmethod
    def _try(fn) -> str:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            return f"（失敗）{e}"
