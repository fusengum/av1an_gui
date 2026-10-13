"""共通の場所と OS 判定。設定と自動取得したツールはアプリのフォルダ内に置く（ポータブル運用）。"""
from __future__ import annotations

import sys
from pathlib import Path

IS_WIN = sys.platform.startswith("win")
EXE = ".exe" if IS_WIN else ""

# アプリのフォルダ（shared パッケージの 1 つ上）
APP_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = APP_ROOT / "tools"

if IS_WIN:
    # DLL 不足などで子プロセス（x265 など）がエラーダイアログを出して止まらないようにする（子プロセスにも継承される）
    try:
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    except Exception:  # noqa: BLE001
        pass
