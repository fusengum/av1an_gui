"""Web 画面に載せるツールの定義（各ツールの webapp.py が WebApp を返す）"""
from __future__ import annotations

import html
import importlib
import re
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
    run: Callable                        # (path, options, log, prog, cancel) -> 出力パス
    settings_note: str = ""
    skip_re: str = ""                    # フォルダ追加時に除外する（変換済みの）ファイル名
    probe_extra: Callable | None = None  # (info) -> 追加の説明行 list[str]
    env_extra: Callable | None = None    # () -> 環境画面の追加行 list[str]
    work_prefixes: tuple = ()            # 作業フォルダ名の接頭辞（ファイル一覧で隠す）
    extra_meta: dict = field(default_factory=dict)
    media_exts: tuple = ()               # ファイル一覧に出す追加の拡張子
    job_defaults: Callable | None = None
    assist_encoders: dict = field(default_factory=dict)  # 並列数アシストの対象（エンコーダ → 表示名）。空なら使わない # () -> {key: 既定値}（設定の「新規追加時の既定」）

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


def load_apps() -> dict[str, WebApp]:
    global _CACHE
    if _CACHE is None:
        _CACHE = {}
        for p in apps():
            try:
                mod = importlib.import_module(f"{p}.webapp")
                _CACHE[p] = mod.make()
            except Exception as e:  # noqa: BLE001
                print(f"{app_name(p)} を Web 画面に読み込めません: {e}")
    return _CACHE
