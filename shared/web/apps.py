"""Web 画面に載せるツールの定義（各ツールの webapp.py が WebApp を返す）"""
from __future__ import annotations

import html
import importlib
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from shared import app_name, apps


def plain(d: dict) -> dict:
    """ツールチップの HTML を素の文字列に"""
    return {k: html.unescape(re.sub(r"<[^>]+>", "", v.replace("<br>", "\n"))) for k, v in d.items()}


@dataclass
class WebApp:
    id: str                              # パッケージ名
    name: str                            # 表示名
    desc: str                            # 一行説明
    icon: Path
    settings_cls: type
    tips: dict                           # キー → 説明（素の文字列）
    job_fields: list[dict]               # ファイルごとの選択肢 {key, label, choices, default, tip}
    settings_fields: list[list]          # [設定名, 表示名, 種類(text|number|bool|select), tip, 選択肢 or None]
    run: Callable                        # (path, options, log, prog, cancel) -> 出力パス or (出力パス, 注意の list)
    settings_note: str = ""
    skip_re: str = ""                    # フォルダ追加時に除外する（変換済みの）ファイル名
    probe_extra: Callable | None = None  # (info) -> 追加の説明行 list[str]
    env_extra: Callable | None = None    # () -> 環境画面の追加行 list[str]
    work_prefixes: tuple = ()            # 作業フォルダ名の接頭辞（ファイル一覧で隠す）
    extra_meta: dict = field(default_factory=dict)
    media_exts: tuple = ()               # ファイル一覧に出す追加の拡張子
    job_defaults: Callable | None = None
    assist_encoders: dict = field(default_factory=dict)  # 並列数アシストの対象（エンコーダ → 表示名）。空なら使わない
    # 見出し付きの設定（デスクトップ版の設定画面と Web 画面の設定タブで共通）。空なら settings_fields を 1 つにまとめる
    #   [{"id", "title", "note", "fields": [{"key", "label", "type", "hint", "tip", "choices", "min", "max",
    #     "step", "suffix", "placeholder", "show_if": {key: 値 or [値]}, "main": bool, "advanced": bool}]}]
    #   key の {encoder} などは、同じ設定のその項目の値に置き換える（extra.{encoder} → extra.x265）
    settings_sections: list = field(default_factory=list)

    def sections(self) -> list[dict]:
        if self.settings_sections:
            return self.settings_sections
        fs = [{"key": f[0], "label": f[1], "type": f[2], "tip": f[3], "choices": f[4]} for f in self.settings_fields]
        return [{"id": "main", "title": "設定", "note": self.settings_note, "fields": fs}] if fs else []

    def option_text(self, options: dict) -> str:
        parts = []
        for f in self.job_fields:
            v = options.get(f["key"])
            if v is None:
                continue
            label = f["choices"].get(str(v), f["choices"].get(v, v)) if f.get("choices") else v
            parts.append(str(label).split("（")[0])
        return " / ".join(parts)


_CACHE: dict[str, WebApp] | None = None
_LOCK = threading.Lock()


def load_apps() -> dict[str, WebApp]:
    """入っているツールの WebApp。最初の 1 回だけ読み込む。
    サーバーは問い合わせを並行して処理するため、読み込み終わるまで待たせ、途中の（空の）一覧を返さない"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    with _LOCK:
        if _CACHE is None:
            found = {}
            for p in apps():
                try:
                    mod = importlib.import_module(f"{p}.webapp")
                    found[p] = mod.make()
                except Exception as e:  # noqa: BLE001
                    print(f"{app_name(p)} を Web 画面に読み込めません: {e}")
            _CACHE = found
    return _CACHE
