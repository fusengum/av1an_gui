"""python -m shared.web [--host 0.0.0.0] [--port 8765]"""
import argparse
import os
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(prog="shared.web")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    os.chdir(root)

    # pythonw.exe（自動起動）では標準出力が無い（None）。uvicorn がそこで落ちるため、ログファイルに向ける
    if sys.stdout is None or sys.stderr is None:
        (root / "logs").mkdir(exist_ok=True)
        f = open(root / "logs" / "web.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f

    print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} Web 画面を起動します: http://{a.host}:{a.port}/", flush=True)
    # デスクトップ版がこのサーバーを見つけるための目印（ポートはインストール時に変えられるため）
    from shared.web.client import write_info
    write_info(a.host, a.port)
    try:
        import uvicorn
        uvicorn.run("shared.web.server:app", host=a.host, port=a.port, log_level="warning", use_colors=False)
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
