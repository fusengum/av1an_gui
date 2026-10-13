"""外部コマンドの実行と、処理全般で使う補助"""
from __future__ import annotations

import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .config import IS_WIN
from .tools import popen_kwargs

LogCB = Callable[[str], None]
ProgCB = Callable[[str, float], None]      # (stage label, overall 0..1)


class Cancelled(Exception):
    pass


class JobError(Exception):
    pass



_SPLIT = re.compile(rb"[\r\n]+")


# ------------------------------------------------------------------ 子プロセスごと止める（Windows）
# Windows では親（av1an.exe など）を止めても、そこから起動されたエンコーダや ffmpeg は動き続ける。
# 起動したプロセスをジョブオブジェクトに入れておき、止めるときはジョブごと終わらせる。
# 子プロセスは自動でジョブに入る。このプロセス（サーバー）が落ちたときも一緒に止まる。

def _win_job(p: subprocess.Popen):
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000           # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)) and \
        k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(int(p._handle)))
    if not ok:
        k32.CloseHandle(wintypes.HANDLE(job))
        return None
    return job


def _win_job_kill(job) -> None:
    import ctypes
    from ctypes import wintypes
    ctypes.windll.kernel32.TerminateJobObject(wintypes.HANDLE(job), 1)


def _win_job_close(job) -> None:
    import ctypes
    from ctypes import wintypes
    ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(job))


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
        p._af_job = None  # type: ignore[attr-defined]
        if IS_WIN:
            try:
                p._af_job = _win_job(p)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001  使えなくても taskkill で止める
                pass

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
            self._close_job(p)          # 取り残された子プロセスがあれば、ここで一緒に終わる
        return [p.returncode for p in procs]

    @staticmethod
    def _close_job(p):
        job = getattr(p, "_af_job", None)
        if job:
            p._af_job = None
            _win_job_close(job)

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
            if IS_WIN:
                # 親が終わっていても子が残っていることがあるため、状態にかかわらずジョブごと止める
                job = getattr(p, "_af_job", None)
                if job:
                    _win_job_kill(job)
                elif p.poll() is None:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                                   **popen_kwargs())
            if p.poll() is None:
                try:
                    p.kill()
                except OSError:
                    pass
            self._close_job(p)


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
    raise JobError("英数字のみの作業フォルダを用意できません。設定の「一般」で作業フォルダを指定してください")


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    i = 2
    while True:
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
        i += 1


