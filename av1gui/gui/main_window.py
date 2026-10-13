"""av1an_gui メインウィンドウ
キュー・進捗は Web 画面のサーバーが持つ（shared.web.client）。この画面で追加したファイルは、解析が終わった
時点でサーバーのキューに入り、Web 画面から追加したものも含めて同じキューを表示する。キュー全体に「開始 / 停止」が
あり、「エンコード開始」で上から順に処理する。
メイン画面にはエンコードの設定（エンコーダ・画質・速度・並列数・目標画質・追加パラメータなど）と保存先・
終わった後の動作を置き、追加時の既定・分割・作業フォルダなどは設定画面（settings_schema.SECTIONS）にまとめる。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QGroupBox, QHBoxLayout, QProgressDialog,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QToolButton, QVBoxLayout, QWidget)

from shared.core import av1an as A
from shared.core import power
from shared.core import probe as P
from shared.core import tools
from shared.gui.settings_dialog import SettingsDialog
from shared import update as U
from shared.version import app_version, code_fingerprint
from shared.web.client import Client, ServerError

from .. import APP_NAME, __version__
from .. import encoders as E
from ..config import AvSettings
from ..encode import AUDIO_LABELS, CROP_LABELS, RES_LABELS, SPLIT_LABELS, AvJob, describe_plan
from ..env import TOOL_LIST
from ..settings_schema import SECTIONS
from .help import TIPS

VIDEO_EXTS = {".mkv", ".m2ts", ".mts", ".ts", ".mp4", ".m4v", ".mov", ".webm", ".avi", ".hevc", ".y4m"}
COL_FILE, COL_VINFO, COL_CONTENT, COL_RES, COL_CROP, COL_AUDIO, COL_STATUS, COL_PROG = range(8)
PER_ROW = ("content", "res", "crop", "audio")
PER_ROW_NAMES = {"content": "種類", "res": "解像度", "crop": "黒帯", "audio": "音声"}
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
    sent_opts: dict | None = None        # サーバーに送った選択肢
    sent: float = 0.0                    # キューに送った時刻（それより前に取った状態では上書きしない）


class ProbeSig(QObject):
    done = Signal(int, object, str)


class ProbeTask(QRunnable):
    def __init__(self, rid, path, ffprobe, sig):
        super().__init__()
        self.rid, self.path, self.ffprobe, self.sig = rid, path, ffprobe, sig

    def run(self):
        try:
            r = (P.probe(self.path, self.ffprobe), "")
        except Exception as e:  # noqa: BLE001
            r = (None, str(e))
        _emit(self.sig, self.rid, *r)


def _emit(sig, *args):
    try:
        sig.done.emit(*args)
    except RuntimeError:          # 画面を閉じた後に終わった（受け取り先がもう無い）
        pass


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
            r = (st, logs, "", t)
        except Exception as e:  # noqa: BLE001
            r = (None, {}, str(e), t)
        _emit(self.sig, *r)


def combo(labels: dict, current=None) -> QComboBox:
    c = QComboBox()
    for k, v in labels.items():
        c.addItem(v, k)
    if current is not None:
        c.setCurrentIndex(max(0, c.findData(current)))
    return c


ROW_LABELS = {"content": E.CONTENT_LABELS, "res": RES_LABELS, "crop": CROP_LABELS, "audio": AUDIO_LABELS}
DEFAULT_OF = {"content": "default_content", "res": "default_res", "crop": "default_crop", "audio": "default_audio"}


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
        self._after_touched = 0.0
        self._after_info = {}
        self._active = False              # キューが開始中
        self._restarting = False          # 古いサーバーを起動し直している
        self._restart_failed = 0.0        # 起動し直しに失敗した時刻（失敗したときは少し待ってから試す）
        self._my_code = code_fingerprint()
        self._cur_enc = self.s.encoder
        self.qsig = PollSig()
        self.qsig.done.connect(self._on_poll)
        self.rsig = PollSig()
        self.rsig.done.connect(self._on_restarted)
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1280, 780)
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
        self.usig = PollSig()
        self.usig.done.connect(self._on_update_info)
        self._upd = {}
        self.utimer = QTimer(self)
        self.utimer.timeout.connect(lambda: self._check_update(False))
        self.utimer.start(3600 * 1000)
        QTimer.singleShot(3000, lambda: self._check_update(False))
        QTimer.singleShot(500, self._show_last_update)

    # ---------------------------------------------------------------- UI
    def _lbl(self, text, key):
        lb = QLabel(f"{text} ⓘ")
        lb.setToolTip(TIPS[key])
        lb.setCursor(Qt.WhatsThisCursor)
        return lb

    def _build(self):
        c = QWidget()
        self.setCentralWidget(c)
        root = QVBoxLayout(c)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # 見出し
        head = QHBoxLayout()
        t = QLabel(APP_NAME)
        t.setObjectName("title")
        sub = QLabel("av1an で分割並列エンコード（AV1 / HEVC / H.264）")
        sub.setObjectName("muted")
        head.addWidget(t)
        head.addWidget(sub)
        head.addStretch(1)
        self.upd_btn = QPushButton("")
        self.upd_btn.setObjectName("primary")
        self.upd_btn.setVisible(False)
        self.upd_btn.clicked.connect(self._update_now)
        head.addWidget(self.upd_btn)
        head.addSpacing(8)
        self.srv_chip = QLabel("キュー: 接続中…")
        self.srv_chip.setObjectName("muted")
        self.srv_chip.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.srv_chip.setOpenExternalLinks(True)
        head.addWidget(self.srv_chip)
        head.addSpacing(8)
        self.env_chip = QLabel()
        head.addWidget(self.env_chip)
        b = QPushButton("設定")
        b.clicked.connect(lambda: self._open_settings())
        head.addWidget(b)
        root.addLayout(head)

        # ファイルの一覧（ログは下に畳んでおく）
        self.split = QSplitter(Qt.Vertical)
        root.addWidget(self.split, 1)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["ファイル", "映像 ⓘ", "種類 ⓘ", "解像度 ⓘ", "黒帯 ⓘ", "音声 ⓘ", "状態 ⓘ", "進捗"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_FILE, QHeaderView.Stretch)
        for col, wd in [(COL_VINFO, 280), (COL_CONTENT, 110), (COL_RES, 120), (COL_CROP, 150), (COL_AUDIO, 120),
                        (COL_STATUS, 300), (COL_PROG, 100)]:
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
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)
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
        tip = QLabel("行を選んで右クリックすると、種類・解像度・黒帯・音声をまとめて変えられます")
        tip.setObjectName("muted")
        btns.addWidget(tip)
        tl.addLayout(btns)
        self.split.addWidget(top)

        self.logw = QPlainTextEdit()
        self.logw.setReadOnly(True)
        self.logw.setMaximumBlockCount(20000)
        self.split.addWidget(self.logw)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 1)

        # エンコード
        enc = QGroupBox("エンコード")
        g = QGridLayout(enc)
        g.setHorizontalSpacing(10)
        g.setColumnStretch(6, 1)
        self.c_enc = combo(E.ENCODER_LABELS)
        self.c_q = combo(E.QUALITY_LABELS)
        self.sp_q = QSpinBox()
        self.lb_qname = QLabel("")
        self.lb_qname.setObjectName("muted")
        self.c_speed = combo(E.SPEED_LABELS)
        for w, k in ((self.c_enc, "encoder"), (self.c_q, "quality"), (self.sp_q, "quality"), (self.c_speed, "speed")):
            w.setToolTip(TIPS[k])
        g.addWidget(self._lbl("エンコーダ", "encoder"), 0, 0)
        g.addWidget(self.c_enc, 0, 1)
        g.addWidget(self._lbl("画質", "quality"), 0, 2)
        hq = QHBoxLayout()
        hq.addWidget(self.c_q)
        hq.addWidget(self.lb_qname)
        hq.addWidget(self.sp_q)
        g.addLayout(hq, 0, 3)
        g.addWidget(self._lbl("速度", "speed"), 0, 4)
        g.addWidget(self.c_speed, 0, 5)
        self.c_enc.currentIndexChanged.connect(self._enc_changed)
        self.c_q.currentIndexChanged.connect(self._q_changed)
        self.sp_q.valueChanged.connect(self._refresh_rows)
        self.c_speed.currentIndexChanged.connect(self._refresh_rows)

        g.addWidget(self._lbl("並列数", "workers"), 1, 0)
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
        b_assist.clicked.connect(lambda: self._assist(self))
        hp.addWidget(b_assist)
        hp.addSpacing(12)
        self.lb_par = QLabel("")
        self.lb_par.setObjectName("muted")
        self.lb_par.setToolTip(TIPS["auto"])
        hp.addWidget(self.lb_par)
        hp.addStretch(1)
        g.addLayout(hp, 1, 1, 1, 6)
        self.sp_w.valueChanged.connect(self._par_changed)
        self.sp_t.valueChanged.connect(self._par_changed)

        g.addWidget(self._lbl("目標画質", "target"), 2, 0)
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
        g.addLayout(ht, 2, 1, 1, 6)
        self.cb_tq.toggled.connect(self._tq_toggled)
        self.sp_vmaf.valueChanged.connect(self._tq_toggled)

        g.addWidget(self._lbl("追加パラメータ", "extra"), 3, 0)
        self.ed_extra = QLineEdit()
        self.ed_extra.setToolTip(TIPS["extra"])
        g.addWidget(self.ed_extra, 3, 1, 1, 6)
        g.addWidget(self._lbl("エンコーダの場所", "encoder_path"), 4, 0)
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
        g.addLayout(he, 4, 1, 1, 6)
        self.ed_path.editingFinished.connect(self._encoder_status)

        g.addWidget(self._lbl("保存先", "out_dir"), 5, 0)
        ho = QHBoxLayout()
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("空欄 = 入力と同じフォルダ")
        self.out_edit.setClearButtonEnabled(True)
        ob = QPushButton("参照…")
        ob.clicked.connect(self._browse_out)
        ho.addWidget(self.out_edit, 1)
        ho.addWidget(ob)
        g.addLayout(ho, 5, 1, 1, 6)

        # 設定画面にあるものの要約
        sumrow = QHBoxLayout()
        self.lb_auto = QLabel("")
        self.lb_auto.setObjectName("muted")
        self.lb_auto.setWordWrap(True)
        more = QPushButton("その他の設定…")
        more.setToolTip("分割・追加時の既定・ファイル名・作業フォルダ・Web 画面・外部ツール")
        more.clicked.connect(lambda: self._open_settings("split"))
        sumrow.addWidget(self.lb_auto, 1)
        sumrow.addWidget(more)
        g.addLayout(sumrow, 6, 0, 1, 7)
        root.addWidget(enc)

        # 実行
        run = QHBoxLayout()
        self.log_btn = QToolButton()
        self.log_btn.setText("ログ")
        self.log_btn.setCheckable(True)
        self.log_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.log_btn.toggled.connect(self._toggle_log)
        run.addWidget(self.log_btn)
        self.cur = QLabel("")
        self.cur.setObjectName("muted")
        self.total = QProgressBar()
        self.total.setRange(0, 1000)
        self.total.setFormat("全体 %p%")
        run.addWidget(self.cur, 1)
        run.addWidget(self.total, 1)
        run.addSpacing(8)
        run.addWidget(QLabel("終わったら"))
        self.c_after = combo(power.LABELS)
        self.c_after.setToolTip("キューのファイルがすべて終わった後の動作です。実行の 60 秒前から取り消せます。"
                                "停止ボタンで止めた場合や、最後のファイルを中止した場合は実行しません。"
                                "Web 画面と共通です。")
        self.c_after.currentIndexChanged.connect(self._after_changed)
        run.addWidget(self.c_after)
        self.btn_after_cancel = QPushButton("取り消す")
        self.btn_after_cancel.setVisible(False)
        self.btn_after_cancel.clicked.connect(lambda: self._set_after(power.NONE))
        run.addWidget(self.btn_after_cancel)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_start = QPushButton("エンコード開始")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self._start)
        run.addWidget(self.btn_stop)
        run.addWidget(self.btn_start)
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

    def _toggle_log(self, on):
        self.logw.setVisible(on)
        self.log_btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.s.show_log = on

    # ---------------------------------------------------------------- 設定
    def _load(self):
        quiet = (self.c_enc, self.c_q, self.c_speed, self.sp_q, self.sp_w, self.sp_t, self.cb_tq, self.sp_vmaf)
        for w in quiet:
            w.blockSignals(True)
        try:
            s = self.s
            self.c_enc.setCurrentIndex(max(0, self.c_enc.findData(s.encoder)))
            self.c_q.setCurrentIndex(max(0, self.c_q.findData(s.quality)))
            self.c_speed.setCurrentIndex(max(0, self.c_speed.findData(s.speed)))
            self.sp_w.setValue(int(s.workers or 0))
            self.sp_t.setValue(int(s.threads or 0))
            self.cb_tq.setChecked(s.target_quality)
            self.sp_vmaf.setValue(float(s.target_vmaf))
            self.sp_probes.setValue(int(s.target_probes))
            self.sp_vmaf.setEnabled(s.target_quality)
            self.sp_probes.setEnabled(s.target_quality)
            self._cur_enc = s.encoder
            self.ed_extra.setText((s.extra or {}).get(s.encoder, ""))
            self.ed_path.setText((s.encoder_paths or {}).get(s.encoder, ""))
            self._extra_placeholder()
            self._apply_q_range()
            self.out_edit.setText(s.output_dir)
            self.log_btn.setChecked(s.show_log)
            self._toggle_log(s.show_log)
        finally:
            for w in quiet:
                w.blockSignals(False)
        self._update_auto()
        self._encoder_status()

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
        s.output_dir = self.out_edit.text().strip().strip('"')
        s.save()

    def _extra_placeholder(self):
        ex = {"svt-av1": "例: --film-grain 12 --tune 2", "aom": "例: --sharpness=2 --arnr-strength=1",
              "rav1e": "例: --tune Psnr", "x265": "例: --psy-rd 1.5 --aq-strength 0.8", "x264": "例: --psy-rd 1.0:0.15"}
        self.ed_extra.setPlaceholderText(f"{E.ENCODER_LABELS[self.s.encoder].split('（')[0]} の書き方。"
                                         f"{ex.get(self.s.encoder, '')}　空欄 = 種類ごとの既定のまま")

    def _tq_toggled(self, *_):
        on = self.cb_tq.isChecked()
        self.sp_vmaf.setEnabled(on)
        self.sp_probes.setEnabled(on)
        self.s.target_quality = on
        self.s.target_vmaf = self.sp_vmaf.value()
        self._apply_q_range()
        self._refresh_rows()

    def _par_changed(self):
        self.s.workers = self.sp_w.value()
        self.s.threads = self.sp_t.value()
        self._update_auto()

    def _apply_q_range(self):
        enc = self.s.encoder
        lo, hi = E.QUALITY[enc][3]
        self.sp_q.blockSignals(True)
        self.sp_q.setRange(lo, hi)
        self.sp_q.setValue(self.s.quality_value())
        self.sp_q.blockSignals(False)
        tq = self.s.target_quality
        self.c_q.setEnabled(not tq)
        self.sp_q.setEnabled(not tq and self.s.quality == "custom")
        self.lb_qname.setText(f"目標 VMAF {self.s.target_vmaf:g}" if tq else E.QUALITY[enc][4])
        self.c_q.setToolTip("目標画質（VMAF）を使っています。設定の「エンコード」で切り替えます" if tq else TIPS["quality"])

    def _enc_changed(self):
        self._store_per_encoder()
        self.s.encoder = self.c_enc.currentData()
        self._cur_enc = self.s.encoder
        self.ed_extra.setText((self.s.extra or {}).get(self.s.encoder, ""))
        self.ed_path.setText((self.s.encoder_paths or {}).get(self.s.encoder, ""))
        self._extra_placeholder()
        self._encoder_status()
        if self.s.quality == "custom":
            self.s.custom_q = E.QUALITY[self.s.encoder][1]
        self._apply_q_range()
        self._update_auto()
        self._refresh_rows()

    def _q_changed(self):
        self.s.quality = self.c_q.currentData()
        self._apply_q_range()
        self._refresh_rows()

    def _update_auto(self):
        """メイン画面に出していない設定の要約（並列数・分割・追加時の既定）"""
        s = self.s
        profiles = (s.worker_profiles or {}).get(s.encoder)
        par = []
        for rc, width in (("4k", 3840), ("hd", 1920)):
            w, t, how = A.resolve_workers(s.encoder, width, s.workers, s.threads, profiles)
            lp = f"・--lp {A.svt_lp(t)}" if s.encoder == "svt-av1" else ""
            par.append(f"{A.RES_CLASS_LABELS[rc]} {w} 本 × {t}{lp}（{how}）")
        self.lb_par.setText("→ " + "　".join(par) + f"　｜ CPU {A.cpu_count()} / メモリ {A.total_memory_gb():.0f} GB")
        split = SPLIT_LABELS.get(s.split, s.split).split("（")[0]
        if s.split == "manual":
            split = f"最大 {s.max_scene_sec:g} 秒・最小 {s.min_scene_len} フレーム"
        defaults = "・".join(str(ROW_LABELS[k].get(getattr(s, DEFAULT_OF[k]), "")).split("（")[0] for k in PER_ROW)
        self.lb_auto.setText(f"分割: {split}　｜　追加時の既定: {defaults}"
                             f"　｜　作業フォルダ: {s.temp_dir or '保存先'}")

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

    def _open_settings(self, tab: str = ""):
        self._save()

        def extra():
            u = self._upd                   # 確認済みの結果（ここで取りに行くと画面が止まるため）
            lines =[f"{APP_NAME} {app_version()}" + (f"（新しい版 {u['latest']} があります。見出しの「更新」から入れ替えます）"
                                                        if u.get("available") else
                                                        (f"（{u['error']}）" if u.get("error") else "（最新です）"))]
            for k in E.ENCODERS:
                ok, msg = A.encoder_status(k, (self.s.encoder_paths or {}).get(k, ""))
                lines.append(f"{E.ENCODER_LABELS[k].split('（')[0]}: {'使用可' if ok else '使用不可'}（{msg}）")
            return lines

        d = SettingsDialog(self.s, self, tool_list=TOOL_LIST, tips=TIPS, extra_status=extra, sections=SECTIONS,
                           client=self.client, tab=tab if self._refresh_env() else "tools")
        d.changed.connect(self._refresh_env)
        d.exec()
        self._load()
        self._refresh_env()
        self._refresh_rows()
        for rid, r in self.rows.items():
            if r.info is None and r.state == ST_ERROR:
                self._probe(rid)

    def _assist(self, parent):
        from shared.gui.worker_assist import WorkerAssist
        w = WorkerAssist(parent, dict(E.ENCODER_LABELS), self.s.encoder, self.s.assist_answers)
        if not w.exec():
            return
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

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "保存先", self.out_edit.text() or self.s.recent_dir or str(Path.home()))
        if d:
            self.out_edit.setText(str(Path(d)))

    # ---------------------------------------------------------------- 更新
    def _check_update(self, force: bool):
        sig = self.usig

        class Task(QRunnable):
            def run(self):
                _emit(sig, U.check(force), {}, "", 0.0)
        self.pool.start(Task())

    @Slot(object, object, str, float)
    def _on_update_info(self, info, _l, _e, _t):
        self._upd = info or {}
        ok = bool(self._upd.get("available"))
        self.upd_btn.setVisible(ok)
        if ok:
            self.upd_btn.setText(f"更新 {self._upd['latest']}")
            self.upd_btn.setToolTip(f"いまの版 {self._upd['current']} → 新しい版 {self._upd['latest']}\n\n"
                                    + (self._upd.get("notes") or "")[:1500])

    def _show_last_update(self):
        st = U.last_status()
        if not st or st.get("shown") or st.get("state") == "running":
            return
        U.mark_shown()
        box = QMessageBox.information if st.get("state") == "done" else QMessageBox.warning
        box(self, APP_NAME, st.get("message", "") + "\n\n詳しい経過は logs/update.log にあります。")

    def _update_now(self):
        info = self._upd
        if not info.get("available"):
            return
        if self._active or any(r.state == ST_RUNNING for r in self.rows.values()):
            QMessageBox.information(self, APP_NAME, "エンコード中は更新できません。停止するか、終わってから更新してください。")
            return
        box = QMessageBox(QMessageBox.Question, APP_NAME,
                          f"{info['current']} から {info['latest']} に更新します。\n\n"
                          "入れ替えの間（1〜数分）、この画面と Web 画面は閉じ、終わると自動で開き直します。"
                          "キューと設定は残ります。", parent=self)
        if info.get("notes"):
            box.setDetailedText(info["notes"])
        go = box.addButton("更新する", QMessageBox.AcceptRole)
        box.addButton("キャンセル", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not go:
            return
        self._save()
        dlg = QProgressDialog(f"{info['latest']} を取得しています…", None, 0, 0, self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setMinimumDuration(0)
        dlg.show()
        sig = self.usig2 = PollSig()
        sig.done.connect(lambda _a, _b, err, _c: self._on_downloaded(dlg, err))

        class Task(QRunnable):
            def run(self):
                try:
                    U.download(info)
                    _emit(sig, None, {}, "", 0.0)
                except Exception as e:  # noqa: BLE001
                    _emit(sig, None, {}, str(e) or "取得に失敗しました", 0.0)
        self.pool.start(Task())

    def _on_downloaded(self, dlg, err):
        dlg.close()
        if err:
            QMessageBox.warning(self, APP_NAME, f"新しい版を取得できませんでした。\n{err}")
            return
        try:
            U.spawn_apply([KIND])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, f"更新を始められませんでした。\n{e}")
            return
        self._closing_for_update = True
        self.close()
        QApplication.instance().quit()

    # ---------------------------------------------------------------- 一覧
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
            cb = combo(ROW_LABELS[k], (options or {}).get(k, getattr(self.s, DEFAULT_OF[k])))
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
                self._set(rid, ST_ERROR, "ffprobe がありません（設定の「外部ツール」でセットアップしてください）")
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
                self._show_vinfo(row)
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
        self._show_vinfo(row)
        row.state = ST_READY
        self._update(rid)
        self._send(rid)                   # 解析できたら、すぐキューに入れる（Web 画面にも出る）

    def _show_vinfo(self, row):
        info = row.info
        row.w["vinfo"].setText(f"{info.video.describe()}・音声 {len(info.audio)}・字幕 {len(info.subtitles)}")
        row.w["vinfo"].setToolTip(info.video.detail() + "\n\n" + "\n".join(info.track_lines())
                                  + "\n\n音声・字幕はすべて新しいファイルに入れます（Opus に変換するときは変換したもの）")

    def _job(self, row) -> AvJob:
        return AvJob(row.path, content=row.w["content"].currentData(), res=row.w["res"].currentData(),
                     crop=row.w["crop"].currentData(), audio=row.w["audio"].currentData())

    def _refresh_rows(self, *_):
        for rid in list(self.rows):
            self._update(rid)

    def _update(self, rid):
        """行の選択肢や設定が変わった: 状態の説明を作り直す。キューで待機中なら、サーバーの選択肢も変える"""
        row = self.rows.get(rid)
        if not row or not row.info or not row.info.video:
            return
        if row.state not in (ST_READY, ST_ERROR, ST_QUEUED):
            return
        self.s.speed = self.c_speed.currentData() or self.s.speed
        if self.s.quality == "custom":
            self.s.custom_q = self.sp_q.value()
        short, detail = describe_plan(row.info, self._job(row), self.s)
        if row.jid and row.state == ST_QUEUED:
            opts = self._opts(row)
            if opts != row.sent_opts:
                try:
                    self.client.set_options(row.jid, opts)
                    row.sent_opts = opts
                except ServerError as e:
                    self.cur.setText(str(e))
        else:
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
        editable = state in (ST_READY, ST_ERROR, ST_QUEUED, ST_DONE, ST_FAILED, ST_CANCELLED)
        for k in PER_ROW:
            row.w[k].setEnabled(editable)

    def _ids_sel(self):
        return [self.table.item(r, COL_FILE).data(Qt.UserRole) for r in sorted({i.row() for i in self.table.selectedIndexes()})]

    def _row_menu(self, pos):
        """右クリック: 選んだ行（未選択なら全部）の種類・解像度・黒帯・音声をまとめて変える"""
        ids = self._ids_sel() or list(self.rows)
        if not ids:
            return
        m = QMenu(self)
        for k in PER_ROW:
            sub = m.addMenu(PER_ROW_NAMES[k])
            for val, label in ROW_LABELS[k].items():
                sub.addAction(label).triggered.connect(lambda _=False, k=k, val=val: self._apply_bulk(ids, k, val))
        m.addSeparator()
        m.addAction("選択を削除").triggered.connect(self._remove_sel)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def _apply_bulk(self, ids, key, value):
        for rid in ids:
            w = self.rows[rid].w[key] if rid in self.rows else None
            if w is not None and w.isEnabled():
                w.setCurrentIndex(max(0, w.findData(value)))

    def _drop_rows(self, ids):
        for rid in ids:
            for r in range(self.table.rowCount()):
                if self.table.item(r, COL_FILE).data(Qt.UserRole) == rid:
                    self.table.removeRow(r)
                    break
            self.rows.pop(rid, None)
        self._hint()

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

    # ---------------------------------------------------------------- 実行（サーバーのキュー）
    def _opts(self, row) -> dict:
        return {k: row.w[k].currentData() for k in PER_ROW}

    def _send(self, rid) -> bool:
        """解析できた行をキューに入れる。サーバーにつながらなければ手元に置き、つながったら送る"""
        row = self.rows.get(rid)
        if not row or row.jid or row.state != ST_READY or not row.info:
            return False
        try:
            row.jid = self.client.add(KIND, row.path, self._opts(row))
        except ServerError:
            return False
        row.sent_opts = self._opts(row)
        self.log_pos[row.jid] = 0
        row.sent = time.time()
        self._set(rid, ST_QUEUED)
        self._update(rid)
        return True

    def _start(self):
        """キューを開始する。失敗・中止した行は今の選択肢でやり直す"""
        self._save()
        if not self._refresh_env():
            QMessageBox.warning(self, APP_NAME, "必要なツールが揃っていません。設定の「外部ツール」でセットアップしてください。")
            self._open_settings("tools")
            return
        if not self._encoder_status() and not (A.IS_WIN and self.s.encoder in A.EXTRA_ENCODERS
                                               and not self.ed_path.text().strip()):
            QMessageBox.warning(self, APP_NAME, f"{E.ENCODER_LABELS[self.s.encoder]} が使えません。"
                                                "設定の「外部ツール」でセットアップしてください。")
            return
        try:
            self.client.ensure()
        except ServerError as e:
            QMessageBox.warning(self, APP_NAME, f"キューを受け持つ Web サーバーに接続できません。\n{e}")
            return
        for r in range(self.table.rowCount()):
            rid = self.table.item(r, COL_FILE).data(Qt.UserRole)
            row = self.rows[rid]
            try:
                if not row.jid:
                    self._send(rid)
                elif row.state in (ST_FAILED, ST_CANCELLED):
                    self.client.retry(row.jid, self._opts(row))
                    row.sent, row.sent_opts = time.time(), self._opts(row)
                    row.w["prog"].setValue(0)
                    self._set(rid, ST_QUEUED)
                    self._update(rid)
            except ServerError as e:
                QMessageBox.warning(self, APP_NAME, f"{row.path.name} をキューに入れられません。\n{e}")
                return
        if not any(r.jid and r.state in (ST_QUEUED, ST_RUNNING) for r in self.rows.values()):
            QMessageBox.information(self, APP_NAME, "エンコード待ちのファイルがありません。")
            return
        try:
            self.client.start_queue()
        except ServerError as e:
            QMessageBox.warning(self, APP_NAME, str(e))
            return
        self._active = True
        self._poll()

    def _stop(self):
        """キューを停止する。処理中のものは待機に戻り、次に開始すると続きから再開する"""
        try:
            self.client.stop_queue()
        except ServerError as e:
            QMessageBox.warning(self, APP_NAME, str(e))
            return
        self._stopping = True
        self.btn_stop.setEnabled(False)
        self.cur.setText("停止中…（処理中だったものは待機に戻ります。もう一度開始すると続きから再開します）")

    def _after_changed(self):
        self._set_after(self.c_after.currentData())

    def _set_after(self, action):
        self._after_touched = time.time()
        try:
            self._after_info = self.client.set_after(action)
        except ServerError as e:
            self.cur.setText(str(e))
            return
        self._show_after()

    def _show_after(self):
        a = self._after_info or {}
        if a and time.time() - self._after_touched > 2:      # 自分で変えた直後は、古い状態で戻さない
            self.c_after.blockSignals(True)
            self.c_after.setCurrentIndex(max(0, self.c_after.findData(a.get("action", power.NONE))))
            self.c_after.blockSignals(False)
        self.btn_after_cancel.setVisible(bool(a.get("seconds")))

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
        restarted = bool(self._inst) and st.get("instance") != self._inst
        self._inst = st.get("instance")
        self._active = bool((st.get("queue") or {}).get("active"))
        self._after_info = st.get("after") or {}
        self._show_after()
        jobs = [j for j in st["jobs"] if j["kind"] == KIND]
        byid = {j["id"]: j for j in jobs}
        by_jid = {r.jid: rid for rid, r in self.rows.items() if r.jid}
        gone = [rid for jid, rid in by_jid.items() if jid not in byid and self.rows[rid].sent < t]
        if restarted:
            # サーバーが起動し直して、キューに無くなったもの（以前の版のサーバーなど）: 手元に戻して入れ直す
            for rid in gone:
                row = self.rows[rid]
                row.jid = None
                if row.info and row.info.video and row.state in (ST_QUEUED, ST_RUNNING):
                    row.state = ST_READY
            gone = []
        self._drop_rows(gone)                     # Web 画面などで消されたもの
        for j in jobs:
            rid = by_jid.get(j["id"])
            if rid is None:                       # Web 画面から追加されたもの
                rid = self._add_row(Path(j["path"]), jid=j["id"], options=j["options"])
                self.rows[rid].sent_opts = dict(j["options"])
                self.log_pos[j["id"]] = max(0, j["log_lines"] - 200) if j["state"] == "running" else j["log_lines"]
            elif self.rows[rid].sent >= t:
                continue                          # 送る前に取った状態
            self._show_job(rid, j)
        for rid, row in list(self.rows.items()):  # まだキューに入っていないもの
            if not row.jid and row.state == ST_READY:
                self._send(rid)
        for jid, (lines, nxt) in logs.items():
            if lines:
                self.logw.appendPlainText("\n".join(lines))
            self.log_pos[jid] = nxt
        self._summary(jobs)
        self._check_code(st, jobs)
        self._hint()

    def _check_code(self, st, jobs):
        """動いているサーバーのコードが、いまのファイルと違う（更新したのに古いまま）なら起動し直す。
        エンコード中は止めないよう、終わるまで待つ（キューは保存されるので、起動し直しても残る）"""
        disk = code_fingerprint(max_age=10)
        if self._my_code != disk:
            self.srv_chip.setToolTip("プログラムが更新されています。この画面を開き直してください")
        if st.get("code") == disk:
            return
        if any(j["state"] == "running" for j in st["jobs"]):
            self.srv_chip.setText(self.srv_chip.text() + "（サーバーが更新前のままです。今のエンコードが終わったら起動し直します）")
            return
        if self._restarting or time.time() - self._restart_failed < 60:
            return
        self._restarting = True
        self.srv_chip.setText("キュー: サーバーを新しいプログラムで起動し直しています…")
        sig, client = self.rsig, self.client

        class Task(QRunnable):
            def run(self):
                try:
                    client.restart()
                    r = (None, {}, "", 0.0)
                except Exception as e:  # noqa: BLE001
                    r = (None, {}, str(e), 0.0)
                _emit(sig, *r)
        self.pool.start(Task())

    @Slot(object, object, str, float)
    def _on_restarted(self, _st, _logs, err, _t):
        self._restarting = False
        if err:
            self._restart_failed = time.time()
            self.cur.setText(f"サーバーを起動し直せませんでした: {err}")
        self._poll()

    def _show_job(self, rid, j):
        row = self.rows[rid]
        state = SERVER_ST.get(j["state"], ST_FAILED)
        self._set(rid, state, j.get("error", ""), j.get("output", ""))
        if state == ST_RUNNING:
            row.w["status"].setText(f"処理中: {j['label']}")
            row.w["status"].setToolTip(j["label"])
        elif state == ST_QUEUED:
            self._update(rid)                     # 待機 → これから行う処理
        if state == ST_DONE and j.get("warning"):
            row.w["status"].setText(f"完了（注意あり）: {j['warning']}")
            row.w["status"].setStyleSheet("color: #c8a96a;")
            row.w["status"].setToolTip(j["warning"])
        if state != ST_DONE:
            row.w["prog"].setValue(int(j["progress"] * 1000))

    def _summary(self, jobs):
        queued = [j for j in jobs if j["state"] == "queued"]
        run = next((j for j in jobs if j["state"] == "running"), None)
        n = len(jobs)
        fin = sum(1 for j in jobs if j["state"] in ("done", "failed", "cancelled"))
        if not self._active and not run:
            self._stopping = False
        self.btn_stop.setEnabled(self._active and not self._stopping)
        self.total.setValue(int((fin + (run["progress"] if run else 0)) / n * 1000) if n else 0)
        a = self._after_info or {}
        if a.get("seconds"):
            self.cur.setText(f"すべて終わりました。あと {a['seconds']} 秒で{a['label']}します（取り消せます）")
            return
        if self._stopping:
            return
        if run:
            self.cur.setText(f"{jobs.index(run) + 1}/{n}  {Path(run['path']).name} — {run['label']}")
        elif queued and self._active:
            self.cur.setText("順番待ち…")
        elif queued:
            self.cur.setText(f"停止中です。待機 {len(queued)} 件は「エンコード開始」で始まります")
        elif n:
            ok = sum(1 for j in jobs if j["state"] == "done")
            ng = sum(1 for j in jobs if j["state"] == "failed")
            extra = f"（{a['message']}）" if a.get("message") else ""
            self.cur.setText(f"終了: 成功 {ok} / 失敗 {ng} / 全 {n}{extra}")
        else:
            self.cur.setText(a.get("message", ""))

    def closeEvent(self, e):
        if not getattr(self, "_closing_for_update", False) and self._active and any(r.jid and r.state in (ST_QUEUED, ST_RUNNING) for r in self.rows.values()):
            box = QMessageBox(QMessageBox.Question, APP_NAME,
                              "エンコードはこの画面を閉じても裏で続きます（Web 画面でも確認できます）。\n"
                              "停止する場合も、途中までの分は残り、次に開始すると続きから再開します。", parent=self)
            keep = box.addButton("閉じる（続ける）", QMessageBox.AcceptRole)
            stop = box.addButton("停止して閉じる", QMessageBox.DestructiveRole)
            box.addButton("キャンセル", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is stop:
                try:
                    self.client.stop_queue()
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

