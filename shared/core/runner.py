"""外部コマンドの実行と、処理全般で使う補助"""
from __future__ import annotations

import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .tools import popen_kwargs

LogCB = Callable[[str], None]
ProgCB = Callable[[str, float], None]      # (stage label, overall 0..1)


class Cancelled(Exception):
    pass


class JobError(Exception):
    pass



_SPLIT = re.compile(rb"[\r\n]+")


class Runner:
    def __init__(self, log: LogCB, cancel: threading.Event):
        self.log = log
        self.cancel = cancel
        self.procs: list[subprocess.Popen] = []
        self.on_cancel = None        # 中止時に追加で呼ぶ処理（Docker コンテナの停止など）

    def _pump(self, stream, on_line: Callable[[str], None]):
        buf = b""
        while True:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(4096)
            if not chunk:
                break
            buf += chunk
            parts = _SPLIT.split(buf)
            buf = parts.pop()
            for p in parts:
                if p:
                    on_line(p.decode("utf-8", errors="replace"))
        if buf:
            on_line(buf.decode("utf-8", errors="replace"))

    def start(self, cmd: list[str], *, cwd=None, stdin=None, stdout=None, on_line=None, quiet_re=None, env=None):
        self.log("> " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
        p = subprocess.Popen([str(c) for c in cmd], cwd=cwd, stdin=stdin, env=env,
                             stdout=stdout if stdout is not None else subprocess.PIPE,
                             stderr=subprocess.PIPE, **popen_kwargs())
        self.procs.append(p)

        def handle(line: str):
            try:
                if on_line and on_line(line):
                    return          # 進捗行などは呑み込む
            except Exception:       # noqa: BLE001  進捗解析の失敗で出力を止めない
                pass
            if quiet_re and quiet_re.search(line):
                return
            self.log(line)

        threads = [threading.Thread(target=self._pump, args=(p.stderr, handle), daemon=True)]
        if stdout is None:
            threads.append(threading.Thread(target=self._pump, args=(p.stdout, handle), daemon=True))
        for t in threads:
            t.start()
        p._af_threads = threads  # type: ignore[attr-defined]
        return p

    def wait(self, *procs: subprocess.Popen) -> list[int]:
        while any(p.poll() is None for p in procs):
            if self.cancel.is_set():
                self.kill_all()
                raise Cancelled()
            time.sleep(0.2)
        for p in procs:
            for t in getattr(p, "_af_threads", []):
                t.join(timeout=5)
        return [p.returncode for p in procs]

    def run(self, cmd, **kw) -> None:
        p = self.start(cmd, **kw)
        rc, = self.wait(p)
        if rc != 0:
            raise JobError(f"{Path(str(cmd[0])).name} がエラー終了しました（code {rc}）")

    def kill_all(self):
        if self.on_cancel and any(p.poll() is None for p in self.procs):
            try:
                self.on_cancel()
            except Exception:  # noqa: BLE001
                pass
        for p in self.procs:
            if p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass


# ------------------------------------------------------------------ 補助

def ffmpeg_progress_parser(duration: float, on_frac: Callable[[float], None]):
    """-progress pipe:2 の out_time_us を拾う"""
    def f(line: str) -> bool:
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
                if duration > 0:
                    on_frac(min(us / 1e6 / duration, 1.0))
            except ValueError:
                pass
            return True
        return bool(re.match(r"^(frame|fps|stream_[\d_]+q|bitrate|total_size|out_time|dup_frames|drop_frames|speed|progress)=", line))
    return f


def ascii_work_root(preferred: Path, log: LogCB) -> Path:
    """一部の外部ツールは英数字以外を含むパスを開けないため、作業フォルダは ASCII パスに置く"""
    if str(preferred.resolve()).isascii():
        return preferred
    sys_tmp = Path(tempfile.gettempdir())
    anchor = Path(preferred.resolve().anchor)
    for cand in (anchor / "EncodeTemp", sys_tmp / "encode_tools"):
        if str(cand).isascii():
            try:
                cand.mkdir(parents=True, exist_ok=True)
                log(f"作業フォルダのパスに英数字以外が含まれるため {cand} を使います（一部のツールの制約）")
                return cand
            except OSError:
                continue
    raise JobError("英数字のみの作業フォルダを用意できません。環境設定で作業フォルダを指定してください")


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    i = 2
    while True:
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
        i += 1


