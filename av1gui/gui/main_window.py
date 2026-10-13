"""av1an_gui メインウィンドウ
キュー・進捗は Web 画面のサーバーが持つ（shared.web.client）。この画面で追加した行は「エンコード開始」で
サーバーのキューに送られ、Web 画面から追加したものも含めて同じキューを表示する。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QSpinBox,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from shared.core import av1an as A
from shared.core import probe as P
from shared.core import tools
from shared.gui.settings_dialog import SettingsDialog
from shared.web.client import Client, ServerError

from .. import APP_NAME, __version__
from .. import encoders as E
from ..config import AvSettings
from ..encode import AUDIO_LABELS, CHUNK_LABELS, CROP_LABELS, RES_LABELS, SC_LABELS, AvJob, describe_plan
from ..env import TOOL_LIST
from .help import TIPS

VIDEO_EXTS = {".mkv", ".m2ts", ".mts", ".ts", ".mp4", ".m4v", ".mov", ".webm", ".avi", ".hevc", ".y4m"}
COL_FILE, COL_VINFO, COL_CONTENT, COL_RES, COL_CROP, COL_AUDIO, COL_STATUS, COL_PROG = range(8)
PER_ROW = ("content", "res", "crop", "audio")
ST_PROBING, ST_READY, ST_ERROR, ST_RUNNING, ST_DONE, ST_FAILED, ST_CANCELLED = (
    "解析中…", "待機", "対象外", "処理中", "完了", "失敗", "中止")
ST_QUEUED = "queued"                     # サーバーのキューで順番待ち
SERVER_ST = {"queued": ST_QUEUED, "running": ST_RUNNING, "done": ST_DONE, "failed": ST_FAILED,
             "cancelled": ST_CANCELLED}
KIND = __name__.split(".")[0]            # サーバーでのツール名（パッケージ名）


@dataclass
class Row:
    path: Path
    info: P.MediaInfo | None = None
    state: str = ST_PROBING
    error: str = ""
    output: Path | None = None
    w: dict = field(default_factory=dict)
    jid: str | None = None               # サーバーのジョブ ID（キューに送った後）
    sent: float = 0.0                    # キューに送った時刻（それより前に取った状態では上書きしない）


class ProbeSig(QObject):
    done = Signal(int, object, str)


class ProbeTask(QRunnable):
    def __init__(self, rid, path, ffprobe, sig):
        super().__init__()
        self.rid, self.path, self.ffprobe, self.sig = rid, path, ffprobe, sig

    def run(self):
        try:
            self.sig.done.emit(self.rid, P.probe(self.path, self.ffprobe), "")
        except Exception as e:  # noqa: BLE001
            self.sig.done.emit(self.rid, None, str(e))


class PollSig(QObject):
    done = Signal(object, object, str, float)     # 状態, {jid: (ログ行, 次の位置)}, エラー, 問い合わせた時刻


class PollTask(QRunnable):
    """サーバーのキューとログを取ってくる（画面を止めないよう別スレッドで）"""
    def __init__(self, client: Client, log_pos: dict, sig: PollSig):
        super().__init__()
        self.client, self.log_pos, self.sig = client, log_pos, sig

    def run(self):
        t = time.time()
        try:
            self.client.ensure()
            st = self.client.state()
            logs = {}
            for j in st["jobs"]:
                pos = self.log_pos.get(j["id"])
                if j["kind"] == KIND and pos is not None and j["log_lines"] > pos:
                    r = self.client.log(j["id"], pos)
                    logs[j["id"]] = (r["lines"], r["next"])
            self.sig.done.emit(st, logs, "", t)
        except Exception as e:  # noqa: BLE001
            self.sig.done.emit(None, {}, str(e), t)


def combo(labels: dict, current=None) -> QComboBox:
    c = QComboBox()
    for k, v in labels.items():
        c.addItem(v, k)
    if current is not None:
        c.setCurrentIndex(max(0, c.findData(current)))
    return c


ROW_LABELS = {"content": E.CONTENT_LABELS, "res": RES_LABELS, "crop": CROP_LABELS, "audio": AUDIO_LABELS}


class AvMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.s = AvSettings.load()
        self.rows: dict[int, Row] = {}
        self._next = 0
        self.pool = QThreadPool.globalInstance()
        self.psig = ProbeSig()
        self.psig.done.connect(self._on_probed)
        self.client = Client()
        self.log_pos: dict[str, int] = {}
        self._inst = None
        self._polling = False
        self._stopping = False
        self.qsig = PollSig()
        self.qsig.done.connect(self._on_poll)
        self._cur_enc = self.s.encoder
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1360, 900)
        self.setAcceptDrops(True)
        self._build()
        self._load()
        self._refresh_env()
        if self.s.window_geometry:
            try:
                self.restoreGeometry(bytes.fromhex(self.s.window_geometry))
            except ValueError:
                pass
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(1000)
        self._poll()

    # ---------------------------------------------------------------- UI
    def _lbl(self, text, key):
        colon = text.endswith(":")
        lb = QLabel(f"{text.rstrip(':')} ⓘ" + (":" if colon else ""))
        lb.setToolTip(TIPS[key])
        lb.setCursor(Qt.WhatsThisCursor)
        return lb

    def _build(self):
        c = QWidget()
        self.setCentralWidget(c)
        root = QVBoxLayout(c)
        root.setContentsMargins(14, 12, 14, 12)
        head = QHBoxLayout()
        t = QLabel(APP_NAME)
        t.setObjectName("title")
        sub = QLabel("av1an で分割並列エンコード（AV1 / HEVC / H.264）")
        sub.setObjectName("muted")
        head.addWidget(t)
        head.addWidget(sub)
        head.addStretch(1)
        self.srv_chip = QLabel("キュー: 接続中…")
        self.srv_chip.setObjectName("muted")
        self.srv_chip.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.srv_chip.setOpenExternalLinks(True)
        head.addWidget(self.srv_chip)
        head.addSpacing(8)
        self.env_chip = QLabel()
        head.addWidget(self.env_chip)
        b = QPushButton("環境設定")
        b.clicked.connect(self._open_settings)
        head.addWidget(b)
        root.addLayout(head)

        split = QSplitter(Qt.Vertical)
        root.addWidget(split, 1)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["ファイル", "映像 ⓘ", "種類 ⓘ", "解像度 ⓘ", "黒帯 ⓘ", "音声 ⓘ", "状態 ⓘ", "進捗"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_FILE, QHeaderView.Stretch)
        for col, wd in [(COL_VINFO, 190), (COL_CONTENT, 110), (COL_RES, 120), (COL_CROP, 150), (COL_AUDIO, 120),
                        (COL_STATUS, 260), (COL_PROG, 100)]:
            hh.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, wd)
        for col, key in [(COL_VINFO, "vinfo"), (COL_CONTENT, "content"), (COL_RES, "res"), (COL_CROP, "crop"),
                         (COL_AUDIO, "audio"), (COL_STATUS, "status")]:
            self.table.horizontalHeaderItem(col).setToolTip(TIPS[key])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tl.addWidget(self.table, 1)
        self.hint = QLabel("ここに動画ファイルやフォルダをドロップしてください", self.table.viewport())
        self.hint.setObjectName("drop")
        self.hint.setAlignment(Qt.AlignCenter)

        btns = QHBoxLayout()
        for text, slot in [("ファイル追加…", self._add_files), ("フォルダ追加…", self._add_folder),
                           ("選択を削除", self._remove_sel), ("完了を消去", self._clear_done)]:
            bb = QPushButton(text)
            bb.clicked.connect(slot)
            btns.addWidget(bb)
        btns.addStretch(1)
        btns.addWidget(self._lbl("選択行に一括適用:", "bulk"))
        self.bulk = {k: combo(ROW_LABELS[k]) for k in PER_ROW}
        ab = QPushButton("適用")
        ab.clicked.connect(self._apply_bulk)
        for k in PER_ROW:
            btns.addWidget(self.bulk[k])
        btns.addWidget(ab)
        tl.addLayout(btns)
        split.addWidget(top)

        # エンコード設定
        enc = QGroupBox("エンコード設定")
        g = QGridLayout(enc)
        g.setColumnStretch(6, 1)
        self.c_enc = combo(E.ENCODER_LABELS)
        self.c_q = combo(E.QUALITY_LABELS)
        self.sp_q = QSpinBox()
        self.c_speed = combo(E.SPEED_LABELS)
        g.addWidget(self._lbl("エンコーダ", "encoder"), 0, 0)
        g.addWidget(self.c_enc, 0, 1)
        g.addWidget(self._lbl("画質", "quality"), 0, 2)
        hq = QHBoxLayout()
        hq.addWidget(self.c_q)
        self.lb_qname = QLabel("")
        self.lb_qname.setObjectName("muted")
        hq.addWidget(self.lb_qname)
        hq.addWidget(self.sp_q)
        g.addLayout(hq, 0, 3)
        g.addWidget(self._lbl("速度", "speed"), 0, 4)
        g.addWidget(self.c_speed, 0, 5)
        for w, k in ((self.c_enc, "encoder"), (self.c_q, "quality"), (self.sp_q, "quality"), (self.c_speed, "speed")):
            w.setToolTip(TIPS[k])
        self.c_enc.currentIndexChanged.connect(self._enc_changed)
        self.c_q.currentIndexChanged.connect(self._q_changed)
        self.c_speed.currentIndexChanged.connect(self._refresh_rows)

        g.addWidget(self._lbl("新規追加時の既定:", "default"), 1, 0)
        self.defaults = {k: combo(ROW_LABELS[k]) for k in PER_ROW}
        hd = QHBoxLayout()
        for k in PER_ROW:
            self.defaults[k].setToolTip(TIPS[k])
            hd.addWidget(self.defaults[k])
        hd.addStretch(1)
        g.addLayout(hd, 1, 1, 1, 6)

        g.addWidget(self._lbl("並列数", "workers"), 2, 0)
        hp = QHBoxLayout()
        self.sp_w = QSpinBox()
        self.sp_w.setRange(0, 4096)
        self.sp_w.setSpecialValueText("自動")
        self.sp_t = QSpinBox()
        self.sp_t.setRange(0, 1024)
        self.sp_t.setSpecialValueText("自動")
        self.sp_w.setToolTip(TIPS["workers"])
        self.sp_t.setToolTip(TIPS["threads"])
        hp.addWidget(self.sp_w)
        hp.addSpacing(12)
        hp.addWidget(self._lbl("1 本あたりのスレッド", "threads"))
        hp.addWidget(self.sp_t)
        hp.addSpacing(8)
        b_assist = QPushButton("アシスト…")
        b_assist.setToolTip(TIPS["assist"])
        b_assist.clicked.connect(self._assist)
        hp.addWidget(b_assist)
        hp.addSpacing(12)
        self.lb_auto = QLabel("")
        self.lb_auto.setObjectName("muted")
        self.lb_auto.setToolTip(TIPS["auto"])
        hp.addWidget(self.lb_auto)
        hp.addStretch(1)
        g.addLayout(hp, 2, 1, 1, 6)
        self.sp_w.valueChanged.connect(self._update_auto)
        self.sp_t.valueChanged.connect(self._update_auto)

        g.addWidget(self._lbl("目標画質", "target"), 3, 0)
        ht = QHBoxLayout()
        self.cb_tq = QCheckBox("VMAF を目標にする")
        self.sp_vmaf = QDoubleSpinBox()
        self.sp_vmaf.setRange(70, 99.5)
        self.sp_vmaf.setSingleStep(0.5)
        self.sp_vmaf.setDecimals(1)
        self.sp_probes = QSpinBox()
        self.sp_probes.setRange(2, 10)
        for w, k in ((self.cb_tq, "target"), (self.sp_vmaf, "target"), (self.sp_probes, "probes")):
            w.setToolTip(TIPS[k])
        ht.addWidget(self.cb_tq)
        ht.addWidget(self.sp_vmaf)
        ht.addSpacing(12)
        ht.addWidget(self._lbl("試行回数", "probes"))
        ht.addWidget(self.sp_probes)
        ht.addStretch(1)
        g.addLayout(ht, 3, 1, 1, 6)
        self.cb_tq.toggled.connect(self._tq_toggled)

        g.addWidget(self._lbl("追加パラメータ", "extra"), 4, 0)
        self.ed_extra = QLineEdit()
        self.ed_extra.setToolTip(TIPS["extra"])
        g.addWidget(self.ed_extra, 4, 1, 1, 6)
        g.addWidget(self._lbl("エンコーダの場所", "encoder_path"), 5, 0)
        he = QHBoxLayout()
        self.ed_path = QLineEdit()
        self.ed_path.setPlaceholderText("空欄 = 自動で取得したものを使います（SVT-AV1-PSY などを使うときに指定します）")
        self.ed_path.setToolTip(TIPS["encoder_path"])
        pb = QPushButton("参照…")
        pb.clicked.connect(self._browse_encoder)
        self.lb_encst = QLabel("")
        self.lb_encst.setObjectName("muted")
        he.addWidget(self.ed_path, 1)
        he.addWidget(pb)
        he.addWidget(self.lb_encst)
        g.addLayout(he, 5, 1, 1, 6)
        self.ed_path.editingFinished.connect(self._encoder_status)
        split.addWidget(enc)

        # 分割の詳細
        adv = QGroupBox("分割（av1an）")
        ga = QHBoxLayout(adv)
        self.c_chunk = combo(CHUNK_LABELS)
        self.c_sc = combo(SC_LABELS)
        self.sp_maxsc = QDoubleSpinBox()
        self.sp_maxsc.setRange(1, 600)
        self.sp_maxsc.setSuffix(" 秒")
        self.sp_minsc = QSpinBox()
        self.sp_minsc.setRange(1, 1000)
        self.sp_minsc.setSuffix(" フレーム")
        for lbl, key, w in (("分割方法", "chunk", self.c_chunk), ("シーン検出", "sc", self.c_sc),
                            ("最大の長さ", "max_scene", self.sp_maxsc), ("最小の長さ", "min_scene", self.sp_minsc)):
            ga.addWidget(self._lbl(lbl, key))
            w.setToolTip(TIPS[key])
            ga.addWidget(w)
            ga.addSpacing(10)
        ga.addStretch(1)
        split.addWidget(adv)

        # 出力
        opt = QGroupBox("出力設定")
        go = QGridLayout(opt)
        go.setColumnStretch(2, 1)
        self.rb_same = QRadioButton("入力と同じフォルダ")
        self.rb_custom = QRadioButton("指定:")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_same)
        grp.addButton(self.rb_custom)
        self.out_edit = QLineEdit()
        ob = QPushButton("参照…")
        ob.clicked.connect(lambda: self._browse(self.out_edit, self.rb_custom))
        go.addWidget(self._lbl("保存先", "out_dir"), 0, 0)
        go.addWidget(self.rb_same, 0, 1)
        h = QHBoxLayout()
        h.addWidget(self.rb_custom)
        h.addWidget(self.out_edit, 1)
        h.addWidget(ob)
        go.addLayout(h, 0, 2)
        go.addWidget(self._lbl("ファイル名", "suffix"), 1, 0)
        self.suffix = QLineEdit()
        self.suffix.setPlaceholderText("空欄 = 自動（_AV1 / _HEVC / _AVC）")
        self.suffix.setMaximumWidth(260)
        go.addWidget(self.suffix, 1, 1)
        go.addWidget(self._lbl("作業フォルダ", "temp_dir"), 2, 0)
        self.tmp = QLineEdit()
        self.tmp.setPlaceholderText("空欄 = 保存先に一時フォルダを作ります")
        tb = QPushButton("参照…")
        tb.clicked.connect(lambda: self._browse(self.tmp, None))
        self.cb_keep_tmp = QCheckBox("作業ファイルを残す")
        self.cb_keep_tmp.setToolTip(TIPS["keep_temp"])
        h2 = QHBoxLayout()
        h2.addWidget(self.tmp, 1)
        h2.addWidget(tb)
        h2.addWidget(self.cb_keep_tmp)
        go.addLayout(h2, 2, 1, 1, 2)
        split.addWidget(opt)

        self.logw = QPlainTextEdit()
        self.logw.setReadOnly(True)
        self.logw.setMaximumBlockCount(20000)
        split.addWidget(self.logw)
        split.setSizes([320, 220, 60, 130, 140])

        run = QHBoxLayout()
        self.cur = QLabel("")
        self.cur.setObjectName("muted")
        self.total = QProgressBar()
        self.total.setRange(0, 1000)
        self.total.setFormat("全体 %p%")
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_start = QPushButton("エンコード開始")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start)
        for wdg, st in ((self.cur, 1), (self.total, 1), (self.btn_stop, 0), (self.btn_start, 0)):
            run.addWidget(wdg, st)
        root.addLayout(run)
        a = QAction(self)
        a.setShortcut("Delete")
        a.triggered.connect(self._remove_sel)
        self.addAction(a)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._hint()

    def _hint(self):
        vp = self.table.viewport()
        self.hint.setGeometry(0, 0, vp.width(), vp.height())
        self.hint.setVisible(self.table.rowCount() == 0)

    # ---------------------------------------------------------------- 設定
    def _load(self):
        s = self.s
        quiet = (self.c_enc, self.c_q, self.c_speed, self.sp_w, self.sp_t, self.cb_tq)
        for w in quiet:
            w.blockSignals(True)
        try:
            self._load_values()
        finally:
            for w in quiet:
                w.blockSignals(False)
        self._update_auto()
        self._encoder_status()

    def _load_values(self):
        s = self.s
        self.c_enc.setCurrentIndex(max(0, self.c_enc.findData(s.encoder)))
        self.c_q.setCurrentIndex(max(0, self.c_q.findData(s.quality)))
        self.c_speed.setCurrentIndex(max(0, self.c_speed.findData(s.speed)))
        self._apply_q_range()
        self.sp_w.setValue(int(s.workers or 0))
        self.sp_t.setValue(int(s.threads or 0))
        self.cb_tq.setChecked(s.target_quality)
        self.sp_vmaf.setValue(float(s.target_vmaf))
        self.sp_probes.setValue(int(s.target_probes))
        self._tq_toggled(s.target_quality)
        self._cur_enc = s.encoder
        self.ed_extra.setText((s.extra or {}).get(s.encoder, ""))
        self.ed_path.setText((s.encoder_paths or {}).get(s.encoder, ""))
        self._extra_placeholder()
        for k, val in (("content", s.default_content), ("res", s.default_res), ("crop", s.default_crop),
                       ("audio", s.default_audio)):
            self.defaults[k].setCurrentIndex(max(0, self.defaults[k].findData(val)))
        self.c_chunk.setCurrentIndex(max(0, self.c_chunk.findData(s.chunk_method)))
        self.c_sc.setCurrentIndex(max(0, self.c_sc.findData(s.sc_method)))
        self.sp_maxsc.setValue(float(s.max_scene_sec))
        self.sp_minsc.setValue(int(s.min_scene_len))
        (self.rb_custom if s.output_dir else self.rb_same).setChecked(True)
        self.out_edit.setText(s.output_dir)
        self.suffix.setText(s.suffix)
        self.tmp.setText(s.temp_dir)
        self.cb_keep_tmp.setChecked(s.keep_temp)

    def _store_per_encoder(self):
        enc = self._cur_enc
        self.s.extra = dict(self.s.extra or {}, **{enc: self.ed_extra.text().strip()})
        self.s.encoder_paths = dict(self.s.encoder_paths or {}, **{enc: self.ed_path.text().strip().strip('"')})

    def _save(self):
        s = self.s
        self._store_per_encoder()
        s.encoder = self.c_enc.currentData()
        s.quality = self.c_q.currentData()
        if s.quality == "custom":
            s.custom_q = self.sp_q.value()
        s.speed = self.c_speed.currentData()
        s.workers = self.sp_w.value()
        s.threads = self.sp_t.value()
        s.target_quality = self.cb_tq.isChecked()
        s.target_vmaf = self.sp_vmaf.value()
        s.target_probes = self.sp_probes.value()
        s.default_content = self.defaults["content"].currentData()
        s.default_res = self.defaults["res"].currentData()
        s.default_crop = self.defaults["crop"].currentData()
        s.default_audio = self.defaults["audio"].currentData()
        s.chunk_method = self.c_chunk.currentData()
        s.sc_method = self.c_sc.currentData()
        s.max_scene_sec = self.sp_maxsc.value()
        s.min_scene_len = self.sp_minsc.value()
        s.output_dir = self.out_edit.text().strip() if self.rb_custom.isChecked() else ""
        s.suffix = self.suffix.text().strip()
        s.temp_dir = self.tmp.text().strip()
        s.keep_temp = self.cb_keep_tmp.isChecked()
        s.save()

    def _extra_placeholder(self):
        ex = {"svt-av1": "例: --film-grain 12 --tune 2", "aom": "例: --sharpness=2 --arnr-strength=1",
              "rav1e": "例: --tune Psnr", "x265": "例: --psy-rd 1.5 --aq-strength 0.8", "x264": "例: --psy-rd 1.0:0.15"}
        self.ed_extra.setPlaceholderText(f"{E.ENCODER_LABELS[self.s.encoder].split('（')[0]} の書き方。"
                                         f"{ex.get(self.s.encoder, '')}　空欄 = 種類ごとの既定のまま")

    def _apply_q_range(self):
        enc = self.s.encoder
        lo, hi = E.QUALITY[enc][3]
        self.sp_q.blockSignals(True)
        self.sp_q.setRange(lo, hi)
        self.sp_q.setValue(self.s.quality_value())
        self.sp_q.blockSignals(False)
        self.sp_q.setEnabled(self.s.quality == "custom")
        self.lb_qname.setText(E.QUALITY[enc][4])

    def _enc_changed(self):
        self._store_per_encoder()
        self.s.encoder = self.c_enc.currentData()
        self._cur_enc = self.s.encoder
        if self.s.quality == "custom":
            self.s.custom_q = E.QUALITY[self.s.encoder][1]
        self._apply_q_range()
        self.ed_extra.setText((self.s.extra or {}).get(self.s.encoder, ""))
        self.ed_path.setText((self.s.encoder_paths or {}).get(self.s.encoder, ""))
        self._extra_placeholder()
        self._update_auto()
        self._encoder_status()
        self._refresh_rows()

    def _q_changed(self):
        self.s.quality = self.c_q.currentData()
        self._apply_q_range()
        self._refresh_rows()

    def _tq_toggled(self, on):
        self.sp_vmaf.setEnabled(on)
        self.sp_probes.setEnabled(on)
        self.c_q.setEnabled(not on)
        self.sp_q.setEnabled(not on and self.s.quality == "custom")
        self.s.target_quality = on
        self._refresh_rows()

    def _max_width(self) -> int:
        ws = [r.info.video.width for r in self.rows.values() if r.info and r.info.video]
        return max(ws) if ws else 3840

    def _update_auto(self):
        profiles = (self.s.worker_profiles or {}).get(self.s.encoder)
        parts = []
        for rc, width in (("4k", 3840), ("hd", 1920)):
            w, t, how = A.resolve_workers(self.s.encoder, width, self.sp_w.value(), self.sp_t.value(), profiles)
            lp = f"・--lp {A.svt_lp(t)}" if self.s.encoder == "svt-av1" else ""
            parts.append(f"{A.RES_CLASS_LABELS[rc]} {w} 本 × {t}{lp}（{how}）")
        self.lb_auto.setText("→ " + "　".join(parts) + f"　｜ CPU {A.cpu_count()} / メモリ {A.total_memory_gb():.0f} GB")

    def _assist(self):
        from shared.gui.worker_assist import WorkerAssist
        w = WorkerAssist(self, {k: v for k, v in E.ENCODER_LABELS.items()}, self.s.encoder, self.s.assist_answers)
        if w.exec():
            self.s.worker_profiles = w.profiles
            self.s.assist_answers = w.answers
            # アシストの値を使うため、手動の指定は「自動」に戻す
            self.sp_w.setValue(0)
            self.sp_t.setValue(0)
            self._save()
            self._update_auto()
            cur = w.profiles.get(self.s.encoder, {})
            self.logw.appendPlainText("並列数アシスト: " + "、".join(
                f"{A.RES_CLASS_LABELS[rc]} {v['workers']} 本 × {v['threads']} スレッド" for rc, v in cur.items()))

    def _encoder_status(self):
        ok, msg = A.encoder_status(self.s.encoder, self.ed_path.text().strip().strip('"'))
        self.lb_encst.setText(("✔ " if ok else "✖ ") + msg)
        self.lb_encst.setStyleSheet(f"color: {'#8de0a6' if ok else '#ff8a95'};")
        return ok

    def _browse_encoder(self):
        f, _ = QFileDialog.getOpenFileName(self, "エンコーダの実行ファイル", self.ed_path.text() or str(Path.home()),
                                           "実行ファイル (*.exe);;すべて (*)")
        if f:
            self.ed_path.setText(str(Path(f)))
            self._encoder_status()

    def _refresh_env(self):
        sts = tools.check_all(self.s, TOOL_LIST, include_extra=False)
        bad = [t.label for t in sts if t.required and not t.ok]
        opt = [t.label for t in sts if not t.required and not t.ok]
        for i in range(self.c_enc.count()):
            k = self.c_enc.itemData(i)
            ok, _ = A.encoder_status(k, (self.s.encoder_paths or {}).get(k, ""))
            self.c_enc.setItemText(i, E.ENCODER_LABELS[k] + ("" if ok else "（未導入）"))
        if bad:
            self.env_chip.setText("要セットアップ: " + ", ".join(bad))
            self.env_chip.setObjectName("chip_ng")
        elif opt:
            self.env_chip.setText("一部のツールが未導入")
            self.env_chip.setToolTip("未導入: " + ", ".join(opt))
            self.env_chip.setObjectName("chip_ng")
        else:
            self.env_chip.setText("環境 OK")
            self.env_chip.setObjectName("chip_ok")
        self.env_chip.style().unpolish(self.env_chip)
        self.env_chip.style().polish(self.env_chip)
        return not bad

    def _open_settings(self):
        def extra():
            lines = []
            for k in E.ENCODERS:
                ok, msg = A.encoder_status(k, (self.s.encoder_paths or {}).get(k, ""))
                lines.append(f"{E.ENCODER_LABELS[k].split('（')[0]}: {'使用可' if ok else '使用不可'}（{msg}）")
            return lines
        d = SettingsDialog(self.s, self, tool_list=TOOL_LIST, tips=TIPS, extra_status=extra)
        d.changed.connect(self._refresh_env)
        d.exec()
        self._refresh_env()
        self._encoder_status()
        for rid, r in self.rows.items():
            if r.info is None and r.state == ST_ERROR:
                self._probe(rid)

    def _browse(self, edit, rb):
        d = QFileDialog.getExistingDirectory(self, "フォルダを選択", edit.text() or self.s.recent_dir or str(Path.home()))
        if d:
            edit.setText(str(Path(d)))
            if rb:
                rb.setChecked(True)

    # ---------------------------------------------------------------- キュー
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()])

    def _add_files(self):
        exts = " ".join(f"*{x}" for x in sorted(VIDEO_EXTS))
        fs, _ = QFileDialog.getOpenFileNames(self, "エンコードするファイル", self.s.recent_dir or str(Path.home()),
                                             f"動画 ({exts});;すべて (*)")
        if fs:
            self.s.recent_dir = str(Path(fs[0]).parent)
            self.add_paths([Path(f) for f in fs])

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "フォルダを追加（サブフォルダも検索）", self.s.recent_dir or str(Path.home()))
        if d:
            self.s.recent_dir = d
            self.add_paths([Path(d)])

    def add_paths(self, paths):
        files = []
        for p in paths:
            if p.is_dir():
                files += sorted(f for f in p.rglob("*") if f.suffix.lower() in VIDEO_EXTS
                                and "av1gui_" not in str(f.parent))
            elif p.is_file():
                files.append(p)
        have = {r.path for r in self.rows.values()}
        for f in files:
            if f not in have:
                self._add_row(f)
        self._hint()

    def _add_row(self, path, jid=None, options=None) -> int:
        rid = self._next
        self._next += 1
        row = Row(path, jid=jid)
        self.rows[rid] = row
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(path.name)
        it.setData(Qt.UserRole, rid)
        it.setToolTip(str(path))
        self.table.setItem(r, COL_FILE, it)
        vinfo = QLabel("…")
        vinfo.setContentsMargins(6, 0, 6, 0)
        st = QLabel(ST_PROBING)
        st.setContentsMargins(6, 0, 6, 0)
        pb = QProgressBar()
        pb.setRange(0, 1000)
        row.w = {"vinfo": vinfo, "status": st, "prog": pb}
        cols = {"content": COL_CONTENT, "res": COL_RES, "crop": COL_CROP, "audio": COL_AUDIO}
        for k in PER_ROW:
            cb = combo(ROW_LABELS[k], (options or {}).get(k, self.defaults[k].currentData()))
            cb.setToolTip(TIPS[k])
            cb.currentIndexChanged.connect(lambda _=0, rid=rid: self._update(rid))
            row.w[k] = cb
            self.table.setCellWidget(r, cols[k], cb)
        for col, wdg in ((COL_VINFO, vinfo), (COL_STATUS, st), (COL_PROG, pb)):
            self.table.setCellWidget(r, col, wdg)
        self._probe(rid)
        return rid

    def _probe(self, rid):
        ffprobe = tools.find_tool("ffprobe", self.s)
        row = self.rows[rid]
        if not ffprobe:
            if not row.jid:
                self._set(rid, ST_ERROR, "ffprobe がありません（環境設定でセットアップしてください）")
            return
        if not row.jid:
            self._set(rid, ST_PROBING)
        self.pool.start(ProbeTask(rid, row.path, ffprobe, self.psig))

    @Slot(int, object, str)
    def _on_probed(self, rid, info, err):
        row = self.rows.get(rid)
        if row is None:
            return
        if row.jid:                       # キューにあるもの: 映像の説明だけ（状態はサーバーに従う）
            row.info = info
            if info and info.video:
                row.w["vinfo"].setText(info.video.describe())
                row.w["vinfo"].setToolTip(info.video.detail())
            else:
                row.w["vinfo"].setText("—")
            return
        if err or info is None:
            self._set(rid, ST_ERROR, err.splitlines()[-1] if err else "解析失敗")
            return
        row.info = info
        if info.video is None:
            self._set(rid, ST_ERROR, "映像がありません")
            return
        row.w["vinfo"].setText(info.video.describe())
        row.w["vinfo"].setToolTip(info.video.detail())
        row.state = ST_READY
        self._update(rid)
        self._update_auto()

    def _job(self, row) -> AvJob:
        return AvJob(row.path, content=row.w["content"].currentData(), res=row.w["res"].currentData(),
                     crop=row.w["crop"].currentData(), audio=row.w["audio"].currentData())

    def _refresh_rows(self, *_):
        for rid in list(self.rows):
            self._update(rid)

    def _update(self, rid):
        row = self.rows.get(rid)
        if not row or not row.info or row.state not in (ST_READY, ST_ERROR) or not row.info.video:
            return
        self.s.speed = self.c_speed.currentData() or self.s.speed
        short, detail = describe_plan(row.info, self._job(row), self.s)
        self._set(rid, ST_READY)
        row.w["status"].setText(f"待機 → {short}")
        row.w["status"].setToolTip(detail)

    def _set(self, rid, state, msg="", output=""):
        row = self.rows[rid]
        row.state, row.error = state, msg
        if output:
            row.output = Path(output)
        st = row.w["status"]
        col = {ST_DONE: "#8de0a6", ST_FAILED: "#ff8a95", ST_ERROR: "#c8a96a", ST_RUNNING: "#9cc0ff",
               ST_CANCELLED: "#c8a96a"}.get(state, "#c9ced8")
        st.setStyleSheet(f"color: {col};")
        text = "待機（順番待ち）" if state == ST_QUEUED else state
        st.setText(text + (f": {msg}" if msg and state in (ST_ERROR, ST_FAILED) else ""))
        st.setToolTip(msg or (str(row.output) if row.output else ""))
        if state == ST_DONE:
            row.w["prog"].setValue(1000)
        editable = state in (ST_READY, ST_ERROR, ST_DONE, ST_FAILED, ST_CANCELLED)
        for k in PER_ROW:
            row.w[k].setEnabled(editable)

    def _ids_sel(self):
        return [self.table.item(r, COL_FILE).data(Qt.UserRole) for r in sorted({i.row() for i in self.table.selectedIndexes()})]

    def _drop_rows(self, ids):
        for rid in ids:
            for r in range(self.table.rowCount()):
                if self.table.item(r, COL_FILE).data(Qt.UserRole) == rid:
                    self.table.removeRow(r)
                    break
            self.rows.pop(rid, None)
        self._hint()
        self._update_auto()

    def _remove_ids(self, ids):
        """表から消す。キューに送ったものはサーバーからも消す（Web 画面からも消える）。処理中のものは消さない"""
        drop = []
        for rid in ids:
            row = self.rows[rid]
            if row.state == ST_RUNNING:
                continue
            if row.jid:
                try:
                    self.client.remove(row.jid)
                except ServerError as e:
                    self.cur.setText(str(e))
                    continue
            drop.append(rid)
        self._drop_rows(drop)

    def _remove_sel(self):
        self._remove_ids(self._ids_sel())

    def _clear_done(self):
        self._remove_ids([k for k, r in self.rows.items() if r.state == ST_DONE])

    def _apply_bulk(self):
        for rid in self._ids_sel() or list(self.rows):
            w = self.rows[rid].w
            for k in PER_ROW:
                if w[k].isEnabled():
                    w[k].setCurrentIndex(max(0, w[k].findData(self.bulk[k].currentData())))

    # ---------------------------------------------------------------- 実行（サーバーのキュー）
    def _opts(self, row) -> dict:
        return {k: row.w[k].currentData() for k in PER_ROW}

    def _start(self):
        self._save()
        if not self._refresh_env():
            QMessageBox.warning(self, APP_NAME, "必要なツールが揃っていません。環境設定でセットアップしてください。")
            self._open_settings()
            return
        if not self._encoder_status() and not (A.IS_WIN and self.s.encoder in A.EXTRA_ENCODERS
                                               and not self.ed_path.text().strip()):
            QMessageBox.warning(self, APP_NAME, f"{E.ENCODER_LABELS[self.s.encoder]} が使えません。環境設定でセットアップしてください。")
            return
        if self.rb_custom.isChecked() and not self.out_edit.text().strip():
            QMessageBox.warning(self, APP_NAME, "保存先フォルダを指定してください。")
            return
        targets = []
        for r in range(self.table.rowCount()):
            rid = self.table.item(r, COL_FILE).data(Qt.UserRole)
            row = self.rows[rid]
            if (not row.jid and row.state == ST_READY and row.info) or \
                    (row.jid and row.state in (ST_FAILED, ST_CANCELLED)):
                targets.append(rid)
        if not targets:
            QMessageBox.information(self, APP_NAME, "エンコード待ちのファイルがありません。")
            return
        try:
            self.client.ensure()
        except ServerError as e:
            QMessageBox.warning(self, APP_NAME, f"キューを受け持つ Web サーバーに接続できません。\n{e}")
            return
        for rid in targets:
            row = self.rows[rid]
            try:
                if row.jid:                       # 失敗・中止したもの: 今の選択肢でやり直す
                    self.client.retry(row.jid, self._opts(row))
                else:
                    row.jid = self.client.add(KIND, row.path, self._opts(row))
                    self.log_pos[row.jid] = 0
            except ServerError as e:
                QMessageBox.warning(self, APP_NAME, f"{row.path.name} をキューに追加できません。\n{e}")
                break
            row.sent = time.time()
            row.w["prog"].setValue(0)
            self._set(rid, ST_QUEUED)
        self._stopping = False
        self._poll()

    def _stop(self):
        try:
            self.client.stop_all()
        except ServerError as e:
            QMessageBox.warning(self, APP_NAME, str(e))
            return
        self._stopping = True
        self.btn_stop.setEnabled(False)
        self.cur.setText("停止中…（途中までの分は残ります。もう一度開始すると続きから再開します）")

    def _poll(self):
        if self._polling:
            return
        self._polling = True
        self.pool.start(PollTask(self.client, dict(self.log_pos), self.qsig))

    @Slot(object, object, str, float)
    def _on_poll(self, st, logs, err, t):
        self._polling = False
        if st is None:
            self.srv_chip.setText("キュー: Web サーバーに接続できません")
            self.srv_chip.setToolTip(err)
            self.btn_stop.setEnabled(False)
            return
        url = self.client.base
        self.srv_chip.setText(f'キュー: <a href="{url}/" style="color:#9cc0ff">{url.split("//")[-1]}</a>')
        self.srv_chip.setToolTip("Web 画面と同じキューです")
        if self._inst and st.get("instance") != self._inst:
            # サーバーが再起動してキューが消えた: 送ったものは開始前に戻す
            for rid, row in list(self.rows.items()):
                if row.jid:
                    row.jid = None
                    if row.state in (ST_QUEUED, ST_RUNNING) and row.info:
                        row.state = ST_READY
                        self._update(rid)
            self.log_pos.clear()
        self._inst = st.get("instance")
        jobs = [j for j in st["jobs"] if j["kind"] == KIND]
        byid = {j["id"]: j for j in jobs}
        by_jid = {r.jid: rid for rid, r in self.rows.items() if r.jid}
        # Web 画面などで消されたものは、この表からも消す
        self._drop_rows([rid for jid, rid in by_jid.items() if jid not in byid and self.rows[rid].sent < t])
        for j in jobs:
            rid = by_jid.get(j["id"])
            if rid is None:                       # Web 画面から追加されたもの
                rid = self._add_row(Path(j["path"]), jid=j["id"], options=j["options"])
                self.log_pos[j["id"]] = max(0, j["log_lines"] - 200) if j["state"] == "running" else j["log_lines"]
            elif self.rows[rid].sent >= t:
                continue                          # 送る前に取った状態
            self._show_job(rid, j)
        for jid, (lines, nxt) in logs.items():
            if lines:
                self.logw.appendPlainText("\n".join(lines))
            self.log_pos[jid] = nxt
        self._summary(jobs)
        self._hint()

    def _show_job(self, rid, j):
        row = self.rows[rid]
        state = SERVER_ST.get(j["state"], ST_FAILED)
        self._set(rid, state, j.get("error", ""), j.get("output", ""))
        if state == ST_RUNNING:
            row.w["status"].setText(f"処理中: {j['label']}")
            row.w["status"].setToolTip(j["label"])
        if state != ST_DONE:
            row.w["prog"].setValue(int(j["progress"] * 1000))

    def _summary(self, jobs):
        active = [j for j in jobs if j["state"] in ("queued", "running")]
        run = next((j for j in jobs if j["state"] == "running"), None)
        n = len(jobs)
        fin = sum(1 for j in jobs if j["state"] in ("done", "failed", "cancelled"))
        if not active:
            self._stopping = False
        self.btn_stop.setEnabled(bool(active) and not self._stopping)
        self.total.setValue(int((fin + (run["progress"] if run else 0)) / n * 1000) if n else 0)
        if self._stopping:
            return
        if run:
            self.cur.setText(f"{jobs.index(run) + 1}/{n}  {Path(run['path']).name} — {run['label']}")
        elif active:
            self.cur.setText("順番待ち…")
        elif n:
            ok = sum(1 for j in jobs if j["state"] == "done")
            ng = sum(1 for j in jobs if j["state"] == "failed")
            self.cur.setText(f"終了: 成功 {ok} / 失敗 {ng} / 全 {n}")
        else:
            self.cur.setText("")

    def closeEvent(self, e):
        if any(r.jid and r.state in (ST_QUEUED, ST_RUNNING) for r in self.rows.values()):
            box = QMessageBox(QMessageBox.Question, APP_NAME,
                              "エンコードはこの画面を閉じても裏で続きます（Web 画面でも確認できます）。\n"
                              "中止する場合も、途中までの分は残り、次回続きから再開できます。", parent=self)
            keep = box.addButton("閉じる（続ける）", QMessageBox.AcceptRole)
            stop = box.addButton("中止して閉じる", QMessageBox.DestructiveRole)
            box.addButton("キャンセル", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is stop:
                try:
                    self.client.stop_all()
                except ServerError:
                    pass
            elif box.clickedButton() is not keep:
                e.ignore()
                return
        self.timer.stop()
        self._save()
        self.s.window_geometry = bytes(self.saveGeometry()).hex()
        self.s.save()
        super().closeEvent(e)
