"""並列数アシスト: いくつか質問に答えると、CPU 数・メモリ・解像度から並列数とスレッド数を決める"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QDoubleSpinBox, QHBoxLayout, QLabel, QRadioButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWizard, QWizardPage)

from shared.core import av1an as A

RES_TARGETS = {"both": ("両方（ファイルごとに自動で切り替える）", ["4k", "hd"]),
               "4k": ("ほぼ 4K", ["4k"]),
               "hd": ("ほぼ 1080p 以下", ["hd"])}
WIDTH = {"4k": 3840, "hd": 1920}


def _radio_page(title: str, sub: str, options: dict, current: str):
    page = QWizardPage()
    page.setTitle(title)
    page.setSubTitle(sub)
    lay = QVBoxLayout(page)
    grp = QButtonGroup(page)
    for k, label in options.items():
        rb = QRadioButton(label)
        rb.setProperty("key", k)
        rb.setChecked(k == current)
        grp.addButton(rb)
        lay.addWidget(rb)
    lay.addStretch(1)
    return page, grp


def _checked(grp: QButtonGroup) -> str:
    b = grp.checkedButton()
    return b.property("key") if b else ""


class WorkerAssist(QWizard):
    """encoders: {キー: 表示名}。結果は profiles（エンコーダ → 解像度区分 → {workers, threads}）と answers"""

    def __init__(self, parent=None, encoders: dict | None = None, current: str = "", answers: dict | None = None):
        super().__init__(parent)
        self.encoders = encoders or {"x265": "x265"}
        self.current = current or next(iter(self.encoders))
        a = answers or {}
        self.cores = A.cpu_count()
        self.mem = A.total_memory_gb()
        self.profiles: dict = {}
        self.answers: dict = {}
        self.setWindowTitle("並列数アシスト")
        self.setWizardStyle(QWizard.ModernStyle)
        self.resize(720, 520)
        self.setButtonText(QWizard.NextButton, "次へ")
        self.setButtonText(QWizard.BackButton, "戻る")
        self.setButtonText(QWizard.FinishButton, "この値を使う")
        self.setButtonText(QWizard.CancelButton, "キャンセル")

        # 1. この PC
        p0 = QWizardPage()
        p0.setTitle("この PC")
        p0.setSubTitle("CPU とメモリを調べました。いくつか質問に答えると、映像の解像度ごとに並列数とスレッド数を決めます。")
        l0 = QVBoxLayout(p0)
        info = QLabel(f"論理プロセッサ: <b>{self.cores}</b><br>搭載メモリ: <b>{self.mem:.0f} GB</b>"
                      f"（いまの空き {A.available_memory_gb():.0f} GB）<br><br>"
                      "仮想マシンの場合は、割り当てた CPU・メモリが表示されます。")
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        l0.addWidget(info)
        l0.addStretch(1)
        self.addPage(p0)

        # 2. 解像度
        p1, self.g_res = _radio_page("主にエンコードする映像", "4K は 1 本あたりのメモリとスレッドを多く使うため、"
                                     "1080p 以下とは並列数を変えます。",
                                     {k: v[0] for k, v in RES_TARGETS.items()}, a.get("res", "both"))
        self.addPage(p1)

        # 3. CPU の使い方
        p2, self.g_cpu = _radio_page("CPU の使い方", "エンコード中にこの PC（や同じホストの他の VM）で別の作業をするなら、少し残してください。",
                                     {k: v[0] for k, v in A.CPU_USE.items()}, a.get("cpu", "all"))
        self.addPage(p2)

        # 4. メモリ
        p3 = QWizardPage()
        p3.setTitle("残しておくメモリ")
        p3.setSubTitle("OS や他のアプリのために残す量です。足りないと途中でエンコーダが落ちることがあります。")
        l3 = QVBoxLayout(p3)
        h = QHBoxLayout()
        self.sp_mem = QDoubleSpinBox()
        self.sp_mem.setRange(1, max(2.0, self.mem))
        self.sp_mem.setSuffix(" GB")
        self.sp_mem.setDecimals(0)
        self.sp_mem.setValue(float(a.get("mem_reserve") or max(4.0, round(self.mem * 0.1))))
        h.addWidget(QLabel("残す量"))
        h.addWidget(self.sp_mem)
        h.addStretch(1)
        l3.addLayout(h)
        note = QLabel(f"既定は搭載メモリの 1 割（最低 4 GB）です。エンコードに使えるのは「{self.mem:.0f} GB − 残す量」です。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        l3.addWidget(note)
        l3.addStretch(1)
        self.addPage(p3)

        # 5. 結果
        self.p4 = QWizardPage()
        self.p4.setTitle("おすすめの設定")
        self.p4.setSubTitle("この値で保存します。あとで設定画面から手動で変えることもできます。")
        l4 = QVBoxLayout(self.p4)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.RichText)
        l4.addWidget(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["エンコーダ", "解像度", "並列数 × スレッド", "メモリ（目安）", "決め手"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        l4.addWidget(self.table, 1)
        self.addPage(self.p4)
        self.currentIdChanged.connect(self._on_page)

    def _on_page(self, pid):
        if self.page(pid) is self.p4:
            self._compute()

    def _compute(self):
        res = RES_TARGETS.get(_checked(self.g_res) or "both")[1]
        cpu = _checked(self.g_cpu) or "all"
        reserve = self.sp_mem.value()
        self.answers = {"res": _checked(self.g_res) or "both", "cpu": cpu, "mem_reserve": reserve}
        self.profiles = {}
        rows = []
        for enc, label in self.encoders.items():
            self.profiles[enc] = {}
            for rc in res:
                p = A.plan_workers(enc, WIDTH[rc], self.cores, self.mem, cpu, reserve)
                self.profiles[enc][rc] = {"workers": p["workers"], "threads": p["threads"]}
                rows.append((enc, label, rc, p))
        self.table.setRowCount(len(rows))
        for i, (enc, label, rc, p) in enumerate(rows):
            vals = [label.split("（")[0], A.RES_CLASS_LABELS[rc], f"{p['workers']} 本 × {p['threads']} スレッド",
                    f"{p['mem_need']:.0f} / {p['use_mem']:.0f} GB",
                    "メモリ不足（1 本でも足りません）" if p["short"] else ("メモリ" if p["limited_by"] == "memory" else "CPU")]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if p["short"] and j == 4:
                    it.setForeground(Qt.red)
                if enc == self.current:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(i, j, it)
        self.table.resizeColumnsToContents()
        cur = self.profiles.get(self.current, {})
        lines = [f"{A.RES_CLASS_LABELS[rc]}: <b>{v['workers']} 本 × {v['threads']} スレッド</b>" for rc, v in cur.items()]
        use_cores = A.plan_workers(self.current, 1920, self.cores, self.mem, cpu, reserve)["use_cores"]
        self.summary.setText(f"{self.encoders[self.current].split('（')[0]}（いま選んでいるエンコーダ）: " + "、".join(lines)
                             + f"<br>使う CPU: {use_cores} / {self.cores}　使うメモリ: 最大 {self.mem - reserve:.0f} GB"
                             + "<br>他のエンコーダの分もまとめて保存します（エンコーダを切り替えても使えます）。"
                             + "<br><span style='color:#8a93a3'>決め手が「メモリ」の行は、メモリを増やすと並列数を上げられます。</span>")
