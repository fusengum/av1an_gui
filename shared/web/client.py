"""デスクトップ版から Web 画面のサーバーを使うためのクライアント。
キューと進捗はサーバーが 1 つだけ持ち、デスクトップ版も Web 画面も同じものを表示する。
サーバーが動いていなければ、このフォルダの Python で裏に起動する（デスクトップ版を閉じてもエンコードは続く）。"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from shared.core.config import APP_ROOT, IS_WIN

INFO = APP_ROOT / "logs" / "web.json"
PORTS = range(8765, 8800)
# localhost への接続がプロキシ設定（HTTP_PROXY など）に取られないようにする
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ServerError(Exception):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status          # HTTP の状態コード（つながらなかったときは 0）


def write_info(host: str, port: int) -> None:
    try:
        INFO.parent.mkdir(parents=True, exist_ok=True)
        INFO.write_text(json.dumps({"host": host, "port": port, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass


def read_info() -> dict:
    try:
        return json.loads(INFO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


class Client:
    def __init__(self):
        self.base = ""
        self._lock = threading.Lock()
        self._spawned = 0.0

    # ------------------------------------------------------------ 通信
    def _req(self, method: str, path: str, body=None, timeout: float = 5, base: str | None = None):
        url = (base or self.base) + path
        if not (base or self.base):
            raise ServerError("Web サーバーに接続していません")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("detail") or str(e)
            except Exception:  # noqa: BLE001
                msg = str(e)
            raise ServerError(str(msg), e.code) from None
        except (urllib.error.URLError, OSError, ValueError) as e:
            if base is None:
                self.base = ""
            raise ServerError(f"Web サーバーに接続できません（{e}）") from None

    def get(self, path: str, **kw):
        return self._req("GET", path, **kw)

    def post(self, path: str, body=None, **kw):
        return self._req("POST", path, body if body is not None else {}, **kw)

    def delete(self, path: str, **kw):
        return self._req("DELETE", path, **kw)

    # ------------------------------------------------------------ 接続
    def _ours(self, port: int) -> bool:
        """そのポートで動いているのが、このフォルダのサーバーか（別のツールの Web 画面と取り違えない）"""
        if not _open(port):
            return False
        base = f"http://127.0.0.1:{port}"
        try:
            m = self._req("GET", "/api/meta", timeout=3, base=base)
        except ServerError:
            return False
        try:
            same = Path(m.get("root", "")).resolve() == APP_ROOT
        except OSError:
            same = False
        if same:
            self.base = base
        return same

    def find(self) -> bool:
        if self.base:
            return True
        port = read_info().get("port")
        ports = [int(port)] if port else []
        ports += [p for p in PORTS if p not in ports]
        return any(self._ours(p) for p in ports)

    def ensure(self, wait: float = 20) -> None:
        """サーバーにつなぐ。動いていなければ起動する。つながらなければ ServerError"""
        with self._lock:
            if self.find():
                return
            if time.time() - self._spawned > 30:        # 起動に失敗しているときに何度も起動しない
                self._spawned = time.time()
                self._spawn()
            end = time.time() + wait
            while time.time() < end:
                time.sleep(0.5)
                if self.find():
                    return
        raise ServerError("Web サーバーを起動できませんでした（logs/web.log を確認してください）")

    def _spawn(self) -> None:
        from .config import WebConfig
        # 待ち受けは Web 画面の設定（settings_web.json）に従う。そのポートを別のツールが使っていれば空きを探す
        cfg = WebConfig.load()
        args = []
        if _open(cfg.port):
            args = ["--port", str(next((p for p in PORTS if not _open(p)), PORTS[0]))]
        exe = Path(sys.executable)
        if IS_WIN and exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").exists():
            exe = exe.with_name("pythonw.exe")             # コンソール画面を出さない
        logs = APP_ROOT / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        out = open(logs / "web.log", "a", encoding="utf-8")
        kw = {"creationflags": 0x00000008 | 0x00000200} if IS_WIN else {"start_new_session": True}
        try:
            subprocess.Popen([str(exe), "-m", "shared.web", *args], cwd=str(APP_ROOT),
                             stdin=subprocess.DEVNULL, stdout=out, stderr=out, close_fds=True, **kw)
        finally:
            out.close()

    # ------------------------------------------------------------ キュー
    def state(self) -> dict:
        return self.get("/api/state", timeout=5)

    def add(self, kind: str, path: Path, options: dict) -> str:
        r = self.post("/api/jobs", {"kind": kind, "paths": [str(path)], "options": options})
        return r["added"][0]

    def retry(self, jid: str, options: dict | None = None) -> None:
        self.post(f"/api/jobs/{jid}/retry", {"options": options})

    def remove(self, jid: str) -> None:
        self.delete(f"/api/jobs/{jid}")

    def set_options(self, jid: str, options: dict) -> None:
        self.post(f"/api/jobs/{jid}/options", {"options": options})

    def start_queue(self) -> None:
        self.post("/api/queue/start")

    def stop_queue(self) -> None:
        """停止: 処理中のものは待機に戻る（次に開始すると続きから）"""
        try:
            self.post("/api/queue/stop")
        except ServerError as e:
            if e.status != 404:
                raise
            self.post("/api/jobs/stop")          # 以前の版のサーバー

    stop_all = stop_queue

    def set_after(self, action: str) -> dict:
        return self.post("/api/after", {"action": action})

    def restart(self, wait: float = 25) -> None:
        """設定やコードを反映するためにサーバーを起動し直し、つながるまで待つ。
        起動し直す窓口が無い以前の版のサーバーは、プロセスを止めてから起動する"""
        old = self.get("/api/meta").get("instance")
        try:
            self.post("/api/server/restart")
        except ServerError as e:
            if e.status not in (404, 405):
                raise
            kill_servers()
            self.base = ""
            self.ensure(wait)
            return
        self.base = ""
        end = time.time() + wait
        while time.time() < end:
            time.sleep(1)
            try:
                if self.find() and self.get("/api/meta", timeout=3).get("instance") != old:
                    return
            except ServerError:
                pass
            self.base = ""
        raise ServerError("Web サーバーが起動し直しませんでした（logs/web.log を確認してください）")

    def log(self, jid: str, since: int) -> dict:
        return self.get(f"/api/jobs/{urllib.parse.quote(jid)}/log?since={since}")


# ------------------------------------------------------------------ 以前の版のサーバーを止める

def _same_dir(path: str) -> bool:
    try:
        return Path(path).resolve() == APP_ROOT
    except OSError:
        return False


def server_pids() -> list[int]:
    """このフォルダの Web サーバー（python -m shared.web）のプロセス。このプロセス自身は含めない"""
    pids = set()
    me = os.getpid()
    root = str(APP_ROOT).lower()
    if IS_WIN:
        ps = ("Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%shared.web%'\" | "
              "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                           timeout=30, creationflags=0x08000000)
        for line in r.stdout.splitlines():
            n, _, cmd = line.partition("\t")
            # venv の python とその子（uv で入れた本体）は、どちらもこのフォルダの中にある
            if n.strip().isdigit() and "-m shared.web" in cmd and root in cmd.lower():
                pids.add(int(n))
    else:
        for e in os.listdir("/proc"):
            if not e.isdigit():
                continue
            try:
                cmd = Path(f"/proc/{e}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                if "-m shared.web" in cmd and _same_dir(os.readlink(f"/proc/{e}/cwd")):
                    pids.add(int(e))
            except OSError:
                continue
    pids.discard(me)
    return sorted(pids)


def kill_servers() -> list[int]:
    pids = server_pids()
    for n in pids:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(n)], capture_output=True, creationflags=0x08000000)
            else:
                os.kill(n, 9)                     # systemd のサービスなら、異常終了として起動し直される
        except OSError:
            pass
    return pids
