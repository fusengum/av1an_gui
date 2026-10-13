"""処理がすべて終わった後の電源操作（スリープ・休止状態・シャットダウン）"""
from __future__ import annotations

import subprocess

from .config import IS_WIN
from .tools import popen_kwargs

NONE, SLEEP, HIBERNATE, SHUTDOWN = "none", "sleep", "hibernate", "shutdown"
LABELS = {NONE: "何もしない", SLEEP: "スリープ", HIBERNATE: "休止状態", SHUTDOWN: "シャットダウン"}
GRACE_SEC = 60                       # 実行までの猶予（この間は取り消せる）


def execute(action: str) -> None:
    """失敗したら RuntimeError"""
    if action == NONE:
        return
    if action not in LABELS:
        raise RuntimeError(f"不明な操作です: {action}")
    if IS_WIN:
        if action in (SLEEP, HIBERNATE):
            import ctypes
            # rundll32 経由だと、休止状態が有効な PC ではスリープのつもりが休止状態になるため、直接呼ぶ
            ok = ctypes.WinDLL("powrprof").SetSuspendState(action == HIBERNATE, False, False)
            if not ok:
                raise RuntimeError(f"{LABELS[action]}にできませんでした（この PC・仮想マシンでは使えない可能性があります）")
            return
        cmd = ["shutdown", "/s", "/t", "0"]
    else:
        cmd = ["systemctl", {SLEEP: "suspend", HIBERNATE: "hibernate", SHUTDOWN: "poweroff"}[action]]
    r = subprocess.run(cmd, capture_output=True, text=True, **popen_kwargs())
    if r.returncode != 0:
        raise RuntimeError(f"{LABELS[action]}にできませんでした: {(r.stderr or r.stdout).strip()}")
