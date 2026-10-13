"""設定画面（タブ: ツールごとの設定ページ / Web 画面 / 外部ツールの検出と自動ダウンロード・フォルダ指定）"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from shared.core import tools
from shared.core.config import EXE

from .tips import TIPS


class InstallWorker(QObject):
    progress = Signal(str, float)
    finished = Signal(str)          # エラーメッセージ（成功時は空）

    def __init__(self, settings, names: list[str] | None, tool_list=None):
        super().__init__()
        self.settings = settings
        self.names = names
        self.tool_list = tool_list

    def run(self):
        try:
            cb = lambda m, f: self.progress.emit(m, f)  # noqa: E731
            if self.names is None:
                tools.install_missing(self.settings, cb, tool_list=self.tool_list)
            else:
                done = set()
                for n in self.names:
                    fn = tools.INSTALLERS[n]
                    if fn not in done:
                        fn(cb)
                        done.add(fn)
                cb("完了", 1.0)
            self.finished.emit("")
        except Exception as e:  # noqa: BLE001
            self.finished.emit(str(e))


class SettingsDialog(QDialog):
    changed = Signal()

    def __init__(self, settings, parent=None, tool_list=None, tips: dict | None = None, extra_status=None,
                 folders: list[dict] | None = None, sections: list[dict] | None = None, actions: dict | None = None,
                 section_extra=None, client=None, tab: str = ""):
        """tool_list: tools.check_all 形式のツール一覧。extra_status: 追加表示する行（文字列リスト）を返す関数
        folders: 手動で指定するフォルダ（自動取得できないツールなど）の定義
            {"key": 設定名, "title": 見出し, "tip": 説明, "placeholder": 例, "note": 補足,
             "status": (settings) -> (ok: bool, 説明)}
        sections: 設定ページの定義（WebApp.settings_sections と同じ形式）。ページごとにタブを作る
        actions: ページのボタンの id → 処理。section_extra: (ページ id) -> ページの下に足すウィジェット or None
        client: shared.web.client.Client。渡すと「Web 画面」タブを出す。tab: 最初に開くタブの id"""
        super().__init__(parent)
        self.s = settings
        self.tool_list = tool_list or []
        self.folders = folders or []
        self.tips = {**TIPS, **(tips or {})}
        self.extra_status = extra_status
        self.setWindowTitle("設定")
        self.resize(860, 600 + 100 * len(self.folders))
        self._thread: QThread | None = None
        self.client = client
        self.pages = []

        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        from .settings_form import SchemaPage
        for sec in sections or []:
            if not [f for f in sec.get("fields", []) if not f.get("main")]:
                continue                          # 全部メイン画面にある
            page = SchemaPage(sec, settings, self.tips, actions,
                              section_extra(sec["id"]) if section_extra else None)
            self.pages.append(page)
            self.tabs.addTab(page, sec["title"])
            page.setObjectName(sec["id"])
        self.web_page = None
        if client is not None:
            from .web_page import WebPage
            self.web_page = WebPage()
            self.web_page.setObjectName("web")
            self.tabs.addTab(self.web_page, "Web 画面")

        tools_page = QWidget()
        tools_page.setObjectName("tools")
        self.tabs.addTab(tools_page, "外部ツール")
        root = QVBoxLayout(tools_page)

        # 手動で指定するフォルダ
        self.folder_rows = []
        for fd in self.folders:
            g = QGroupBox(fd["title"])
            g.setToolTip(fd.get("tip", ""))
            gl = QGridLayout(g)
            edit = QLineEdit(getattr(settings, fd["key"], ""))
            edit.setPlaceholderText(fd.get("placeholder", ""))
            edit.editingFinished.connect(lambda fd=fd, e=edit: self._folder_edited(fd, e))
            b = QPushButton("参照…")
            b.clicked.connect(lambda _=False, fd=fd, e=edit: self._browse_folder(fd, e))
            st = QLabel()
            gl.addWidget(QLabel("フォルダ"), 0, 0)
            gl.addWidget(edit, 0, 1)
            gl.addWidget(b, 0, 2)
            gl.addWidget(st, 1, 1, 1, 2)
            if fd.get("note"):
                note = QLabel(fd["note"])
                note.setObjectName("muted")
                note.setWordWrap(True)
                gl.addWidget(note, 2, 1, 1, 2)
            root.addWidget(g)
            self.folder_rows.append((fd, edit, st))

        # ツール
        g2 = QGroupBox("外部ツール")
        self.grid = QGridLayout(g2)
        self.grid.setColumnStretch(2, 1)
        root.addWidget(g2)
        self.rows: dict[str, tuple[QLabel, QLabel, QPushButton, QPushButton]] = {}
        for i, (name, label, *_rest) in enumerate(self.tool_list):
            st = QLabel()
            info = QLabel()
            info.setObjectName("muted")
            info.setTextInteractionFlags(Qt.TextSelectableByMouse)
            pick = QPushButton("指定…")
            pick.clicked.connect(lambda _=False, n=name: self._pick_tool(n))
            dl = QPushButton("取得")
            dl.clicked.connect(lambda _=False, n=name: self._install([n]))
            dl.setVisible(name in tools.INSTALLERS)
            nl = QLabel(label + " ⓘ")
            nl.setToolTip(self.tips.get("ffmpeg" if name in ("ffmpeg", "ffprobe") else name, ""))
            self.grid.addWidget(nl, i, 0)
            self.grid.addWidget(st, i, 1)
            self.grid.addWidget(info, i, 2)
            self.grid.addWidget(pick, i, 3)
            self.grid.addWidget(dl, i, 4)
            self.rows[name] = (st, info, pick, dl)

        # 一括セットアップ
        bar = QHBoxLayout()
        self.setup_btn = QPushButton("不足ツールを自動セットアップ")
        self.setup_btn.setObjectName("primary")
        self.setup_btn.setToolTip("見つからないツール（" + "・".join(t[1] for t in self.tool_list) + "）を"
                                  "公式の配布元から取得し、アプリのフォルダ内に置きます")
        self.setup_btn.clicked.connect(lambda: self._install(None))
        self.pbar = QProgressBar()
        self.pbar.setVisible(False)
        self.pmsg = QLabel()
        self.pmsg.setObjectName("muted")
        bar.addWidget(self.setup_btn)
        bar.addWidget(self.pbar, 1)
        root.addLayout(bar)
        root.addWidget(self.pmsg)
        self.extra = QLabel()
        self.extra.setObjectName("muted")
        self.extra.setVisible(extra_status is not None)
        root.addWidget(self.extra)
        loc = QLabel(f"取得したツールの保存先: {tools.BIN_DIR}")
        loc.setObjectName("muted")
        root.addWidget(loc)
        root.addStretch(1)

        h = QHBoxLayout()
        h.addStretch(1)
        if self.pages or self.web_page:
            cancel = QPushButton("キャンセル")
            cancel.clicked.connect(self.reject)
            ok = QPushButton("保存")
            ok.setObjectName("primary")
            ok.clicked.connect(self._save)
            h.addWidget(cancel)
            h.addWidget(ok)
        else:
            close = QPushButton("閉じる")
            close.clicked.connect(self.accept)
            h.addWidget(close)
        outer.addLayout(h)
        for i in range(self.tabs.count()):
            if tab and self.tabs.widget(i).objectName() == tab:
                self.tabs.setCurrentIndex(i)

        self.refresh()

    def _save(self):
        if self.web_page and self.web_page.needs_restart() and self.client:
            try:
                busy = any(j["state"] in ("queued", "running") for j in self.client.state()["jobs"])
            except Exception:  # noqa: BLE001  サーバーが動いていなければ、そのまま反映できる
                busy = False
            if busy:
                QMessageBox.warning(self, "設定", "エンコード中（または待機中）のジョブがあるため、Web 画面の待ち受けを"
                                                "変えられません。終わってから、もう一度保存してください。")
                self.tabs.setCurrentWidget(self.web_page)
                return
        for p in self.pages:
            p.apply()
        self.s.save()
        if self.web_page:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                msgs = self.web_page.apply(self.client)
            finally:
                QApplication.restoreOverrideCursor()
            if msgs:
                QMessageBox.information(self, "設定", "\n".join(msgs))
        self.accept()

    # ------------------------------------------------------------------
    def refresh(self):
        sts = {s.name: s for s in tools.check_all(self.s, self.tool_list, include_extra=False)}
        if self.extra_status:
            try:
                self.extra.setText("\n".join(self.extra_status()))
            except Exception as e:  # noqa: BLE001
                self.extra.setText(str(e))
        for fd, _edit, lbl in self.folder_rows:
            try:
                ok, msg = fd["status"](self.s)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, str(e)
            lbl.setText(("✔ " if ok else "✖ ") + msg)
            lbl.setObjectName("chip_ok" if ok else "chip_ng")
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)
        for name, (st, info, _pick, dl) in self.rows.items():
            t = sts[name]
            st.setText("✔" if t.ok else ("✖" if t.required else "—"))
            st.setObjectName("chip_ok" if t.ok else "chip_ng")
            st.style().unpolish(st)
            st.style().polish(st)
            info.setText(f"{t.path}\n{t.version}" if t.ok else t.note or "見つかりません")
            dl.setText("更新" if t.ok else "取得")
        self.changed.emit()

    def _folder_edited(self, fd, edit):
        setattr(self.s, fd["key"], edit.text().strip().strip('"'))
        self.s.save()
        self.refresh()

    def _browse_folder(self, fd, edit):
        d = QFileDialog.getExistingDirectory(self, fd["title"], edit.text() or str(Path.home()))
        if d:
            edit.setText(str(Path(d)))
            self._folder_edited(fd, edit)

    def _pick_tool(self, name: str):
        flt = f"{name} ({name}{EXE});;すべて (*)" if EXE else "すべて (*)"
        f, _ = QFileDialog.getOpenFileName(self, f"{name} の場所", "", flt)
        if f:
            setattr(self.s, name, str(Path(f)))
            self.s.save()
            self.refresh()

    def _install(self, names: list[str] | None):
        if self._thread:
            return
        self.setup_btn.setEnabled(False)
        for _st, _i, _p, dl in self.rows.values():
            dl.setEnabled(False)
        self.pbar.setVisible(True)
        self.pbar.setRange(0, 0)
        self._thread = QThread(self)
        self._worker = InstallWorker(self.s, names, self.tool_list)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def _on_progress(self, msg: str, frac: float):
        self.pmsg.setText(msg)
        if frac < 0:
            self.pbar.setRange(0, 0)
        else:
            self.pbar.setRange(0, 1000)
            self.pbar.setValue(int(frac * 1000))

    def _on_finished(self, err: str):
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self.setup_btn.setEnabled(True)
        for _st, _i, _p, dl in self.rows.values():
            dl.setEnabled(True)
        self.pbar.setVisible(False)
        self.pmsg.setText(f"エラー: {err}" if err else "セットアップ完了")
        self.refresh()

    def closeEvent(self, e):
        if self._thread:
            e.ignore()
            return
        super().closeEvent(e)
