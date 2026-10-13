"""python -m shared.web [--host 0.0.0.0] [--port 8765]
省略した値は settings_web.json（Web 画面の設定）に従う"""
import argparse
import os
import socket
import sys
import time
from pathlib import Path


def _wait_port(host: str, port: int, sec: float = 15) -> None:
    """再起動のとき、前のサーバーがポートを放すまで待つ"""
    end = time.time() + sec
    while time.time() < end:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
            return
        except OSError:
            time.sleep(0.5)
        finally:
            s.close()


def _single_instance(root: Path, sec: float = 20):
    """このフォルダのサーバーは 1 つだけにする（キューが 2 つに分かれないように）。
    ポートでは防げない: Windows は 0.0.0.0 と 127.0.0.1 で同じポートを別々のプロセスが待ち受けられるため、
    デスクトップ版が立てた 127.0.0.1 のサーバーと自動起動の 0.0.0.0 のサーバーが並んで動いてしまう。
    logs/web.lock を排他で持ち続け、取れなければ None（再起動のときは前のサーバーが放すまで待つ）"""
    (root / "logs").mkdir(exist_ok=True)
    f = open(root / "logs" / "web.lock", "a+")
    end = time.time() + sec
    while True:
        try:
            if sys.platform.startswith("win"):
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f                        # プロセスが終わると OS が自動で放す
        except OSError:
            if time.time() >= end:
                f.close()
                return None
            time.sleep(0.5)


def main() -> int:
    ap = argparse.ArgumentParser(prog="shared.web")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
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

    from shared.web.client import write_info
    from shared.web.config import WebConfig
    cfg = WebConfig.load()
    host = a.host or cfg.host
    port = a.port or cfg.port
    print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} Web 画面を起動します: http://{host}:{port}/", flush=True)
    lock = _single_instance(root)
    if lock is None:
        print("このフォルダの Web 画面はすでに動いているため、起動しません（logs/web.json に待ち受けがあります）", flush=True)
        return 0
    _wait_port(host, port)
    # デスクトップ版がこのサーバーを見つけるための目印（ポートは設定で変えられるため）
    write_info(host, port)
    try:
        import uvicorn
        uvicorn.run("shared.web.server:app", host=host, port=port, log_level="warning", use_colors=False)
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
